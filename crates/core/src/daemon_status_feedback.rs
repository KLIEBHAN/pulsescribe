//! User-facing daemon status labels, recovery hints, and tray/menu-bar titles.
//!
//! Mirrors `ui/daemon_status_feedback.py` so menu bar, tray tooltip, and overlay
//! share identical error classification and recovery copy across macOS and Windows.

use crate::overlay_text::normalized_text_prefix;
use crate::state::{AppState, DaemonErrorCode, DaemonStatusError};
use crate::transcript_delivery::{delivery_hint, COPIED, TEST_COMPLETE};

/// Borrowed detail payload passed to daemon status helpers (plain text or structured error).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum StatusDetail<'a> {
    #[default]
    None,
    Text(&'a str),
    Error(&'a DaemonStatusError),
}

impl<'a> From<&'a str> for StatusDetail<'a> {
    fn from(value: &'a str) -> Self {
        Self::Text(value)
    }
}

impl<'a> From<Option<&'a str>> for StatusDetail<'a> {
    fn from(value: Option<&'a str>) -> Self {
        match value {
            Some(text) => Self::Text(text),
            None => Self::None,
        }
    }
}

impl<'a> From<&'a DaemonStatusError> for StatusDetail<'a> {
    fn from(error: &'a DaemonStatusError) -> Self {
        Self::Error(error)
    }
}

impl<'a> StatusDetail<'a> {
    fn raw_text(self) -> &'a str {
        match self {
            Self::None => "",
            Self::Text(text) => text,
            Self::Error(err) => err.detail.as_deref().unwrap_or(err.code.as_str()),
        }
    }
}

/// Default status label per lifecycle state.
pub const fn default_daemon_status_label(state: AppState) -> &'static str {
    match state {
        AppState::Idle => "Ready to dictate",
        AppState::Loading => "Preparing transcription",
        AppState::Listening => "Listening",
        AppState::Recording => "Recording — speak now",
        AppState::Transcribing => "Transcribing",
        AppState::Refining => "Refining",
        AppState::Done => "Transcript pasted",
        AppState::NoSpeech => "No speech detected",
        AppState::Error => "Something went wrong",
    }
}

/// Default contextual guidance hint per lifecycle state.
pub const fn default_daemon_hint(state: AppState) -> &'static str {
    match state {
        AppState::Idle => "Use your hotkey to start dictation.",
        AppState::Loading => "First launch or provider changes can take a moment.",
        AppState::Listening => "Start speaking, or release the hold hotkey to cancel.",
        AppState::Recording => "Release the hold hotkey or stop the toggle hotkey to finish.",
        AppState::Transcribing => "PulseScribe is turning your speech into text.",
        AppState::Refining => {
            "PulseScribe is polishing punctuation and formatting before paste."
        }
        AppState::Done => "Ready for another dictation.",
        AppState::NoSpeech => {
            "Try again and speak a short sentence after the listening prompt."
        }
        AppState::Error => {
            "Try again. PulseScribe will return to ready automatically. Export diagnostics or open Setup if it keeps happening."
        }
    }
}

/// Friendly label for a recognized `DaemonErrorCode`.
pub const fn error_code_label(code: DaemonErrorCode) -> Option<&'static str> {
    match code {
        DaemonErrorCode::MissingApiKey => Some("Missing API key"),
        DaemonErrorCode::Busy => Some("PulseScribe is busy"),
        DaemonErrorCode::Timeout => Some("Transcription timed out"),
        DaemonErrorCode::InvalidProvider => Some("Invalid provider setting"),
        DaemonErrorCode::InputMonitoring => Some("Input monitoring needed"),
        DaemonErrorCode::AccessibilityPermission => Some("Accessibility permission needed"),
        DaemonErrorCode::MicrophonePermission => Some("Microphone permission needed"),
        DaemonErrorCode::MicrophoneUnavailable => Some("Microphone unavailable"),
        DaemonErrorCode::Connection => Some("Could not reach the transcription service"),
        DaemonErrorCode::MissingDependency => Some("Missing dependency"),
        DaemonErrorCode::Unknown => None,
    }
}

