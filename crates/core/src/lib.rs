//! Platform-independent domain logic and `~/.pulsescribe/` contracts for PulseScribe.
//!
//! This crate intentionally contains no OS or UI bindings so every module can be
//! tested identically on macOS, Windows, and Linux.

pub mod atomic_io;
pub mod env;
pub mod onboarding;
pub mod paths;
pub mod preferences;
pub mod state;

pub use onboarding::{OnboardingChoice, OnboardingStep};
pub use paths::{AppPaths, FileSignature};
pub use preferences::{HotkeyKind, PreferencesError, PreferencesStore};
pub use state::{AppState, DaemonErrorCode, DaemonStatusError};
