//! Transcript delivery outcomes, status formatting, and last-transcript recovery.
//!
//! Mirrors `utils/transcript_delivery.py` while keeping clipboard/paste OS actions
//! behind the `TranscriptClipboardBackend` trait boundary.

use serde::{Deserialize, Serialize};

use crate::preferences::PreferencesStore;

pub const PASTED: &str = "Transcript pasted";
pub const COPIED: &str = "Copied to clipboard";
/// DONE payload of onboarding test runs (nothing pasted).
pub const TEST_COMPLETE: &str = "Test complete";
pub const PASTE_FAILED_MACOS: &str = "Copied — press ⌘V";
pub const PASTE_FAILED_OTHER: &str = "Copied — press Ctrl+V";
#[cfg(target_os = "macos")]
pub const PASTE_FAILED: &str = PASTE_FAILED_MACOS;
#[cfg(not(target_os = "macos"))]
pub const PASTE_FAILED: &str = PASTE_FAILED_OTHER;
pub const COPY_FAILED: &str = "Copy failed — use Copy Last Transcript";

pub const PASTE_FAILED_HINT: &str =
    "Paste manually, or choose Copy Last Transcript to copy it again.";
pub const COPY_FAILED_HINT: &str = "Choose Copy Last Transcript in the menu to try copying again.";

pub const NO_TRANSCRIPT_TO_COPY: &str = "No transcript to copy yet.";
pub const LAST_TRANSCRIPT_COPIED: &str = "Last transcript copied.";
pub const COULD_NOT_COPY: &str = "Could not copy. Try again.";

/// Platform-appropriate paste-failed message (`⌘V` on macOS, `Ctrl+V` elsewhere).
pub const fn paste_failed_message(is_macos: bool) -> &'static str {
    if is_macos {
        PASTE_FAILED_MACOS
    } else {
        PASTE_FAILED_OTHER
    }
}

/// Optional recovery hint shown beside a delivery failure status.
pub fn delivery_hint(feedback: &str) -> Option<&'static str> {
    if feedback == PASTE_FAILED_MACOS || feedback == PASTE_FAILED_OTHER {
        Some(PASTE_FAILED_HINT)
    } else if feedback == COPY_FAILED {
        Some(COPY_FAILED_HINT)
    } else {
        None
    }
}

/// Format the DONE payload combining delivery feedback, target app (only for pastes),
/// and optional non-negative transcription duration (e.g. `"Transcript pasted into Mail · 1.2 s"`).
pub fn done_status(feedback: &str, app: Option<&str>, seconds: Option<f64>) -> String {
    let mut text = String::from(feedback);
    if let Some(app_name) = app.filter(|s| !s.is_empty()) {
        if feedback == PASTED {
            text.push_str(" into ");
            text.push_str(app_name);
        }
    }
    if let Some(secs) = seconds.filter(|s| s.is_finite() && *s >= 0.0) {
        let normalized = if secs == 0.0 { 0.0 } else { secs };
        text.push_str(&format!(" · {normalized:.1} s"));
    }
    text
}

/// Evaluate whether `PULSESCRIBE_SHOW_RTF` enables showing transcription duration.
///
/// Mirrors `utils/transcript_delivery.py:transcription_time_enabled` (`value.lower() in ("true", "1", "yes", "on")`).
pub fn transcription_time_enabled_from_value(value: Option<&str>) -> bool {
    value.is_some_and(|raw| {
        matches!(
            raw.to_ascii_lowercase().as_str(),
            "true" | "1" | "yes" | "on"
        )
    })
}

/// Read `PULSESCRIBE_SHOW_RTF` from `PreferencesStore` and return whether DONE shows duration.
pub fn transcription_time_enabled(store: &PreferencesStore) -> bool {
    let raw = store.get_env_setting("PULSESCRIBE_SHOW_RTF");
    transcription_time_enabled_from_value(raw.as_deref())
}

/// OS adapter boundary for pasting and copying transcripts.
pub trait TranscriptClipboardBackend {
    fn paste_transcript(&mut self, text: &str) -> bool;
    fn copy_transcript(&mut self, text: &str) -> bool;
}

/// Result of `TranscriptDelivery::deliver`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DeliveryOutcome {
    pub ok: bool,
    pub feedback: String,
}

/// Session-only tracker for the last normal dictation and its clipboard recovery.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TranscriptDelivery {
    text: String,
    is_macos: bool,
}

