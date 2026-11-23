//! Toolkit-independent overlay content view-model per daemon state.
//!
//! Mirrors `ui/overlay_feedback.py`: maps daemon states and status payloads into
//! a single `OverlayContent` frame specifying bar mode, outcome badge, text,
//! right-aligned meta, clickable action, and dismissibility.

use serde::{Deserialize, Serialize};

use crate::daemon_status_feedback::{
    build_daemon_status_label, normalize_daemon_status_text, parse_app_state_or_idle,
};
use crate::overlay_style::{OverlayAction, OverlayBarMode, OverlayTone};
use crate::state::AppState;
use crate::transcript_delivery::{
    COPIED, COPY_FAILED, PASTED, PASTE_FAILED_MACOS, PASTE_FAILED_OTHER, TEST_COMPLETE,
};

/// How long daemons keep `AppState::Error` before returning to `Idle`.
pub const ERROR_FEEDBACK_SECONDS: f64 = 4.0;
pub const NO_SPEECH_TEXT: &str = "Nothing was pasted";
pub const PASTED_BADGE: &str = "Pasted";
/// Suffix appended to a `TRANSCRIBING` payload when a `REFINING` phase follows.
pub const THEN_REFINING: &str = ", then refining";

const FRIENDLY_ERROR_LABELS: [&str; 7] = [
    "missing api key",
    "pulsescribe is busy",
    "transcription timed out",
    "microphone unavailable",
    "could not reach the transcription service",
    "missing dependency",
    "something went wrong",
];

const SETTINGS_ERROR_LABELS: [&str; 2] = ["missing api key", "microphone unavailable"];

/// Default overlay copy per active progress state.
pub const fn overlay_state_text(state: AppState) -> Option<&'static str> {
    match state {
        AppState::Listening => Some("Listening…"),
        AppState::Recording => Some("Recording"),
        AppState::Transcribing => Some("Transcribing…"),
        AppState::Refining => Some("Refining…"),
        AppState::Loading => Some("Preparing…"),
        _ => None,
    }
}

/// Whether the overlay meta zone shows the local 1 Hz recording clock (`m:ss`).
pub const fn is_recording_clock_state(state: AppState) -> bool {
    matches!(state, AppState::Listening | AppState::Recording)
}

/// One overlay frame: bar mode, optional outcome badge, text, meta, and action.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct OverlayContent {
    pub bars: OverlayBarMode,
    pub text: String,
    pub emphasized: bool,
    pub badge: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tone: Option<OverlayTone>,
    pub dismissible: bool,
    pub meta: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub action: Option<OverlayAction>,
}

/// Build a `TRANSCRIBING` payload signaling whether a `REFINING` phase follows.
pub fn transcribing_status(detail: Option<&str>, refine: bool) -> Option<String> {
    if !refine {
        return detail.map(ToOwned::to_owned);
    }
    let base = detail.filter(|s| !s.is_empty()).unwrap_or("Transcribing");
    Some(format!(
        "{}{THEN_REFINING}",
        base.trim_end_matches([' ', '.', '…'])
    ))
}

fn truncate_overlay_text(text: &str, max_chars: usize) -> String {
    if max_chars == 0 || text.chars().count() <= max_chars {
        return text.to_string();
    }
    if max_chars <= 3 {
        return text.chars().take(max_chars).collect();
    }
    let prefix: String = text.chars().take(max_chars - 3).collect();
    format!("{}...", prefix.trim_end())
}

fn ensure_progress_ellipsis(text: &str) -> String {
    let cleaned = text.trim();
    if cleaned.is_empty() {
        return String::new();
    }
    if cleaned.ends_with('…') {
        return cleaned.to_string();
    }
    if let Some(prefix) = cleaned.strip_suffix("...") {
        return format!("{}…", prefix.trim_end());
    }
    format!("{}…", cleaned.trim_end_matches(['.', '!', '?', ' ']))
}

fn loading_text(detail: &str, max_chars: usize) -> String {
    let default_loading = overlay_state_text(AppState::Loading).unwrap_or("Preparing…");
    if detail.is_empty() {
        return default_loading.to_string();
    }
    let mut label = build_daemon_status_label(AppState::Loading, detail, true, max_chars);
    if label.is_empty() {
        label = default_loading.to_string();
    }
    truncate_overlay_text(&ensure_progress_ellipsis(&label), max_chars)
}

