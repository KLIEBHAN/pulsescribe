//! Deepgram live WebSocket streaming provider (`tokio-tungstenite`) implementing ADR-001.
//!
//! Connects directly to Deepgram's `/v1/listen` WebSocket endpoint without coupling to
//! audio hardware, sound playback, or UI files. Callers stream raw PCM chunks over a
//! `tokio::sync::mpsc::Receiver<Bytes>` and optionally observe interim/final events.
//!
//! Graceful shutdown follows `docs/adr/001-deepgram-streaming-shutdown.md`:
//! 1. Drain PCM chunks and append optional linear16 tail silence padding.
//! 2. Send `{"type":"Finalize"}` with a bounded send timeout.
//! 3. Wait for a `from_finalize: true` acknowledgement (plus optional short grace window
//!    on empty acknowledgements, exiting early when a late final transcript arrives).
//! 4. Send `{"type":"CloseStream"}` only if the connection is still usable.
//! 5. Perform the WebSocket close handshake within a strict `close` timeout (`0.5s` default).

use std::fmt;
use std::time::Duration;

use bytes::Bytes;
use futures_util::{SinkExt, StreamExt};
use pulsescribe_core::vocabulary::DEEPGRAM_KEYWORD_LIMIT;
use serde::Deserialize;
use tokio::net::TcpStream;
use tokio::sync::mpsc;
use tokio::time::Instant;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::http::header::{HeaderValue, AUTHORIZATION};
use tokio_tungstenite::tungstenite::{Error as WsError, Message};
use tokio_tungstenite::{connect_async_with_config, MaybeTlsStream, WebSocketStream};
use url::Url;

use crate::deepgram::DEEPGRAM_API_KEY_ENV_VAR;
use crate::http::{read_env_non_empty, redact_url_for_debug, validate_header_secret};
use crate::{normalize_auto_language, ProviderError, DEFAULT_DEEPGRAM_MODEL};

pub const DEEPGRAM_WS_URL: &str = "wss://api.deepgram.com/v1/listen";
pub const DEFAULT_STREAM_SAMPLE_RATE: u32 = 16_000;
pub const DEFAULT_STREAM_CHANNELS: u16 = 1;
pub const DEFAULT_STREAM_ENCODING: &str = "linear16";

pub const MIN_STREAM_SAMPLE_RATE: u32 = 8_000;
pub const MAX_STREAM_SAMPLE_RATE: u32 = 48_000;
pub const MAX_STREAM_CHANNELS: u16 = 8;

pub const DEFAULT_CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
pub const DEFAULT_SEND_MEDIA_TIMEOUT: Duration = Duration::from_secs(10);
pub const DEFAULT_FINALIZE_TIMEOUT: Duration = Duration::from_secs(5);
pub const DEFAULT_EMPTY_FINALIZE_GRACE: Duration = Duration::from_millis(250);
pub const DEFAULT_TAIL_PADDING: Duration = Duration::from_millis(250);
pub const DEFAULT_FINALIZE_SEND_TIMEOUT: Duration = Duration::from_secs(1);
pub const DEFAULT_CLOSE_STREAM_SEND_TIMEOUT: Duration = Duration::from_millis(500);
pub const DEFAULT_CLOSE_TIMEOUT: Duration = Duration::from_millis(500);
pub const DEFAULT_KEEPALIVE_INTERVAL: Duration = Duration::from_secs(3);
pub const DEFAULT_KEEPALIVE_SEND_TIMEOUT: Duration = Duration::from_secs(1);

const MAX_TAIL_PADDING: Duration = Duration::from_secs(5);

const FINALIZE_MESSAGE: &str = r#"{"type":"Finalize"}"#;
const CLOSE_STREAM_MESSAGE: &str = r#"{"type":"CloseStream"}"#;

pub(crate) type DeepgramWsStream = WebSocketStream<MaybeTlsStream<TcpStream>>;

/// Timing bounds for Deepgram streaming, ADR-001 shutdown, and warm KeepAlive maintenance.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DeepgramStreamTimeouts {
    pub connect: Duration,
    pub send_media: Duration,
    pub finalize: Duration,
    pub empty_finalize_grace: Duration,
    pub tail_padding: Duration,
    pub finalize_send: Duration,
    pub close_stream_send: Duration,
    pub close: Duration,
    pub keepalive_interval: Duration,
    pub keepalive_send: Duration,
}

