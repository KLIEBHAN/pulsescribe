//! Single-slot prewarmed Deepgram WebSocket connection manager with background `KeepAlive`.
//!
//! Mirrors `DeepgramWarmConnectionManager` from `providers/deepgram_stream.py`:
//! Deepgram's `CloseStream` ends a session, so each prewarmed socket is claimed at most once.
//! While idle, a background task sends periodic `{"type":"KeepAlive"}` frames, flushes Pong
//! replies to server Pings, and immediately marks the slot closed if the remote drops it.

use std::fmt;
use std::future::Future;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use bytes::Bytes;
use futures_util::{SinkExt, StreamExt};
use tokio::sync::{mpsc, oneshot, Mutex};
use tokio::task::JoinHandle;
use tokio::time::MissedTickBehavior;
use tokio_tungstenite::tungstenite::{Error as WsError, Message};

use crate::deepgram_stream::{
    close_idle_websocket, normalized_keywords, parse_server_error_message, run_stream_session,
    DeepgramStreamClient, DeepgramStreamConfig, DeepgramStreamEvent, DeepgramStreamResult,
    DeepgramWsStream,
};
use crate::{normalize_auto_language, ProviderError};

const KEEPALIVE_MESSAGE: &str = r#"{"type":"KeepAlive"}"#;
const MIN_KEEPALIVE_TIMER: Duration = Duration::from_millis(10);

/// Normalized URL parameters that determine whether a prewarmed socket matches a request.
#[derive(Debug, Clone, PartialEq, Eq)]
struct DeepgramConnectionKey {
    model: String,
    language: Option<String>,
    sample_rate: u32,
    channels: u16,
    encoding: String,
    smart_format: bool,
    punctuate: bool,
    interim_results: bool,
    keywords: Vec<String>,
}

impl DeepgramConnectionKey {
    fn from_config(config: &DeepgramStreamConfig) -> Self {
        Self {
            model: config.model.trim().to_string(),
            language: normalize_auto_language(config.language.as_deref()),
            sample_rate: config.sample_rate,
            channels: config.channels,
            encoding: config.encoding.trim().to_string(),
            smart_format: config.smart_format,
            punctuate: config.punctuate,
            interim_results: config.interim_results,
            keywords: normalized_keywords(&config.keywords),
        }
    }
}

enum WarmCommand {
    Claim(oneshot::Sender<Option<DeepgramWsStream>>),
    Close(oneshot::Sender<()>),
}

struct ActiveWarmEntry {
    key: DeepgramConnectionKey,
    close_timeout: Duration,
    cmd_tx: mpsc::Sender<WarmCommand>,
    closed_flag: Arc<AtomicBool>,
    task: JoinHandle<()>,
}

struct WarmSlot {
    generation: u64,
    entry: Option<ActiveWarmEntry>,
}

/// Maintains at most one preconnected Deepgram WebSocket with background `KeepAlive` frames.
#[derive(Clone)]
pub struct DeepgramWarmConnectionManager {
    client: DeepgramStreamClient,
    slot: Arc<Mutex<WarmSlot>>,
}

impl fmt::Debug for DeepgramWarmConnectionManager {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("DeepgramWarmConnectionManager")
            .field("client", &self.client)
            .finish_non_exhaustive()
    }
}

impl DeepgramWarmConnectionManager {
    pub fn new(client: DeepgramStreamClient) -> Self {
        Self {
            client,
            slot: Arc::new(Mutex::new(WarmSlot {
                generation: 0,
                entry: None,
            })),
        }
    }

    /// Preconnect a WebSocket for `config` and start periodic `KeepAlive` frames.
    ///
    /// Reuses an existing healthy connection with matching session parameters; otherwise closes
    /// any stale entry and opens a fresh socket outside the internal state lock.
    pub async fn warm_up(&self, config: &DeepgramStreamConfig) -> Result<(), ProviderError> {
        config.validate()?;
        let key = DeepgramConnectionKey::from_config(config);

        let (my_gen, previous) = {
            let mut slot = self.slot.lock().await;
            if let Some(entry) = slot.entry.as_ref() {
                if entry.key == key && !entry.closed_flag.load(Ordering::Acquire) {
                    return Ok(());
                }
            }
            slot.generation = slot.generation.wrapping_add(1);
            (slot.generation, slot.entry.take())
        };

        if let Some(prev) = previous {
            close_warm_entry(prev).await;
        }

        let mut ws = self.client.connect(config).await?;
        let keepalive_interval = config.timeouts.keepalive_interval.max(MIN_KEEPALIVE_TIMER);
        let keepalive_send = config.timeouts.keepalive_send;
        let close_timeout = config.timeouts.close;

        let mut slot = self.slot.lock().await;
        if slot.generation != my_gen {
            drop(slot);
            close_idle_websocket(&mut ws, close_timeout).await;
            return Ok(());
        }

        let (cmd_tx, cmd_rx) = mpsc::channel(2);
        let closed_flag = Arc::new(AtomicBool::new(false));
        let task_closed_flag = Arc::clone(&closed_flag);

        let task = tokio::spawn(run_keepalive_loop(
            ws,
            keepalive_interval,
            keepalive_send,
            close_timeout,
            cmd_rx,
            task_closed_flag,
        ));

        slot.entry = Some(ActiveWarmEntry {
            key,
            close_timeout,
            cmd_tx,
            closed_flag,
            task,
        });
        tracing::info!(
            model = config.model.trim(),
            language = normalize_auto_language(config.language.as_deref())
                .as_deref()
                .unwrap_or("multi"),
            sample_rate = config.sample_rate,
            "Deepgram WebSocket prewarmed"
        );
        Ok(())
    }