/// Recovery hint for a recognized `DaemonErrorCode`.
pub const fn error_code_hint(code: DaemonErrorCode) -> Option<&'static str> {
    match code {
        DaemonErrorCode::MissingApiKey => {
            Some("Add the required key in Setup & Settings, then try again.")
        }
        DaemonErrorCode::Busy => {
            Some("Wait a moment for the current task to finish, then try again.")
        }
        DaemonErrorCode::Timeout => Some(
            "PulseScribe will return to ready automatically. Export diagnostics or open Setup if it repeats.",
        ),
        DaemonErrorCode::InvalidProvider => {
            Some("Open Setup & Settings, choose a supported provider, then try again.")
        }
        DaemonErrorCode::InputMonitoring => {
            Some("Open Setup & Settings, grant the missing permission, then try again.")
        }
        DaemonErrorCode::AccessibilityPermission => {
            Some("Open Setup & Settings, grant the missing permission, then try again.")
        }
        DaemonErrorCode::MicrophonePermission => {
            Some("Open Setup & Settings, allow microphone access, then try again.")
        }
        DaemonErrorCode::MicrophoneUnavailable => {
            Some("Check microphone access and the selected input device, then try again.")
        }
        DaemonErrorCode::Connection => {
            Some("Check your internet connection or provider status, then try again.")
        }
        DaemonErrorCode::MissingDependency => {
            Some("Install the missing dependency or switch providers in Setup & Settings.")
        }
        DaemonErrorCode::Unknown => None,
    }
}

/// Parse a state name (case-insensitive enum name or snake_case value), falling back to `Idle`.
pub fn parse_app_state_or_idle(raw: Option<&str>) -> AppState {
    match raw.unwrap_or("").trim().to_ascii_lowercase().as_str() {
        "idle" => AppState::Idle,
        "loading" => AppState::Loading,
        "listening" => AppState::Listening,
        "recording" => AppState::Recording,
        "transcribing" => AppState::Transcribing,
        "refining" => AppState::Refining,
        "done" => AppState::Done,
        "no_speech" => AppState::NoSpeech,
        "error" => AppState::Error,
        _ => AppState::Idle,
    }
}

/// Collapse whitespace so titles and hints stay compact and readable.
pub fn normalize_daemon_status_text(text: Option<&str>) -> String {
    let mut out = String::new();
    for (idx, word) in text.unwrap_or("").split_whitespace().enumerate() {
        if idx > 0 {
            out.push(' ');
        }
        out.push_str(word);
    }
    out
}

fn truncate_status_text(text: &str, max_chars: usize) -> String {
    if max_chars == 0 || text.chars().count() <= max_chars {
        return text.to_string();
    }
    if max_chars == 1 {
        return text.chars().take(1).collect();
    }
    let prefix: String = text.chars().take(max_chars - 1).collect();
    format!("{}…", prefix.trim_end())
}

fn contains_any(text: &str, keywords: &[&str]) -> bool {
    keywords.iter().any(|kw| text.contains(kw))
}

