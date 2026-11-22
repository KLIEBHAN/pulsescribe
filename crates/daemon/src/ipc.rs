//! File-based IPC protocol wire-compatible with `utils/ipc.py`.
//!
//! Exchanges atomic JSON files (`ipc_command.json` and `ipc_response.json`) inside
//! `~/.pulsescribe/` so external onboarding/settings tools and the daemon can coordinate
//! test dictations without platform-specific named pipes or sockets.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use pulsescribe_core::atomic_io::write_text_atomic;
use pulsescribe_core::paths::{build_file_signature, AppPaths, FileSignature};
use serde::{Deserialize, Serialize};
use thiserror::Error;
use tokio::sync::watch;
use tokio::task::JoinHandle;

/// Command file written by client (wizard) and consumed by daemon (`utils/ipc.py`).
pub const IPC_COMMAND_FILENAME: &str = "ipc_command.json";

/// Response file written by daemon and polled by client (`utils/ipc.py`).
pub const IPC_RESPONSE_FILENAME: &str = "ipc_response.json";

/// Default polling interval matching `utils/ipc.py::POLL_INTERVAL_SECONDS`.
pub const IPC_POLL_INTERVAL: Duration = Duration::from_millis(200);

static IPC_ID_COUNTER: AtomicU64 = AtomicU64::new(1);

/// Errors returned by IPC file write operations.
#[derive(Debug, Error)]
pub enum IpcError {
    #[error("IPC I/O error: {0}")]
    Io(#[from] io::Error),
    #[error("IPC JSON error: {0}")]
    Json(#[from] serde_json::Error),
}

/// Command verbs accepted on `ipc_command.json`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum IpcCommandKind {
    StartTest,
    StopTest,
    OpenSettings,
    Unknown(String),
}

impl IpcCommandKind {
    pub fn parse(raw: &str) -> Self {
        match raw.trim() {
            "start_test" => Self::StartTest,
            "stop_test" => Self::StopTest,
            "open_settings" => Self::OpenSettings,
            other => Self::Unknown(other.to_owned()),
        }
    }

    pub fn as_str(&self) -> &str {
        match self {
            Self::StartTest => "start_test",
            Self::StopTest => "stop_test",
            Self::OpenSettings => "open_settings",
            Self::Unknown(raw) => raw.as_str(),
        }
    }
}

/// Status values written to `ipc_response.json` (`STATUS_*` in `utils/ipc.py`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IpcStatus {
    Recording,
    Done,
    Error,
    Stopped,
}

impl IpcStatus {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Recording => "recording",
            Self::Done => "done",
            Self::Error => "error",
            Self::Stopped => "stopped",
        }
    }
}

/// Wire payload stored in `ipc_command.json`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct IpcCommand {
    pub id: String,
    pub command: String,
    #[serde(default)]
    pub timestamp: f64,
}

/// Wire payload stored in `ipc_response.json`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct IpcResponse {
    pub id: String,
    pub status: IpcStatus,
    #[serde(default)]
    pub transcript: String,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default)]
    pub timestamp: f64,
}

/// File locations for one IPC endpoint pair.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IpcPaths {
    pub command_file: PathBuf,
    pub response_file: PathBuf,
}

impl IpcPaths {
    pub fn from_app_paths(paths: &AppPaths) -> Self {
        Self::in_dir(paths.config_dir())
    }

    pub fn in_dir(dir: impl AsRef<Path>) -> Self {
        let dir = dir.as_ref();
        Self {
            command_file: dir.join(IPC_COMMAND_FILENAME),
            response_file: dir.join(IPC_RESPONSE_FILENAME),
        }
    }
}

/// Client side of the file-based IPC protocol (mirrors `utils/ipc.py::IPCClient`).
#[derive(Debug)]
pub struct IpcClient {
    paths: IpcPaths,
    last_response_sig: Option<FileSignature>,
    last_response_payload: Option<IpcResponse>,
}

impl IpcClient {
    pub fn new(paths: IpcPaths) -> Self {
        Self {
            paths,
            last_response_sig: None,
            last_response_payload: None,
        }
    }

    /// Write a command JSON file atomically and return its 8-char hex correlation ID.
    pub fn send_command(&self, kind: &IpcCommandKind) -> Result<String, IpcError> {
        let cmd_id = next_command_id();
        let payload = IpcCommand {
            id: cmd_id.clone(),
            command: kind.as_str().to_owned(),
            timestamp: unix_timestamp_secs(),
        };
        let encoded = serde_json::to_string(&payload)?;
        write_text_atomic(&self.paths.command_file, &encoded)?;
        tracing::debug!(command = kind.as_str(), id = %cmd_id, "IPC command sent");
        Ok(cmd_id)
    }