impl Default for DeepgramStreamTimeouts {
    fn default() -> Self {
        Self {
            connect: DEFAULT_CONNECT_TIMEOUT,
            send_media: DEFAULT_SEND_MEDIA_TIMEOUT,
            finalize: DEFAULT_FINALIZE_TIMEOUT,
            empty_finalize_grace: DEFAULT_EMPTY_FINALIZE_GRACE,
            tail_padding: DEFAULT_TAIL_PADDING,
            finalize_send: DEFAULT_FINALIZE_SEND_TIMEOUT,
            close_stream_send: DEFAULT_CLOSE_STREAM_SEND_TIMEOUT,
            close: DEFAULT_CLOSE_TIMEOUT,
            keepalive_interval: DEFAULT_KEEPALIVE_INTERVAL,
            keepalive_send: DEFAULT_KEEPALIVE_SEND_TIMEOUT,
        }
    }
}

impl DeepgramStreamTimeouts {
    /// Load Deepgram timeout overrides from `PULSESCRIBE_DEEPGRAM_*` environment variables,
    /// mirroring `config.py`'s bounds and defaults.
    pub fn from_env() -> Self {
        Self {
            close: read_bounded_duration_env(
                "PULSESCRIBE_DEEPGRAM_CLOSE_TIMEOUT",
                DEFAULT_CLOSE_TIMEOUT,
                Duration::ZERO,
                Duration::from_secs(10),
            ),
            tail_padding: read_bounded_duration_env(
                "PULSESCRIBE_DEEPGRAM_TAIL_PADDING_SECONDS",
                DEFAULT_TAIL_PADDING,
                Duration::ZERO,
                MAX_TAIL_PADDING,
            ),
            empty_finalize_grace: read_bounded_duration_env(
                "PULSESCRIBE_DEEPGRAM_EMPTY_FINALIZE_GRACE_SECONDS",
                DEFAULT_EMPTY_FINALIZE_GRACE,
                Duration::ZERO,
                Duration::from_secs(5),
            ),
            keepalive_interval: read_bounded_duration_env(
                "PULSESCRIBE_DEEPGRAM_KEEPALIVE_INTERVAL_SECONDS",
                DEFAULT_KEEPALIVE_INTERVAL,
                Duration::from_secs(1),
                Duration::from_secs(8),
            ),
            finalize_send: read_bounded_duration_env(
                "PULSESCRIBE_DEEPGRAM_FINALIZE_SEND_TIMEOUT",
                DEFAULT_FINALIZE_SEND_TIMEOUT,
                Duration::from_millis(50),
                Duration::from_secs(10),
            ),
            close_stream_send: read_bounded_duration_env(
                "PULSESCRIBE_DEEPGRAM_CLOSE_STREAM_SEND_TIMEOUT",
                DEFAULT_CLOSE_STREAM_SEND_TIMEOUT,
                Duration::from_millis(50),
                Duration::from_secs(10),
            ),
            keepalive_send: read_bounded_duration_env(
                "PULSESCRIBE_DEEPGRAM_KEEPALIVE_SEND_TIMEOUT",
                DEFAULT_KEEPALIVE_SEND_TIMEOUT,
                Duration::from_millis(50),
                Duration::from_secs(10),
            ),
            ..Self::default()
        }
    }
}

/// Parameters controlling a Deepgram live WebSocket transcription session.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DeepgramStreamConfig {
    pub model: String,
    pub language: Option<String>,
    pub sample_rate: u32,
    pub channels: u16,
    pub encoding: String,
    pub smart_format: bool,
    pub punctuate: bool,
    pub interim_results: bool,
    pub keywords: Vec<String>,
    pub timeouts: DeepgramStreamTimeouts,
}

impl Default for DeepgramStreamConfig {
    fn default() -> Self {
        Self {
            model: DEFAULT_DEEPGRAM_MODEL.to_string(),
            language: None,
            sample_rate: DEFAULT_STREAM_SAMPLE_RATE,
            channels: DEFAULT_STREAM_CHANNELS,
            encoding: DEFAULT_STREAM_ENCODING.to_string(),
            smart_format: true,
            punctuate: true,
            interim_results: true,
            keywords: Vec::new(),
            timeouts: DeepgramStreamTimeouts::default(),
        }
    }
}

impl DeepgramStreamConfig {
    pub fn with_model(mut self, model: impl Into<String>) -> Self {
        self.model = model.into();
        self
    }

    pub fn with_language(mut self, language: impl Into<String>) -> Self {
        self.language = Some(language.into());
        self
    }

    pub fn with_sample_rate(mut self, sample_rate: u32) -> Self {
        self.sample_rate = sample_rate;
        self
    }

    pub fn with_channels(mut self, channels: u16) -> Self {
        self.channels = channels;
        self
    }

