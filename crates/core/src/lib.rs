//! Platform-independent domain logic and `~/.pulsescribe/` contracts for PulseScribe.
//!
//! This crate intentionally contains no OS or UI bindings so every module can be
//! tested identically on macOS, Windows, and Linux.

pub mod atomic_io;
pub mod env;
pub mod history;
pub mod onboarding;
pub mod paths;
pub mod preferences;
pub mod prompts;
pub mod state;
pub mod vocabulary;

pub use history::{HistoryError, HistoryStore, TranscriptEntry};
pub use onboarding::{OnboardingChoice, OnboardingStep};
pub use paths::{AppPaths, FileSignature};
pub use preferences::{HotkeyKind, PreferencesError, PreferencesStore};
pub use prompts::{
    PromptEditorContext, PromptOverrides, PromptsConfig, PromptsError, PromptsStore,
};
pub use state::{AppState, DaemonErrorCode, DaemonStatusError};
pub use vocabulary::{VocabularyError, VocabularyInputAnalysis, VocabularyState, VocabularyStore};
