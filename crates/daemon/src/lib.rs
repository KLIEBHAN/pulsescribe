//! Cross-platform dictation daemon core for PulseScribe (ADR-003).
//!
//! Unifies the macOS (`pulsescribe_daemon.py`) and Windows (`pulsescribe_windows.py`)
//! daemon configuration, event bus, file-based IPC protocol, lifecycle state machine,
//! platform adapter bundle, async runtime actor, and live audio/provider dictation backend
//! into one platform-agnostic crate.

pub mod config;
pub mod events;
pub mod ipc;
pub mod live_backend;
pub mod platform_bundle;
pub mod runtime;
pub mod state_machine;

pub use config::{
    DaemonConfig, DaemonMode, DaemonTimings, RedactedEnv, ReloadTracker, DEFAULT_DONE_DISPLAY_HOLD,
    DEFAULT_MACOS_HOLD_HOTKEY, DEFAULT_NO_SPEECH_RESET_DELAY, DEFAULT_TRANSCRIBING_TIMEOUT,
    DEFAULT_WINDOWS_HOLD_HOTKEY, DEFAULT_WINDOWS_TOGGLE_HOTKEY, RELOAD_SIGNAL_FILENAME,
};
pub use events::{DaemonEvent, DaemonEventBus, DEFAULT_EVENT_BUS_CAPACITY};
pub use ipc::{
    IpcClient, IpcCommand, IpcCommandKind, IpcError, IpcPaths, IpcResponse, IpcServer, IpcStatus,
    IPC_COMMAND_FILENAME, IPC_POLL_INTERVAL, IPC_RESPONSE_FILENAME,
};
pub use live_backend::{
    AudioSource, AudioSourceFactory, CpalAudioSourceFactory, LiveDictationBackend,
    LocalAsrManagerTranscriber, LocalPreloadObserver, LocalTranscriber, LocalTranscriptionRequest,
    ProviderEndpoints, AUDIO_LEVEL_EMIT_INTERVAL, CAPTURE_POLL_INTERVAL, MAX_BATCH_CAPTURE_SAMPLES,
    SPEECH_VAD_THRESHOLD_RMS,
};
pub use platform_bundle::{DaemonPlatform, NoopHotkeyBackend, NoopHotkeyRegistration};
pub use runtime::{
    ActiveSession, DaemonHandle, DaemonRuntime, DaemonRuntimeOptions, DictationBackend,
    PipelineEvent, DEFAULT_RELOAD_POLL_INTERVAL,
};
pub use state_machine::{ActiveRun, DaemonAction, DaemonStateMachine, RunKind};