    pub fn with_keywords(mut self, keywords: Vec<String>) -> Self {
        self.keywords = keywords;
        self
    }

    pub fn with_timeouts(mut self, timeouts: DeepgramStreamTimeouts) -> Self {
        self.timeouts = timeouts;
        self
    }

    /// Validate model, encoding, sample rate, and channel bounds before opening a socket.
    pub fn validate(&self) -> Result<(), ProviderError> {
        if self.model.trim().is_empty() {
            return Err(ProviderError::InvalidInput(
                "Deepgram stream model must not be empty".to_string(),
            ));
        }
        if self.encoding.trim().is_empty() {
            return Err(ProviderError::InvalidInput(
                "Deepgram stream encoding must not be empty".to_string(),
            ));
        }
        if !(MIN_STREAM_SAMPLE_RATE..=MAX_STREAM_SAMPLE_RATE).contains(&self.sample_rate) {
            return Err(ProviderError::InvalidInput(format!(
                "Deepgram stream sample_rate {} out of range ({}..={})",
                self.sample_rate, MIN_STREAM_SAMPLE_RATE, MAX_STREAM_SAMPLE_RATE
            )));
        }
        if self.channels == 0 || self.channels > MAX_STREAM_CHANNELS {
            return Err(ProviderError::InvalidInput(format!(
                "Deepgram stream channels {} out of range (1..={})",
                self.channels, MAX_STREAM_CHANNELS
            )));
        }
        Ok(())
    }
}

/// Live transcript updates emitted during a streaming session.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DeepgramStreamEvent {
    Interim(String),
    Final { text: String, from_finalize: bool },
}

/// Final outcome and telemetry of a Deepgram WebSocket streaming session.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DeepgramStreamResult {
    pub transcript: String,
    pub used_prewarmed_connection: bool,
    pub used_interim_fallback: bool,
    pub finalize_acknowledged: bool,
    pub bytes_sent: u64,
}

/// Build the Deepgram `/v1/listen` WebSocket URL with streaming query parameters.
///
/// Deepgram live streaming does not support `detect_language`, so unset or `"auto"` languages
/// map to `language=multi` (matching `providers/deepgram_stream.py`). Optional vocabulary terms
/// use `keyterm` for `nova-3*` models and `keywords` otherwise, capped at `DEEPGRAM_KEYWORD_LIMIT`.
pub fn build_stream_ws_url(
    ws_base_url: &str,
    config: &DeepgramStreamConfig,
) -> Result<Url, ProviderError> {
    config.validate()?;
    let mut url = parse_ws_base_url(ws_base_url)?;
    let model = config.model.trim();
    let encoding = config.encoding.trim();
    let normalized_lang = normalize_auto_language(config.language.as_deref());
    let vocab_param = if model.to_ascii_lowercase().starts_with("nova-3") {
        "keyterm"
    } else {
        "keywords"
    };

    {
        let mut query = url.query_pairs_mut();
        query.append_pair("model", model);
        query.append_pair("language", normalized_lang.as_deref().unwrap_or("multi"));
        query.append_pair("smart_format", bool_str(config.smart_format));
        query.append_pair("punctuate", bool_str(config.punctuate));
        query.append_pair("interim_results", bool_str(config.interim_results));
        query.append_pair("encoding", encoding);
        query.append_pair("sample_rate", &config.sample_rate.to_string());
        query.append_pair("channels", &config.channels.to_string());
        for keyword in normalized_keywords(&config.keywords) {
            query.append_pair(vocab_param, &keyword);
        }
    }

    Ok(url)
}

/// Direct Deepgram live WebSocket streaming client.
#[derive(Clone)]
pub struct DeepgramStreamClient {
    ws_url: String,
    api_key: String,
}

impl fmt::Debug for DeepgramStreamClient {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("DeepgramStreamClient")
            .field("ws_url", &redact_url_for_debug(&self.ws_url))
            .field("api_key", &"[REDACTED]")
            .finish_non_exhaustive()
    }
}

impl DeepgramStreamClient {
    pub const NAME: &'static str = "deepgram";

    /// Create a Deepgram streaming client targeting `wss://api.deepgram.com/v1/listen`.
    pub fn new(api_key: impl Into<String>) -> Result<Self, ProviderError> {
        Self::with_ws_url(DEEPGRAM_WS_URL, api_key)
    }