    /// Poll `ipc_response.json` using `FileSignature` caching, returning the response only when
    /// its correlation `id` matches `cmd_id`.
    pub fn poll_response(&mut self, cmd_id: &str) -> Option<IpcResponse> {
        let Ok(sig) = build_file_signature(&self.paths.response_file) else {
            self.last_response_sig = None;
            self.last_response_payload = None;
            return None;
        };

        if self.last_response_sig != Some(sig) {
            self.last_response_payload = safe_read_json::<IpcResponse>(&self.paths.response_file);
            self.last_response_sig = Some(sig);
        }

        self.last_response_payload
            .as_ref()
            .filter(|resp| resp.id == cmd_id)
            .cloned()
    }

    /// Best-effort removal of `ipc_response.json` after the client consumes it.
    pub fn clear_response(&mut self) {
        delete_if_exists(&self.paths.response_file);
        self.last_response_sig = None;
        self.last_response_payload = None;
    }
}

/// Server side of the file-based IPC protocol (mirrors `utils/ipc.py::IPCServer`).
#[derive(Debug)]
pub struct IpcServer {
    paths: IpcPaths,
    last_processed_id: Option<String>,
}

impl IpcServer {
    pub fn new(paths: IpcPaths) -> Self {
        Self {
            paths,
            last_processed_id: None,
        }
    }

    pub fn paths(&self) -> &IpcPaths {
        &self.paths
    }

    /// Discard stale IPC files before starting or after stopping the polling loop.
    pub fn cleanup_files(&self) {
        delete_if_exists(&self.paths.command_file);
        delete_if_exists(&self.paths.response_file);
    }

    /// Poll once for a new unprocessed command, removing `ipc_command.json` before returning so
    /// subsequent polls never re-dispatch it.
    pub fn poll_once(&mut self) -> Option<(String, IpcCommandKind)> {
        let cmd = safe_read_json::<IpcCommand>(&self.paths.command_file)?;
        if cmd.id.is_empty()
            || cmd.command.is_empty()
            || self.last_processed_id.as_deref() == Some(cmd.id.as_str())
        {
            return None;
        }
        self.last_processed_id = Some(cmd.id.clone());
        delete_if_exists(&self.paths.command_file);
        let kind = IpcCommandKind::parse(&cmd.command);
        tracing::debug!(command = kind.as_str(), id = %cmd.id, "IPC command received");
        Some((cmd.id, kind))
    }

    /// Write an atomic response JSON payload for `cmd_id`.
    pub fn send_response(
        &self,
        cmd_id: &str,
        status: IpcStatus,
        transcript: &str,
        error: Option<&str>,
    ) -> Result<(), IpcError> {
        let payload = IpcResponse {
            id: cmd_id.to_owned(),
            status,
            transcript: transcript.to_owned(),
            error: error.map(ToOwned::to_owned),
            timestamp: unix_timestamp_secs(),
        };
        let encoded = serde_json::to_string(&payload)?;
        write_text_atomic(&self.paths.response_file, &encoded)?;
        tracing::debug!(status = status.as_str(), id = %cmd_id, "IPC response sent");
        Ok(())
    }

    /// Spawn a background Tokio task polling `ipc_command.json` at `poll_interval` until
    /// `shutdown_rx` turns `true` (or its sender drops), cleaning up IPC files on start and exit.
    pub fn spawn_polling_task<F>(
        mut self,
        poll_interval: Duration,
        mut shutdown_rx: watch::Receiver<bool>,
        mut on_command: F,
    ) -> JoinHandle<()>
    where
        F: FnMut(String, IpcCommandKind) + Send + 'static,
    {
        self.cleanup_files();
        tokio::spawn(async move {
            let mut ticker = tokio::time::interval(poll_interval.max(Duration::from_millis(10)));
            ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
            loop {
                tokio::select! {
                    biased;
                    changed = shutdown_rx.changed() => {
                        if changed.is_err() || *shutdown_rx.borrow() {
                            break;
                        }
                    }
                    _ = ticker.tick() => {
                        if *shutdown_rx.borrow() {
                            break;
                        }
                        if let Some((cmd_id, kind)) = self.poll_once() {
                            on_command(cmd_id, kind);
                        }
                    }
                }
            }
            self.cleanup_files();
        })
    }
}

fn next_command_id() -> String {
    let seq = IPC_ID_COUNTER.fetch_add(1, Ordering::Relaxed);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0);
    let mixed = nanos ^ (u64::from(std::process::id()) << 16) ^ seq.wrapping_mul(0x9E37_79B9);
    format!("{:08x}", mixed as u32)
}