    /// Returns `true` if a prewarmed socket is currently open and available to claim.
    pub async fn has_ready_connection(&self) -> bool {
        let slot = self.slot.lock().await;
        slot.entry
            .as_ref()
            .is_some_and(|entry| !entry.closed_flag.load(Ordering::Acquire))
    }

    /// Run a streaming transcription using the prewarmed socket when available and matching,
    /// transparently falling back to a fresh connection otherwise.
    pub async fn transcribe_stream(
        &self,
        config: &DeepgramStreamConfig,
        audio_rx: mpsc::Receiver<Bytes>,
        event_tx: Option<mpsc::Sender<DeepgramStreamEvent>>,
    ) -> Result<DeepgramStreamResult, ProviderError> {
        config.validate()?;
        let (ws, used_prewarmed) = match self.claim(config).await {
            Some(ws) => {
                tracing::info!("claimed prewarmed Deepgram WebSocket");
                (ws, true)
            }
            None => (self.client.connect(config).await?, false),
        };
        run_stream_session(ws, config, audio_rx, event_tx, used_prewarmed).await
    }

    /// Discard and close any prewarmed socket (and cancel pending warm-ups) within `close` timeout.
    pub async fn invalidate(&self) {
        let previous = {
            let mut slot = self.slot.lock().await;
            slot.generation = slot.generation.wrapping_add(1);
            slot.entry.take()
        };
        if let Some(entry) = previous {
            close_warm_entry(entry).await;
        }
    }

    async fn claim(&self, config: &DeepgramStreamConfig) -> Option<DeepgramWsStream> {
        let key = DeepgramConnectionKey::from_config(config);
        let entry = {
            let mut slot = self.slot.lock().await;
            slot.entry.take()
        }?;

        if entry.key != key || entry.closed_flag.load(Ordering::Acquire) {
            close_warm_entry(entry).await;
            return None;
        }

        let (reply_tx, reply_rx) = oneshot::channel();
        if entry
            .cmd_tx
            .send(WarmCommand::Claim(reply_tx))
            .await
            .is_err()
        {
            return None;
        }
        reply_rx.await.ok().flatten()
    }
}

async fn close_warm_entry(entry: ActiveWarmEntry) {
    let (done_tx, done_rx) = oneshot::channel();
    let wait_limit = entry.close_timeout + Duration::from_millis(100);
    if entry.cmd_tx.send(WarmCommand::Close(done_tx)).await.is_ok()
        && tokio::time::timeout(wait_limit, done_rx).await.is_err()
    {
        entry.task.abort();
    }
}

enum MaintenanceOutcome {
    Completed,
    Failed,
    Interrupted(Option<WarmCommand>),
}

async fn run_maintenance_io<F>(
    io: F,
    timeout: Duration,
    cmd_rx: &mut mpsc::Receiver<WarmCommand>,
) -> MaintenanceOutcome
where
    F: Future<Output = Result<(), WsError>>,
{
    tokio::select! {
        biased;
        cmd = cmd_rx.recv() => MaintenanceOutcome::Interrupted(cmd),
        res = tokio::time::timeout(timeout, io) => match res {
            Ok(Ok(())) => MaintenanceOutcome::Completed,
            Ok(Err(err)) => {
                tracing::debug!(error = %err, "Deepgram warm maintenance I/O failed");
                MaintenanceOutcome::Failed
            }
            Err(_) => {
                tracing::debug!(
                    timeout_ms = timeout.as_millis(),
                    "Deepgram warm maintenance I/O timed out"
                );
                MaintenanceOutcome::Failed
            }
        }
    }
}