    /// Create a Deepgram streaming client with a custom `ws://` or `wss://` endpoint URL.
    pub fn with_ws_url(
        ws_url: impl Into<String>,
        api_key: impl Into<String>,
    ) -> Result<Self, ProviderError> {
        let ws_url = ws_url.into().trim().to_string();
        let api_key = api_key.into().trim().to_string();
        if api_key.is_empty() {
            return Err(ProviderError::MissingConfig(
                "DEEPGRAM_API_KEY nicht gesetzt. Registrierung unter https://console.deepgram.com (200$ Startguthaben)"
                    .to_string(),
            ));
        }
        validate_header_secret(DEEPGRAM_API_KEY_ENV_VAR, &api_key)?;
        let _ = parse_ws_base_url(&ws_url)?;
        Ok(Self { ws_url, api_key })
    }

    /// Create a Deepgram streaming client reading `DEEPGRAM_API_KEY` from the environment.
    pub fn from_env() -> Result<Self, ProviderError> {
        let api_key = read_env_non_empty(DEEPGRAM_API_KEY_ENV_VAR).ok_or_else(|| {
            ProviderError::MissingConfig(
                "DEEPGRAM_API_KEY nicht gesetzt. Registrierung unter https://console.deepgram.com (200$ Startguthaben)"
                    .to_string(),
            )
        })?;
        Self::new(api_key)
    }

    /// Stream PCM audio chunks from `audio_rx` to Deepgram until `audio_rx` closes,
    /// then run the ADR-001 graceful shutdown sequence and return the assembled transcript.
    pub async fn transcribe_stream(
        &self,
        config: &DeepgramStreamConfig,
        audio_rx: mpsc::Receiver<Bytes>,
        event_tx: Option<mpsc::Sender<DeepgramStreamEvent>>,
    ) -> Result<DeepgramStreamResult, ProviderError> {
        let ws = self.connect(config).await?;
        run_stream_session(ws, config, audio_rx, event_tx, false).await
    }

    pub(crate) async fn connect(
        &self,
        config: &DeepgramStreamConfig,
    ) -> Result<DeepgramWsStream, ProviderError> {
        let url = build_stream_ws_url(&self.ws_url, config)?;
        let mut request = url.as_str().into_client_request().map_err(|err| {
            ProviderError::InvalidInput(format!("failed to build WebSocket request: {err}"))
        })?;
        let auth_value =
            HeaderValue::from_str(&format!("Token {}", self.api_key)).map_err(|err| {
                ProviderError::InvalidInput(format!("invalid Deepgram authorization header: {err}"))
            })?;
        request.headers_mut().insert(AUTHORIZATION, auth_value);

        // Disable Nagle's algorithm so small live PCM frames leave immediately.
        let (ws, _response) = tokio::time::timeout(
            config.timeouts.connect,
            connect_async_with_config(request, None, true),
        )
        .await
        .map_err(|_| ProviderError::WebSocket {
            provider: Self::NAME,
            detail: format!(
                "WebSocket connect timed out after {:?}",
                config.timeouts.connect
            ),
        })?
        .map_err(|err| ProviderError::WebSocket {
            provider: Self::NAME,
            detail: err.to_string(),
        })?;

        Ok(ws)
    }
}

#[derive(Default)]
struct SessionState {
    final_transcripts: Vec<String>,
    last_interim_text: String,
    in_finalize_phase: bool,
    finalize_ack_received: bool,
    finalize_transcript_received: bool,
    late_final_after_finalize: bool,
    socket_closed: bool,
    server_error: Option<ProviderError>,
    transport_error: Option<ProviderError>,
}

impl SessionState {
    fn has_any_transcript(&self) -> bool {
        !self.final_transcripts.is_empty() || !self.last_interim_text.is_empty()
    }
}

enum SendOutcome {
    Sent,
    Failed(WsError),
    TimedOut,
}

async fn send_with_timeout(
    ws: &mut DeepgramWsStream,
    msg: Message,
    timeout: Duration,
) -> SendOutcome {
    match tokio::time::timeout(timeout, ws.send(msg)).await {
        Ok(Ok(())) => SendOutcome::Sent,
        Ok(Err(err)) => SendOutcome::Failed(err),
        Err(_) => SendOutcome::TimedOut,
    }
}