impl Default for TranscriptDelivery {
    fn default() -> Self {
        Self::new()
    }
}

impl TranscriptDelivery {
    pub fn new() -> Self {
        Self::for_platform(cfg!(target_os = "macos"))
    }

    pub fn for_platform(is_macos: bool) -> Self {
        Self {
            text: String::new(),
            is_macos,
        }
    }

    /// Last transcript handed to `deliver`, kept independent of clipboard restoration.
    pub fn last_text(&self) -> &str {
        &self.text
    }

    /// Store `text` and attempt auto-paste (with clipboard fallback) or copy-only delivery.
    pub fn deliver<B: TranscriptClipboardBackend>(
        &mut self,
        text: &str,
        auto_paste: bool,
        backend: &mut B,
    ) -> DeliveryOutcome {
        self.text = text.to_string();
        if auto_paste && backend.paste_transcript(text) {
            return DeliveryOutcome {
                ok: true,
                feedback: PASTED.to_string(),
            };
        }
        if backend.copy_transcript(text) {
            return if auto_paste {
                DeliveryOutcome {
                    ok: false,
                    feedback: paste_failed_message(self.is_macos).to_string(),
                }
            } else {
                DeliveryOutcome {
                    ok: true,
                    feedback: COPIED.to_string(),
                }
            };
        }
        DeliveryOutcome {
            ok: false,
            feedback: COPY_FAILED.to_string(),
        }
    }

    /// Explicitly copy the last stored transcript without auto-paste.
    pub fn copy_last<B: TranscriptClipboardBackend>(&self, backend: &mut B) -> &'static str {
        if self.text.is_empty() {
            return NO_TRANSCRIPT_TO_COPY;
        }
        if backend.copy_transcript(&self.text) {
            LAST_TRANSCRIPT_COPIED
        } else {
            COULD_NOT_COPY
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct FakeBackend {
        paste_ok: bool,
        copy_ok: bool,
        pasted: Vec<String>,
        copied: Vec<String>,
    }

    impl TranscriptClipboardBackend for FakeBackend {
        fn paste_transcript(&mut self, text: &str) -> bool {
            self.pasted.push(text.to_string());
            self.paste_ok
        }

        fn copy_transcript(&mut self, text: &str) -> bool {
            self.copied.push(text.to_string());
            self.copy_ok
        }
    }

    #[test]
    fn done_status_appends_app_only_for_paste_and_handles_durations() {
        assert_eq!(
            done_status(PASTED, Some("Mail"), Some(1.24)),
            "Transcript pasted into Mail · 1.2 s"
        );
        assert_eq!(
            done_status(COPIED, Some("Mail"), Some(1.25)),
            "Copied to clipboard · 1.2 s"
        );
        assert_eq!(
            done_status(TEST_COMPLETE, Some("Mail"), Some(-0.1)),
            "Test complete"
        );
        assert_eq!(
            done_status(PASTED, None, Some(f64::NAN)),
            "Transcript pasted"
        );
        assert_eq!(
            done_status(PASTED, None, Some(f64::INFINITY)),
            "Transcript pasted"
        );
        assert_eq!(
            done_status(PASTED, None, Some(-0.0)),
            "Transcript pasted · 0.0 s"
        );
    }

    #[test]
    fn delivery_falls_back_to_copy_and_retains_last_text() {
        let mut delivery = TranscriptDelivery::for_platform(true);
        let mut backend = FakeBackend {
            paste_ok: false,
            copy_ok: true,
            pasted: Vec::new(),
            copied: Vec::new(),
        };

        assert_eq!(delivery.copy_last(&mut backend), NO_TRANSCRIPT_TO_COPY);

        let outcome = delivery.deliver("Hello", true, &mut backend);
        assert!(!outcome.ok);
        assert_eq!(outcome.feedback, PASTE_FAILED_MACOS);
        assert_eq!(delivery_hint(&outcome.feedback), Some(PASTE_FAILED_HINT));
        assert_eq!(delivery.last_text(), "Hello");
        assert_eq!(delivery.copy_last(&mut backend), LAST_TRANSCRIPT_COPIED);

        backend.copy_ok = false;
        let failed = delivery.deliver("World", false, &mut backend);
        assert!(!failed.ok);
        assert_eq!(failed.feedback, COPY_FAILED);
        assert_eq!(delivery_hint(&failed.feedback), Some(COPY_FAILED_HINT));
        assert_eq!(delivery.copy_last(&mut backend), COULD_NOT_COPY);
    }
}