fn infer_daemon_error_code(detail_lower: &str) -> DaemonErrorCode {
    if contains_any(
        detail_lower,
        &[
            "api key",
            "_api_key",
            "deepgram_api_key",
            "openai_api_key",
            "groq_api_key",
            "openrouter_api_key",
            "gemini_api_key",
        ],
    ) {
        return DaemonErrorCode::MissingApiKey;
    }
    if contains_any(
        detail_lower,
        &["busy", "already recording", "still running"],
    ) {
        return DaemonErrorCode::Busy;
    }
    if contains_any(
        detail_lower,
        &["timeout", "timed out", "watchdog", "no final response"],
    ) {
        return DaemonErrorCode::Timeout;
    }
    if contains_any(
        detail_lower,
        &[
            "unknown provider",
            "unbekannter provider",
            "invalid provider",
            "unsupported provider",
            "unknown refine-provider",
            "unknown refine provider",
            "unbekannter refine-provider",
            "unbekannter refine provider",
            "invalid refine provider",
            "unsupported refine provider",
            "unsupported refine-provider",
        ],
    ) {
        return DaemonErrorCode::InvalidProvider;
    }
    if detail_lower.contains("input monitoring") {
        return DaemonErrorCode::InputMonitoring;
    }
    if contains_any(
        detail_lower,
        &["accessibility", "assistive access", "access permission"],
    ) {
        return DaemonErrorCode::AccessibilityPermission;
    }
    if contains_any(detail_lower, &["microphone", "mic"])
        && contains_any(
            detail_lower,
            &[
                "permission",
                "denied",
                "not permitted",
                "not allowed",
                "access",
            ],
        )
    {
        return DaemonErrorCode::MicrophonePermission;
    }
    if contains_any(
        detail_lower,
        &[
            "microphone",
            "mic",
            "input device",
            "audio",
            "sounddevice",
            "wasapi",
        ],
    ) {
        return DaemonErrorCode::MicrophoneUnavailable;
    }
    if contains_any(
        detail_lower,
        &[
            "connection",
            "connect",
            "network",
            "socket",
            "websocket",
            "streaming",
            "could not reach",
            "failed to reach",
            "service unavailable",
            "provider status",
            "provider unavailable",
            "transcription service",
        ],
    ) {
        return DaemonErrorCode::Connection;
    }
    if contains_any(
        detail_lower,
        &[
            "no module named",
            "modulenotfounderror",
            "importerror",
            "cannot import name",
            "missing dependency",
            "dependency",
            "pip install",
            "install package",
        ],
    ) {
        return DaemonErrorCode::MissingDependency;
    }
    DaemonErrorCode::Unknown
}

/// Infer a structured `DaemonStatusError` from plain text or pass an existing one through.
pub fn infer_daemon_status_error<'a>(
    input: impl Into<StatusDetail<'a>>,
) -> Option<DaemonStatusError> {
    match input.into() {
        StatusDetail::None => None,
        StatusDetail::Error(err) => Some(err.clone()),
        StatusDetail::Text(text) => {
            let detail = normalize_daemon_status_text(Some(text));
            if detail.is_empty() {
                return None;
            }
            let code = infer_daemon_error_code(&detail.to_lowercase());
            Some(DaemonStatusError::new(code, Some(detail)))
        }
    }
}

fn build_loading_label(detail: &str, prefer_detail: bool) -> String {
    if detail.is_empty() {
        return default_daemon_status_label(AppState::Loading).to_string();
    }
    let lower = detail.to_lowercase();
    if contains_any(
        &lower,
        &["starting up", "startup", "initializing", "prewarm"],
    ) {
        return "Starting up PulseScribe".to_string();
    }
    if lower.contains("warming up") {
        return "Warming up local model".to_string();
    }
    if contains_any(&lower, &["input device", "microphone", "audio"]) {
        return "Preparing microphone".to_string();
    }
    if contains_any(
        &lower,
        &[
            "connection",
            "connect",
            "network",
            "socket",
            "websocket",
            "service",
        ],
    ) {
        return "Connecting to transcription service".to_string();
    }
    if prefer_detail || lower.starts_with("loading ") {
        return detail.trim_end_matches('.').to_string();
    }
    default_daemon_status_label(AppState::Loading).to_string()
}

fn build_no_speech_label(detail: &str) -> String {
    if detail.is_empty() {
        return default_daemon_status_label(AppState::NoSpeech).to_string();
    }
    let lower = detail.to_lowercase();
    if contains_any(
        &lower,
        &[
            "no speech",
            "empty transcript",
            "no transcript",
            "no audio",
            "silent",
            "silence",
            "kein audio",
            "keine sprache",
        ],
    ) {
        return default_daemon_status_label(AppState::NoSpeech).to_string();
    }
    detail.trim_end_matches('.').to_string()
}