pub(crate) async fn run_stream_session(
    mut ws: DeepgramWsStream,
    config: &DeepgramStreamConfig,
    mut audio_rx: mpsc::Receiver<Bytes>,
    event_tx: Option<mpsc::Sender<DeepgramStreamEvent>>,
    used_prewarmed_connection: bool,
) -> Result<DeepgramStreamResult, ProviderError> {
    let mut state = SessionState::default();
    let mut bytes_sent: u64 = 0;
    let mut connection_usable = true;
    let mut audio_open = true;

    // 1. Stream live PCM chunks while concurrently processing Deepgram transcript frames.
    while audio_open && connection_usable && !state.socket_closed {
        tokio::select! {
            maybe_chunk = audio_rx.recv() => {
                match maybe_chunk {
                    Some(chunk) if !chunk.is_empty() => {
                        let len = chunk.len() as u64;
                        match send_with_timeout(&mut ws, Message::Binary(chunk), config.timeouts.send_media).await {
                            SendOutcome::Sent => bytes_sent = bytes_sent.saturating_add(len),
                            SendOutcome::Failed(err) => {
                                connection_usable = false;
                                record_transport_error(&mut state, err);
                            }
                            SendOutcome::TimedOut => {
                                connection_usable = false;
                                state.transport_error = Some(ProviderError::WebSocket {
                                    provider: DeepgramStreamClient::NAME,
                                    detail: format!("timed out sending PCM chunk after {:?}", config.timeouts.send_media),
                                });
                            }
                        }
                    }
                    Some(_) => {}
                    None => audio_open = false,
                }
            }
            maybe_msg = ws.next() => {
                handle_incoming_message(&mut ws, maybe_msg, &mut state, event_tx.as_ref(), config.timeouts.send_media).await;
            }
        }
    }

    // 2. Append linear16 tail silence padding so Deepgram does not clip trailing phonemes.
    if connection_usable && !state.socket_closed {
        if let Some(padding) = build_tail_padding(config) {
            let len = padding.len() as u64;
            match send_with_timeout(
                &mut ws,
                Message::Binary(padding),
                config.timeouts.send_media,
            )
            .await
            {
                SendOutcome::Sent => bytes_sent = bytes_sent.saturating_add(len),
                SendOutcome::Failed(err) => {
                    connection_usable = false;
                    record_transport_error(&mut state, err);
                }
                SendOutcome::TimedOut => {
                    connection_usable = false;
                    tracing::warn!(
                        timeout_ms = config.timeouts.send_media.as_millis(),
                        "Deepgram tail-padding send timed out"
                    );
                }
            }
        }
    }

    // 3. Send Finalize control message (ADR-001).
    let mut finalize_sent = false;
    if connection_usable && !state.socket_closed {
        state.in_finalize_phase = true;
        state.late_final_after_finalize = false;

        match send_with_timeout(
            &mut ws,
            Message::text(FINALIZE_MESSAGE),
            config.timeouts.finalize_send,
        )
        .await
        {
            SendOutcome::Sent => finalize_sent = true,
            SendOutcome::Failed(err) => {
                connection_usable = false;
                tracing::warn!(error = %err, "Deepgram Finalize send failed");
                record_transport_error(&mut state, err);
            }
            SendOutcome::TimedOut => {
                // Dropping an in-progress sink write leaves the socket unsafe for further writes.
                connection_usable = false;
                tracing::warn!(
                    timeout_ms = config.timeouts.finalize_send.as_millis(),
                    "Deepgram Finalize send timed out; skipping CloseStream"
                );
            }
        }
    }

    // 4. Wait for from_finalize acknowledgement (and optional short grace on empty ack).
    if finalize_sent && !state.socket_closed {
        let acked = drain_until(
            &mut ws,
            &mut state,
            event_tx.as_ref(),
            Instant::now() + config.timeouts.finalize,
            config.timeouts.close_stream_send,
            |s| s.finalize_ack_received,
        )
        .await;
        if !acked && !state.socket_closed {
            tracing::warn!(
                timeout_ms = config.timeouts.finalize.as_millis(),
                "Deepgram Finalize wait timed out"
            );
        }

        if state.finalize_ack_received
            && !state.finalize_transcript_received
            && !state.socket_closed
            && !config.timeouts.empty_finalize_grace.is_zero()
        {
            let _ = drain_until(
                &mut ws,
                &mut state,
                event_tx.as_ref(),
                Instant::now() + config.timeouts.empty_finalize_grace,
                config.timeouts.close_stream_send,
                |s| s.late_final_after_finalize,
            )
            .await;
        }
    }

    // 5. Send CloseStream and bound the WebSocket close handshake (ADR-001).
    if connection_usable && !state.socket_closed {
        match send_with_timeout(
            &mut ws,
            Message::text(CLOSE_STREAM_MESSAGE),
            config.timeouts.close_stream_send,
        )
        .await
        {
            SendOutcome::Sent => close_websocket(&mut ws, config.timeouts.close).await,
            SendOutcome::Failed(err) => {
                tracing::debug!(error = %err, "Deepgram CloseStream send failed");
            }
            SendOutcome::TimedOut => {
                tracing::warn!(
                    timeout_ms = config.timeouts.close_stream_send.as_millis(),
                    "Deepgram CloseStream send timed out"
                );
            }
        }
    }

    // 6. Resolve final transcript or surface errors.
    if let Some(err) = state.server_error {
        return Err(err);
    }

    let (transcript, used_interim_fallback) = if !state.final_transcripts.is_empty() {
        (state.final_transcripts.join(" "), false)
    } else if !state.last_interim_text.is_empty() {
        tracing::debug!("using Deepgram interim transcript fallback");
        (state.last_interim_text, true)
    } else if let Some(err) = state.transport_error {
        return Err(err);
    } else {
        (String::new(), false)
    };

    Ok(DeepgramStreamResult {
        transcript,
        used_prewarmed_connection,
        used_interim_fallback,
        finalize_acknowledged: state.finalize_ack_received,
        bytes_sent,
    })
}

