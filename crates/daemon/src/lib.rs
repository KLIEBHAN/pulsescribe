//! Cross-platform dictation daemon core for PulseScribe (ADR-003).
//!
//! Unifies the macOS (`pulsescribe_daemon.py`) and Windows (`pulsescribe_windows.py`)
//! daemon configuration, event bus, and file-based IPC protocol into one platform-agnostic crate.

pub mod config;
pub mod events;
pub mod ipc;

pub use config::{
    DaemonConfig, DaemonMode, DaemonTimings, ReloadTracker, DEFAULT_DONE_DISPLAY_HOLD,
    DEFAULT_NO_SPEECH_RESET_DELAY, DEFAULT_TRANSCRIBING_TIMEOUT, RELOAD_SIGNAL_FILENAME,
};
pub use events::{DaemonEvent, DaemonEventBus, DEFAULT_EVENT_BUS_CAPACITY};
pub use ipc::{
    IpcClient, IpcCommand, IpcCommandKind, IpcError, IpcPaths, IpcResponse, IpcServer, IpcStatus,
    IPC_COMMAND_FILENAME, IPC_POLL_INTERVAL, IPC_RESPONSE_FILENAME,
};