fn strip_error_prefix(label: &str) -> &str {
    let bytes = label.as_bytes();
    if bytes.len() < 5 || !bytes[..5].eq_ignore_ascii_case(b"error") {
        return label;
    }
    let mut rest = label[5..].trim_start();
    if let Some(after_sep) = rest.strip_prefix([':', '-', '–', '—']) {
        rest = after_sep.trim_start();
    }
    if rest.is_empty() {
        label
    } else {
        rest
    }
}

fn error_text(detail: &str, max_chars: usize) -> String {
    if detail.is_empty() {
        return "Something went wrong".to_string();
    }
    let raw_label = build_daemon_status_label(AppState::Error, detail, true, max_chars);
    let normalized = normalize_daemon_status_text(Some(&raw_label));
    let label = if !normalized.is_empty()
        && (normalized != detail
            || FRIENDLY_ERROR_LABELS.contains(&normalized.to_lowercase().as_str()))
    {
        normalized
    } else {
        detail.to_string()
    };
    truncate_overlay_text(strip_error_prefix(&label), max_chars)
}

fn is_one_decimal_seconds(token: &str) -> bool {
    let Some((whole, frac)) = token.split_once('.') else {
        return false;
    };
    !whole.is_empty()
        && whole.bytes().all(|b| b.is_ascii_digit())
        && frac.len() == 1
        && frac.as_bytes()[0].is_ascii_digit()
}

fn parse_done_status(detail: &str) -> Option<(&'static str, Option<&str>, Option<&str>)> {
    let (feedback, mut rest) = if let Some(after) = detail.strip_prefix(PASTED) {
        (PASTED, after)
    } else {
        let after = detail.strip_prefix(COPIED)?;
        (COPIED, after)
    };

    let mut seconds = None;
    if let Some(without_unit) = rest.strip_suffix(" s") {
        if let Some((before_sep, secs_candidate)) = without_unit.rsplit_once(" · ") {
            if is_one_decimal_seconds(secs_candidate) {
                seconds = Some(secs_candidate);
                rest = before_sep;
            }
        }
    }

    let app = if rest.is_empty() {
        None
    } else {
        let app_name = rest.strip_prefix(" into ")?;
        if app_name.is_empty() {
            return None;
        }
        Some(app_name)
    };

    Some((feedback, app, seconds))
}

fn done_content(detail: &str) -> OverlayContent {
    if detail == TEST_COMPLETE {
        return OverlayContent {
            text: TEST_COMPLETE.to_string(),
            badge: "Done".to_string(),
            tone: Some(OverlayTone::Success),
            ..Default::default()
        };
    }

    let Some((feedback, app, seconds)) = parse_done_status(detail) else {
        return OverlayContent {
            badge: PASTED_BADGE.to_string(),
            tone: Some(OverlayTone::Success),
            ..Default::default()
        };
    };

    OverlayContent {
        text: app.map(|a| format!("into {a}")).unwrap_or_default(),
        badge: if feedback == COPIED {
            "Copied".to_string()
        } else {
            PASTED_BADGE.to_string()
        },
        tone: Some(OverlayTone::Success),
        meta: seconds.map(|s| format!("{s} s")).unwrap_or_default(),
        ..Default::default()
    }
}