async fn drain_until(
    ws: &mut DeepgramWsStream,
    state: &mut SessionState,
    event_tx: Option<&mpsc::Sender<DeepgramStreamEvent>>,
    deadline: Instant,
    flush_timeout: Duration,
    done: impl Fn(&SessionState) -> bool,
) -> bool {
    while !done(state) && !state.socket_closed {
        match tokio::time::timeout_at(deadline, ws.next()).await {
            Ok(maybe_msg) => {
                handle_incoming_message(ws, maybe_msg, state, event_tx, flush_timeout).await
            }
            Err(_) => return false,
        }
    }
    done(state)
}

async fn handle_incoming_message(
    ws: &mut DeepgramWsStream,
    maybe_msg: Option<Result<Message, WsError>>,
    state: &mut SessionState,
    event_tx: Option<&mpsc::Sender<DeepgramStreamEvent>>,
    flush_timeout: Duration,
) {
    match maybe_msg {
        Some(Ok(Message::Text(text))) => process_deepgram_text_frame(&text, state, event_tx),
        Some(Ok(Message::Ping(_))) => {
            let _ = tokio::time::timeout(flush_timeout, ws.flush()).await;
        }
        Some(Ok(Message::Binary(_) | Message::Pong(_) | Message::Frame(_))) => {}
        Some(Ok(Message::Close(_))) | None => state.socket_closed = true,
        Some(Err(err)) => record_transport_error(state, err),
    }
}

fn record_transport_error(state: &mut SessionState, err: WsError) {
    state.socket_closed = true;
    if matches!(err, WsError::ConnectionClosed | WsError::AlreadyClosed) {
        return;
    }
    if state.has_any_transcript() {
        tracing::warn!(
            error = %err,
            "Deepgram WebSocket disconnected after receiving transcript; preserving collected text"
        );
    }
    if state.transport_error.is_none() {
        state.transport_error = Some(ProviderError::WebSocket {
            provider: DeepgramStreamClient::NAME,
            detail: err.to_string(),
        });
    }
}

/// Close the WebSocket within `close_timeout` (ADR-001) so unresponsive peers never hang shutdown.
pub(crate) async fn close_websocket(ws: &mut DeepgramWsStream, close_timeout: Duration) {
    if close_timeout.is_zero() {
        return;
    }
    let deadline = Instant::now() + close_timeout;
    if !matches!(
        tokio::time::timeout_at(deadline, ws.close(None)).await,
        Ok(Ok(()))
    ) {
        return;
    }
    loop {
        match tokio::time::timeout_at(deadline, ws.next()).await {
            Ok(Some(Ok(Message::Close(_)))) | Ok(Some(Err(_))) | Ok(None) | Err(_) => break,
            Ok(Some(Ok(_))) => {}
        }
    }
}

#[derive(Debug, Deserialize)]
struct DeepgramStreamFrame {
    #[serde(rename = "type")]
    frame_type: Option<String>,
    #[serde(default)]
    is_final: bool,
    #[serde(default)]
    from_finalize: bool,
    channel: Option<DeepgramStreamChannel>,
    err_msg: Option<String>,
    description: Option<String>,
    message: Option<String>,
    err_code: Option<String>,
}

#[derive(Debug, Deserialize)]
struct DeepgramStreamChannel {
    #[serde(default)]
    alternatives: Vec<DeepgramStreamAlternative>,
}

#[derive(Debug, Deserialize)]
struct DeepgramStreamAlternative {
    transcript: Option<String>,
}

