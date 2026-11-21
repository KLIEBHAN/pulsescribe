//! Core daemon lifecycle states and structured status errors.
//!
//! Mirrors `utils/state.py` so Python and Rust share identical wire/display values.

use std::fmt;

use serde::{Deserialize, Serialize};

/// Dictation daemon lifecycle state shown in the menu bar, tray, and overlay.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AppState {
    Idle,
    /// Model is being loaded or downloaded.
    Loading,
    /// Hotkey pressed, waiting for speech.
    Listening,
    /// Speech detected and actively recording.
    Recording,
    Transcribing,
    Refining,
    Done,
    /// Recording finished without a usable transcript.
    NoSpeech,
    Error,
}

impl AppState {
    /// Canonical snake_case identifier matching `utils.state.AppState.value`.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Idle => "idle",
            Self::Loading => "loading",
            Self::Listening => "listening",
            Self::Recording => "recording",
            Self::Transcribing => "transcribing",
            Self::Refining => "refining",
            Self::Done => "done",
            Self::NoSpeech => "no_speech",
            Self::Error => "error",
        }
    }
}

/// Structured error categories surfaced by the dictation daemon.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DaemonErrorCode {
    MissingApiKey,
    Busy,
    Timeout,
    InvalidProvider,
    InputMonitoring,
    AccessibilityPermission,
    MicrophonePermission,
    MicrophoneUnavailable,
    Connection,
    MissingDependency,
    Unknown,
}

impl DaemonErrorCode {
    /// Canonical snake_case identifier matching `utils.state.DaemonErrorCode.value`.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::MissingApiKey => "missing_api_key",
            Self::Busy => "busy",
            Self::Timeout => "timeout",
            Self::InvalidProvider => "invalid_provider",
            Self::InputMonitoring => "input_monitoring",
            Self::AccessibilityPermission => "accessibility_permission",
            Self::MicrophonePermission => "microphone_permission",
            Self::MicrophoneUnavailable => "microphone_unavailable",
            Self::Connection => "connection",
            Self::MissingDependency => "missing_dependency",
            Self::Unknown => "unknown",
        }
    }
}

/// User-facing daemon error carrying a stable code and optional detail text.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DaemonStatusError {
    pub code: DaemonErrorCode,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
}

impl DaemonStatusError {
    pub fn new(code: DaemonErrorCode, detail: impl Into<Option<String>>) -> Self {
        Self {
            code,
            detail: detail.into(),
        }
    }
}

impl fmt::Display for DaemonStatusError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self.detail.as_deref() {
            Some(detail) if !detail.is_empty() => f.write_str(detail),
            _ => f.write_str(self.code.as_str()),
        }
    }
}

impl std::error::Error for DaemonStatusError {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn status_error_display_prefers_non_empty_detail() {
        let with_detail = DaemonStatusError::new(
            DaemonErrorCode::MissingApiKey,
            Some("DEEPGRAM_API_KEY missing".to_string()),
        );
        assert_eq!(with_detail.to_string(), "DEEPGRAM_API_KEY missing");

        let without_detail = DaemonStatusError::new(DaemonErrorCode::Busy, None);
        assert_eq!(without_detail.to_string(), "busy");

        let empty_detail = DaemonStatusError::new(DaemonErrorCode::Timeout, Some(String::new()));
        assert_eq!(empty_detail.to_string(), "timeout");
    }
}
