//! Platform-independent domain logic and `~/.pulsescribe/` contracts for PulseScribe.
//!
//! This crate intentionally contains no OS or UI bindings so every module can be
//! tested identically on macOS, Windows, and Linux.

pub mod animation;
pub mod atomic_io;
pub mod daemon_status_feedback;
pub mod env;
pub mod history;
pub mod hold_state;
pub mod hotkey;
pub mod onboarding;
pub mod overlay_feedback;
pub mod overlay_style;
pub mod overlay_text;
pub mod paths;
pub mod preferences;
pub mod prompts;
pub mod state;
pub mod transcript_delivery;
pub mod vocabulary;
pub mod vocabulary_correction;

pub use animation::AnimationLogic;
pub use daemon_status_feedback::StatusDetail;
pub use history::{HistoryError, HistorySaveOutcome, HistoryStore, TranscriptEntry};
pub use hold_state::HoldHotkeyState;
pub use hotkey::{
    CarbonHotkey, HotkeyParseError, HotkeyValidationContext, HotkeyValidationLevel,
    HotkeyValidationResult,
};
pub use onboarding::{OnboardingChoice, OnboardingStep};
pub use overlay_feedback::OverlayContent;
pub use overlay_style::{
    OverlayAction, OverlayBarMode, OverlayLayout, OverlayLayoutParams, OverlayTone, Rgb,
    ShimmerTrack,
};
pub use overlay_text::InterimLines;
pub use paths::{AppPaths, FileSignature};
pub use preferences::{HotkeyKind, PreferencesError, PreferencesStore};
pub use prompts::{
    PromptEditorContext, PromptOverrides, PromptsConfig, PromptsError, PromptsStore,
};
pub use state::{AppState, DaemonErrorCode, DaemonMessage, DaemonStatusError, MessageType};
pub use transcript_delivery::{DeliveryOutcome, TranscriptClipboardBackend, TranscriptDelivery};
pub use vocabulary::{VocabularyError, VocabularyInputAnalysis, VocabularyState, VocabularyStore};
pub use vocabulary_correction::apply_vocabulary_corrections;