fn process_deepgram_text_frame(
    raw: &str,
    state: &mut SessionState,
    event_tx: Option<&mpsc::Sender<DeepgramStreamEvent>>,
) {
    let parsed: DeepgramStreamFrame = match serde_json::from_str(raw) {
        Ok(frame) => frame,
        Err(err) => {
            tracing::warn!(error = %err, "ignoring unparseable Deepgram WebSocket JSON frame");
            return;
        }
    };

    if let Some(detail) = extract_error_detail(&parsed) {
        state.server_error = Some(ProviderError::WebSocket {
            provider: DeepgramStreamClient::NAME,
            detail,
        });
        state.socket_closed = true;
        return;
    }

    if let Some(kind) = parsed.frame_type.as_deref() {
        if !kind.eq_ignore_ascii_case("Results") {
            return;
        }
    }

    let transcript = parsed
        .channel
        .and_then(|c| c.alternatives.into_iter().next())
        .and_then(|a| a.transcript)
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty());

    if let Some(text) = transcript {
        if parsed.is_final {
            state.final_transcripts.push(text.clone());
            if state.in_finalize_phase {
                state.late_final_after_finalize = true;
            }
            if parsed.from_finalize {
                state.finalize_transcript_received = true;
                state.finalize_ack_received = true;
            }
            emit_event(
                event_tx,
                DeepgramStreamEvent::Final {
                    text,
                    from_finalize: parsed.from_finalize,
                },
            );
        } else {
            state.last_interim_text = text.clone();
            if parsed.from_finalize {
                state.finalize_ack_received = true;
            }
            emit_event(event_tx, DeepgramStreamEvent::Interim(text));
        }
    } else if parsed.from_finalize {
        state.finalize_ack_received = true;
    }
}

fn extract_error_detail(frame: &DeepgramStreamFrame) -> Option<String> {
    let is_error_type = frame
        .frame_type
        .as_deref()
        .is_some_and(|t| t.eq_ignore_ascii_case("Error"));
    if !is_error_type && frame.err_msg.is_none() && frame.err_code.is_none() {
        return None;
    }
    let detail = frame
        .err_msg
        .as_deref()
        .or(frame.description.as_deref())
        .or(frame.message.as_deref())
        .or(frame.err_code.as_deref())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or("Deepgram reported a streaming error");
    Some(detail.to_string())
}

fn emit_event(tx: Option<&mpsc::Sender<DeepgramStreamEvent>>, event: DeepgramStreamEvent) {
    if let Some(sender) = tx {
        if let Err(err) = sender.try_send(event) {
            tracing::trace!(error = %err, "dropped Deepgram stream event because consumer lagged");
        }
    }
}

fn build_tail_padding(config: &DeepgramStreamConfig) -> Option<Bytes> {
    if !config.encoding.trim().eq_ignore_ascii_case("linear16")
        || config.timeouts.tail_padding.is_zero()
    {
        return None;
    }
    let clamped = config.timeouts.tail_padding.min(MAX_TAIL_PADDING);
    let sample_count = (f64::from(config.sample_rate)
        * f64::from(config.channels)
        * clamped.as_secs_f64()) as usize;
    if sample_count == 0 {
        return None;
    }
    let byte_count = sample_count.checked_mul(2)?;
    Some(Bytes::from(vec![0u8; byte_count]))
}

fn parse_ws_base_url(ws_base_url: &str) -> Result<Url, ProviderError> {
    let trimmed = ws_base_url.trim();
    if trimmed.is_empty() {
        return Err(ProviderError::InvalidInput(
            "WebSocket URL must not be empty".to_string(),
        ));
    }
    let mut url = Url::parse(trimmed).map_err(|err| {
        ProviderError::InvalidInput(format!("invalid WebSocket URL '{trimmed}': {err}"))
    })?;
    match url.scheme() {
        "ws" | "wss" => {}
        scheme => {
            return Err(ProviderError::InvalidInput(format!(
                "unsupported WebSocket scheme '{scheme}' (expected ws or wss)"
            )));
        }
    }
    if url.host().is_none() {
        return Err(ProviderError::InvalidInput(format!(
            "WebSocket URL '{trimmed}' is missing a host"
        )));
    }
    url.set_fragment(None);
    Ok(url)
}

pub(crate) fn normalized_keywords(keywords: &[String]) -> Vec<String> {
    keywords
        .iter()
        .map(|k| k.trim())
        .filter(|k| !k.is_empty())
        .take(DEEPGRAM_KEYWORD_LIMIT)
        .map(ToOwned::to_owned)
        .collect()
}

const fn bool_str(value: bool) -> &'static str {
    if value {
        "true"
    } else {
        "false"
    }
}