/// Map a daemon state and optional text payload to what the overlay draws.
pub fn describe_overlay_content(
    state: AppState,
    text: Option<&str>,
    max_chars: usize,
) -> OverlayContent {
    let detail = normalize_daemon_status_text(text);

    match state {
        AppState::Recording => OverlayContent {
            bars: OverlayBarMode::Level,
            text: overlay_state_text(AppState::Recording)
                .unwrap_or("Recording")
                .to_string(),
            emphasized: true,
            ..Default::default()
        },
        AppState::Loading => OverlayContent {
            bars: OverlayBarMode::Line,
            text: loading_text(&detail, max_chars),
            ..Default::default()
        },
        AppState::Transcribing if detail.starts_with("Downloading ") => OverlayContent {
            bars: OverlayBarMode::Line,
            text: truncate_overlay_text(&ensure_progress_ellipsis(&detail), max_chars),
            ..Default::default()
        },
        AppState::Transcribing if detail.ends_with(THEN_REFINING) => OverlayContent {
            bars: OverlayBarMode::Phase1,
            text: overlay_state_text(AppState::Transcribing)
                .unwrap_or("Transcribing…")
                .to_string(),
            meta: "1 of 2".to_string(),
            ..Default::default()
        },
        AppState::Refining => OverlayContent {
            bars: OverlayBarMode::Phase2,
            text: overlay_state_text(AppState::Refining)
                .unwrap_or("Refining…")
                .to_string(),
            meta: "2 of 2".to_string(),
            ..Default::default()
        },
        AppState::Listening => OverlayContent {
            bars: OverlayBarMode::Low,
            text: overlay_state_text(AppState::Listening)
                .unwrap_or("Listening…")
                .to_string(),
            ..Default::default()
        },
        AppState::Transcribing => OverlayContent {
            bars: OverlayBarMode::Line,
            text: overlay_state_text(AppState::Transcribing)
                .unwrap_or("Transcribing…")
                .to_string(),
            ..Default::default()
        },
        AppState::Done => done_content(&detail),
        AppState::NoSpeech => OverlayContent {
            text: NO_SPEECH_TEXT.to_string(),
            badge: "No speech".to_string(),
            tone: Some(OverlayTone::Warn),
            ..Default::default()
        },
        AppState::Error => {
            if detail == PASTE_FAILED_MACOS || detail == PASTE_FAILED_OTHER || detail == COPY_FAILED
            {
                return OverlayContent {
                    text: detail,
                    badge: "Not pasted".to_string(),
                    tone: Some(OverlayTone::Warn),
                    dismissible: true,
                    ..Default::default()
                };
            }
            let message = error_text(&detail, max_chars);
            let offers_settings = SETTINGS_ERROR_LABELS.contains(&message.to_lowercase().as_str());
            OverlayContent {
                text: message,
                badge: "Error".to_string(),
                tone: Some(OverlayTone::Error),
                dismissible: true,
                meta: if offers_settings {
                    "Settings".to_string()
                } else {
                    String::new()
                },
                action: offers_settings.then_some(OverlayAction::Settings),
                ..Default::default()
            }
        }
        AppState::Idle => OverlayContent::default(),
    }
}

/// Convenience wrapper accepting raw state strings (e.g. `"RECORDING"`, `"no_speech"`).
pub fn describe_overlay_content_for_str(
    state: Option<&str>,
    text: Option<&str>,
    max_chars: usize,
) -> OverlayContent {
    describe_overlay_content(parse_app_state_or_idle(state), text, max_chars)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn transcribing_and_refining_phases_and_done_outcomes_match_contract() {
        assert_eq!(
            transcribing_status(Some("Deepgram"), false),
            Some("Deepgram".to_string())
        );
        let marked = transcribing_status(Some("Deepgram…"), true).unwrap();
        assert_eq!(marked, "Deepgram, then refining");

        let phase1 = describe_overlay_content(AppState::Transcribing, Some(&marked), 72);
        assert_eq!(phase1.bars, OverlayBarMode::Phase1);
        assert_eq!(phase1.meta, "1 of 2");

        let phase2 = describe_overlay_content(AppState::Refining, None, 72);
        assert_eq!(phase2.bars, OverlayBarMode::Phase2);
        assert_eq!(phase2.meta, "2 of 2");

        let pasted = describe_overlay_content(
            AppState::Done,
            Some("Transcript pasted into Mail · 1.2 s"),
            72,
        );
        assert_eq!(pasted.badge, "Pasted");
        assert_eq!(pasted.text, "into Mail");
        assert_eq!(pasted.meta, "1.2 s");
        assert_eq!(pasted.tone, Some(OverlayTone::Success));
        assert!(!pasted.dismissible);
    }

    #[test]
    fn errors_offer_settings_link_and_handle_delivery_failures() {
        let missing_key =
            describe_overlay_content(AppState::Error, Some("DEEPGRAM_API_KEY missing"), 72);
        assert_eq!(missing_key.badge, "Error");
        assert_eq!(missing_key.text, "Missing API key");
        assert_eq!(missing_key.meta, "Settings");
        assert_eq!(missing_key.action, Some(OverlayAction::Settings));
        assert!(missing_key.dismissible);

        let paste_fail = describe_overlay_content(AppState::Error, Some(PASTE_FAILED_MACOS), 72);
        assert_eq!(paste_fail.badge, "Not pasted");
        assert_eq!(paste_fail.tone, Some(OverlayTone::Warn));
        assert!(paste_fail.dismissible);
        assert_eq!(paste_fail.action, None);

        let prefixed = describe_overlay_content(AppState::Error, Some("Error: custom failure"), 72);
        assert_eq!(prefixed.text, "custom failure");
    }
}