fn unix_timestamp_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn safe_read_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Option<T> {
    let raw = fs::read_to_string(path).ok()?;
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return None;
    }
    serde_json::from_str(trimmed).ok()
}

fn delete_if_exists(path: &Path) {
    if let Err(err) = fs::remove_file(path) {
        if err.kind() != io::ErrorKind::NotFound {
            tracing::debug!(path = %path.display(), error = %err, "IPC file cleanup ignored error");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::sync::mpsc;

    fn unique_ipc_dir(tag: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "pulsescribe-daemon-ipc-{tag}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn ipc_client_and_server_round_trip_and_deduplicate() {
        let dir = unique_ipc_dir("roundtrip");
        let paths = IpcPaths::in_dir(&dir);
        let mut client = IpcClient::new(paths.clone());
        let mut server = IpcServer::new(paths.clone());

        let cmd_id = client.send_command(&IpcCommandKind::StartTest).unwrap();
        assert_eq!(cmd_id.len(), 8);

        let polled = server.poll_once();
        assert_eq!(polled, Some((cmd_id.clone(), IpcCommandKind::StartTest)));
        assert!(!paths.command_file.exists());
        // Polling again returns None.
        assert_eq!(server.poll_once(), None);

        // Re-writing the exact same command ID is ignored as duplicate.
        let dup = serde_json::to_string(&IpcCommand {
            id: cmd_id.clone(),
            command: "start_test".into(),
            timestamp: 1.0,
        })
        .unwrap();
        fs::write(&paths.command_file, dup).unwrap();
        assert_eq!(server.poll_once(), None);

        // Wrong ID response is ignored; matching ID response is returned & cached.
        server
            .send_response("other123", IpcStatus::Recording, "", None)
            .unwrap();
        assert_eq!(client.poll_response(&cmd_id), None);

        server
            .send_response(&cmd_id, IpcStatus::Done, "Hallo Welt", None)
            .unwrap();
        let resp = client.poll_response(&cmd_id).expect("matching response");
        assert_eq!(resp.status, IpcStatus::Done);
        assert_eq!(resp.transcript, "Hallo Welt");
        assert_eq!(resp.error, None);

        client.clear_response();
        assert!(!paths.response_file.exists());
        assert_eq!(client.poll_response(&cmd_id), None);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn ipc_ignores_corrupt_and_non_object_json_gracefully() {
        let dir = unique_ipc_dir("corrupt");
        let paths = IpcPaths::in_dir(&dir);
        let mut server = IpcServer::new(paths.clone());
        let mut client = IpcClient::new(paths.clone());

        fs::write(&paths.command_file, "{truncated").unwrap();
        assert_eq!(server.poll_once(), None);

        fs::write(&paths.command_file, "[1, 2, 3]").unwrap();
        assert_eq!(server.poll_once(), None);

        fs::write(&paths.response_file, "\"not-an-object\"").unwrap();
        assert_eq!(client.poll_response("abc12345"), None);

        let _ = fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn ipc_polling_task_cleans_stale_files_dispatches_and_cleans_on_shutdown() {
        let dir = unique_ipc_dir("async");
        let paths = IpcPaths::in_dir(&dir);
        fs::write(
            &paths.command_file,
            "{\"id\":\"stale\",\"command\":\"start_test\"}",
        )
        .unwrap();
        fs::write(
            &paths.response_file,
            "{\"id\":\"stale\",\"status\":\"done\"}",
        )
        .unwrap();

        let (shutdown_tx, shutdown_rx) = watch::channel(false);
        let (rx_tx, mut rx_rx) = mpsc::unbounded_channel();

        let server = IpcServer::new(paths.clone());
        let handle = server.spawn_polling_task(
            Duration::from_millis(20),
            shutdown_rx,
            move |cmd_id, kind| {
                let _ = rx_tx.send((cmd_id, kind));
            },
        );

        // Stale files were discarded on startup.
        assert!(!paths.command_file.exists());
        assert!(!paths.response_file.exists());

        let client = IpcClient::new(paths.clone());
        let cmd_id = client.send_command(&IpcCommandKind::OpenSettings).unwrap();

        let received = tokio::time::timeout(Duration::from_secs(2), rx_rx.recv())
            .await
            .expect("command should arrive promptly")
            .expect("channel open");
        assert_eq!(received, (cmd_id, IpcCommandKind::OpenSettings));

        let _ = shutdown_tx.send(true);
        handle.await.unwrap();
        assert!(!paths.command_file.exists());
        assert!(!paths.response_file.exists());

        let _ = fs::remove_dir_all(&dir);
    }
}