fn read_bounded_duration_env(
    var: &str,
    default: Duration,
    min: Duration,
    max: Duration,
) -> Duration {
    let Some(raw) = read_env_non_empty(var) else {
        return default;
    };
    let Ok(secs) = raw.parse::<f64>() else {
        tracing::warn!(var, value = raw, "invalid float env value; using default");
        return default;
    };
    if !secs.is_finite() {
        return default;
    }
    Duration::from_secs_f64(secs.clamp(min.as_secs_f64(), max.as_secs_f64()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn build_stream_ws_url_uses_multi_for_auto_and_selects_vocab_param() {
        let keywords: Vec<String> = (0..120).map(|i| format!("term{i}")).collect();
        let config = DeepgramStreamConfig::default()
            .with_model("nova-3")
            .with_language("auto")
            .with_sample_rate(16_000)
            .with_keywords(keywords);

        let url = build_stream_ws_url(DEEPGRAM_WS_URL, &config).unwrap();
        let pairs: Vec<(String, String)> = url.query_pairs().into_owned().collect();
        assert!(pairs.contains(&("model".to_string(), "nova-3".to_string())));
        assert!(pairs.contains(&("language".to_string(), "multi".to_string())));
        assert!(pairs.contains(&("smart_format".to_string(), "true".to_string())));
        assert!(pairs.contains(&("punctuate".to_string(), "true".to_string())));
        assert!(pairs.contains(&("interim_results".to_string(), "true".to_string())));
        assert!(pairs.contains(&("encoding".to_string(), "linear16".to_string())));
        assert!(pairs.contains(&("sample_rate".to_string(), "16000".to_string())));
        assert!(pairs.contains(&("channels".to_string(), "1".to_string())));
        assert!(!pairs.iter().any(|(k, _)| k == "detect_language"));
        assert_eq!(pairs.iter().filter(|(k, _)| k == "keyterm").count(), 100);

        let nova2_cfg = DeepgramStreamConfig::default()
            .with_model("nova-2")
            .with_language("de")
            .with_keywords(vec!["PulseScribe".to_string(), "  ".to_string()]);
        let nova2_url = build_stream_ws_url("ws://127.0.0.1:9000/v1/listen", &nova2_cfg).unwrap();
        let pairs2: Vec<(String, String)> = nova2_url.query_pairs().into_owned().collect();
        assert!(pairs2.contains(&("language".to_string(), "de".to_string())));
        assert_eq!(pairs2.iter().filter(|(k, _)| k == "keywords").count(), 1);
    }

    #[test]
    fn build_stream_ws_url_validates_scheme_model_and_audio_params() {
        let valid = DeepgramStreamConfig::default();
        assert!(build_stream_ws_url("https://api.deepgram.com/v1/listen", &valid).is_err());

        let bad_rate = DeepgramStreamConfig::default().with_sample_rate(4_000);
        assert!(build_stream_ws_url(DEEPGRAM_WS_URL, &bad_rate).is_err());

        let bad_channels = DeepgramStreamConfig::default().with_channels(0);
        assert!(build_stream_ws_url(DEEPGRAM_WS_URL, &bad_channels).is_err());

        let empty_model = DeepgramStreamConfig::default().with_model("   ");
        assert!(build_stream_ws_url(DEEPGRAM_WS_URL, &empty_model).is_err());
    }

    #[test]
    fn stream_client_debug_redacts_api_key_and_url_credentials() {
        let client = DeepgramStreamClient::with_ws_url(
            "wss://user:pw@proxy.example/v1/listen?secret=xyz",
            "dg-super-secret",
        )
        .unwrap();
        let debug = format!("{client:?}");
        assert!(!debug.contains("dg-super-secret"));
        assert!(!debug.contains("pw"));
        assert!(!debug.contains("secret=xyz"));
        assert!(debug.contains("[REDACTED]"));
    }

    #[test]
    fn tail_padding_matches_linear16_pcm_byte_count() {
        let cfg = DeepgramStreamConfig::default()
            .with_sample_rate(16_000)
            .with_channels(1);
        let padding = build_tail_padding(&cfg).unwrap();
        // 16000 Hz * 1 ch * 0.25 s * 2 bytes/sample = 8000 bytes
        assert_eq!(padding.len(), 8_000);
        assert!(padding.iter().all(|&b| b == 0));

        let mut zero_cfg = cfg;
        zero_cfg.timeouts.tail_padding = Duration::ZERO;
        assert!(build_tail_padding(&zero_cfg).is_none());
    }
}