fn finish_interrupted_command(cmd: Option<WarmCommand>) {
    match cmd {
        Some(WarmCommand::Claim(reply)) => {
            // An interrupted sink write leaves the socket unsafe to reuse; fall back to fresh.
            let _ = reply.send(None);
        }
        Some(WarmCommand::Close(done)) => {
            let _ = done.send(());
        }
        None => {}
    }
}

async fn run_keepalive_loop(
    mut ws: DeepgramWsStream,
    keepalive_interval: Duration,
    keepalive_send: Duration,
    close_timeout: Duration,
    mut cmd_rx: mpsc::Receiver<WarmCommand>,
    closed_flag: Arc<AtomicBool>,
) {
    let mut ticker = tokio::time::interval(keepalive_interval);
    ticker.set_missed_tick_behavior(MissedTickBehavior::Delay);
    // Consume the immediate first tick so KeepAlive fires after one full interval.
    ticker.tick().await;

    loop {
        tokio::select! {
            biased;
            cmd = cmd_rx.recv() => {
                closed_flag.store(true, Ordering::Release);
                match cmd {
                    Some(WarmCommand::Claim(reply)) => {
                        if let Err(Some(mut unclaimed_ws)) = reply.send(Some(ws)) {
                            close_idle_websocket(&mut unclaimed_ws, close_timeout).await;
                        }
                    }
                    Some(WarmCommand::Close(done)) => {
                        close_idle_websocket(&mut ws, close_timeout).await;
                        let _ = done.send(());
                    }
                    None => close_idle_websocket(&mut ws, close_timeout).await,
                }
                return;
            }
            _ = ticker.tick() => {
                match run_maintenance_io(
                    ws.send(Message::text(KEEPALIVE_MESSAGE)),
                    keepalive_send,
                    &mut cmd_rx,
                )
                .await
                {
                    MaintenanceOutcome::Completed => tracing::trace!("sent Deepgram warm KeepAlive"),
                    MaintenanceOutcome::Failed => {
                        closed_flag.store(true, Ordering::Release);
                        return;
                    }
                    MaintenanceOutcome::Interrupted(cmd) => {
                        closed_flag.store(true, Ordering::Release);
                        finish_interrupted_command(cmd);
                        return;
                    }
                }
            }
            maybe_msg = ws.next() => {
                match maybe_msg {
                    Some(Ok(Message::Ping(_))) => {
                        match run_maintenance_io(ws.flush(), keepalive_send, &mut cmd_rx).await {
                            MaintenanceOutcome::Completed => {}
                            MaintenanceOutcome::Failed => {
                                closed_flag.store(true, Ordering::Release);
                                return;
                            }
                            MaintenanceOutcome::Interrupted(cmd) => {
                                closed_flag.store(true, Ordering::Release);
                                finish_interrupted_command(cmd);
                                return;
                            }
                        }
                    }
                    Some(Ok(Message::Text(text))) => {
                        if let Some(detail) = parse_server_error_message(&text) {
                            tracing::debug!(detail, "Deepgram warm socket received server error");
                            closed_flag.store(true, Ordering::Release);
                            close_idle_websocket(&mut ws, close_timeout).await;
                            return;
                        }
                    }
                    Some(Ok(Message::Binary(_) | Message::Pong(_) | Message::Frame(_))) => {}
                    Some(Ok(Message::Close(_))) | Some(Err(_)) | None => {
                        tracing::debug!("Deepgram warm socket closed while idle");
                        closed_flag.store(true, Ordering::Release);
                        return;
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn connection_key_normalizes_auto_language_and_whitespace() {
        let a = DeepgramStreamConfig::default()
            .with_model(" nova-3 ")
            .with_language("auto")
            .with_keywords(vec![" PulseScribe ".to_string(), "  ".to_string()]);
        let b = DeepgramStreamConfig::default()
            .with_model("nova-3")
            .with_keywords(vec!["PulseScribe".to_string()]);
        assert_eq!(
            DeepgramConnectionKey::from_config(&a),
            DeepgramConnectionKey::from_config(&b)
        );
    }

    #[test]
    fn warm_manager_debug_redacts_underlying_client_credentials() {
        let client = DeepgramStreamClient::with_ws_url(
            "wss://user:pw@proxy.example/v1/listen?token=secret",
            "dg-warm-secret",
        )
        .unwrap();
        let manager = DeepgramWarmConnectionManager::new(client);
        let debug = format!("{manager:?}");
        assert!(!debug.contains("dg-warm-secret"));
        assert!(!debug.contains("pw"));
        assert!(!debug.contains("token=secret"));
        assert!(debug.contains("[REDACTED]"));
    }
}