fn build_error_label(input: StatusDetail<'_>) -> String {
    let Some(error_info) = infer_daemon_status_error(input) else {
        return default_daemon_status_label(AppState::Error).to_string();
    };
    let normalized_detail = normalize_daemon_status_text(error_info.detail.as_deref());
    if error_info.code == DaemonErrorCode::Unknown {
        return if normalized_detail.is_empty() {
            default_daemon_status_label(AppState::Error).to_string()
        } else {
            normalized_detail
        };
    }
    error_code_label(error_info.code)
        .map(ToOwned::to_owned)
        .unwrap_or_else(|| {
            if normalized_detail.is_empty() {
                default_daemon_status_label(AppState::Error).to_string()
            } else {
                normalized_detail
            }
        })
}

/// Build a concise user-facing status label for non-overlay UI surfaces.
///
/// Passing `max_chars == 0` preserves the full normalized label without truncation.
pub fn build_daemon_status_label<'a>(
    state: AppState,
    text: impl Into<StatusDetail<'a>>,
    prefer_detail: bool,
    max_chars: usize,
) -> String {
    let detail_input = text.into();

    if matches!(state, AppState::Recording | AppState::Done) && prefer_detail {
        let raw_detail = detail_input.raw_text();
        let detail = if max_chars > 0 {
            normalized_text_prefix(raw_detail, max_chars.saturating_add(1))
        } else {
            normalize_daemon_status_text(Some(raw_detail))
        };
        if !detail.is_empty() {
            let label = if state == AppState::Recording {
                format!("Recording: {detail}")
            } else {
                detail
            };
            return truncate_status_text(&label, max_chars);
        }
    }

    match state {
        AppState::Loading => {
            let detail = normalize_daemon_status_text(Some(detail_input.raw_text()));
            truncate_status_text(&build_loading_label(&detail, prefer_detail), max_chars)
        }
        AppState::Done => {
            let raw = detail_input.raw_text();
            let label = if raw.starts_with(COPIED) {
                COPIED
            } else if raw.starts_with(TEST_COMPLETE) {
                TEST_COMPLETE
            } else {
                default_daemon_status_label(AppState::Done)
            };
            truncate_status_text(label, max_chars)
        }
        AppState::NoSpeech => {
            let detail = normalize_daemon_status_text(Some(detail_input.raw_text()));
            truncate_status_text(&build_no_speech_label(&detail), max_chars)
        }
        AppState::Error => {
            // Preserve structured DaemonStatusError codes directly while normalizing raw text.
            let label = match detail_input {
                StatusDetail::Error(err) => build_error_label(StatusDetail::Error(err)),
                other => {
                    let normalized = normalize_daemon_status_text(Some(other.raw_text()));
                    build_error_label(StatusDetail::Text(&normalized))
                }
            };
            truncate_status_text(&label, max_chars)
        }
        other => truncate_status_text(default_daemon_status_label(other), max_chars),
    }
}

fn build_loading_hint(detail: &str, detail_lower: &str) -> &'static str {
    if contains_any(
        detail_lower,
        &["starting up", "startup", "initializing", "prewarm"],
    ) {
        return "PulseScribe is starting in the background. Hotkeys and audio are still getting ready.";
    }
    if detail_lower.contains("warming up") {
        return "Offline dictation is warming up after a model or settings change.";
    }
    if detail_lower.starts_with("loading ") {
        return "Offline dictation is loading your selected local model. The first run can take a moment.";
    }
    if contains_any(
        detail_lower,
        &[
            "connection",
            "connect",
            "network",
            "socket",
            "websocket",
            "service",
        ],
    ) {
        return "PulseScribe is reconnecting to the transcription service. Try again once it returns to ready.";
    }
    if !detail.is_empty() {
        return "PulseScribe is preparing the current provider or model.";
    }
    default_daemon_hint(AppState::Loading)
}

fn build_no_speech_hint(detail_lower: &str) -> &'static str {
    if contains_any(
        detail_lower,
        &[
            "no audio",
            "empty transcript",
            "silent",
            "silence",
            "too short",
        ],
    ) {
        return "Try again and speak a little earlier, louder, or closer to the microphone.";
    }
    default_daemon_hint(AppState::NoSpeech)
}

fn build_error_hint(input: StatusDetail<'_>) -> &'static str {
    if let StatusDetail::Text(text) = input {
        if let Some(hint) = delivery_hint(text) {
            return hint;
        }
    }
    if let Some(error_info) = infer_daemon_status_error(input) {
        if let Some(hint) = error_code_hint(error_info.code) {
            return hint;
        }
    }
    default_daemon_hint(AppState::Error)
}

/// Build contextual guidance or recovery help for a daemon state.
pub fn build_daemon_status_hint<'a>(
    state: AppState,
    text: impl Into<StatusDetail<'a>>,
    max_chars: usize,
) -> String {
    let detail_input = text.into();
    let hint = match state {
        AppState::Loading => {
            let detail = normalize_daemon_status_text(Some(detail_input.raw_text()));
            let lower = detail.to_lowercase();
            build_loading_hint(&detail, &lower)
        }
        AppState::NoSpeech => {
            let lower = normalize_daemon_status_text(Some(detail_input.raw_text())).to_lowercase();
            build_no_speech_hint(&lower)
        }
        AppState::Error => build_error_hint(detail_input),
        other => default_daemon_hint(other),
    };
    truncate_status_text(hint, max_chars)
}

/// Build one accessible tray/tooltip title combining status and recovery guidance.
pub fn build_daemon_tray_title<'a>(
    state: AppState,
    text: impl Into<StatusDetail<'a>>,
    app_name: &str,
    max_chars: usize,
) -> String {
    let detail_input = text.into();
    let label = build_daemon_status_label(state, detail_input, false, max_chars);

    let summary = if matches!(
        state,
        AppState::Loading | AppState::NoSpeech | AppState::Error
    ) {
        let hint = build_daemon_status_hint(state, detail_input, max_chars);
        format!("{label} — {hint}")
    } else {
        label
    };

    let prefix = format!("{app_name} — ");
    let budget = max_chars.saturating_sub(prefix.chars().count()).max(8);
    format!("{prefix}{}", truncate_status_text(&summary, budget))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn error_classification_and_structured_errors_match_contract() {
        let err = DaemonStatusError::new(
            DaemonErrorCode::InvalidProvider,
            Some("Unbekannter Provider: foo".to_string()),
        );
        assert_eq!(
            build_daemon_status_label(AppState::Error, &err, true, 80),
            "Invalid provider setting"
        );
        assert_eq!(
            build_daemon_status_hint(AppState::Error, &err, 120),
            "Open Setup & Settings, choose a supported provider, then try again."
        );
        assert_eq!(infer_daemon_status_error(&err), Some(err));

        // Unclassified attribute error stays verbatim.
        let verbatim = "module 'x' has no attribute 'y'";
        assert_eq!(
            build_daemon_status_label(AppState::Error, verbatim, true, 80),
            verbatim
        );

        // Structured error with empty detail still maps by code.
        let code_only = DaemonStatusError::new(DaemonErrorCode::MissingApiKey, None);
        assert_eq!(
            build_daemon_status_label(AppState::Error, &code_only, true, 80),
            "Missing API key"
        );
    }

    #[test]
    fn tray_title_and_done_label_preserve_known_statuses() {
        assert_eq!(
            build_daemon_status_label(AppState::Done, "Copied to clipboard · 0.4 s", false, 80),
            "Copied to clipboard"
        );
        assert_eq!(
            build_daemon_status_label(AppState::Done, "Transcript pasted into Mail", false, 80),
            "Transcript pasted"
        );

        let title = build_daemon_tray_title(
            AppState::Error,
            "OPENAI_API_KEY not set",
            "PulseScribe",
            128,
        );
        assert!(title.starts_with("PulseScribe — Missing API key — "));
        assert!(title.contains("Setup & Settings"));
    }
}
