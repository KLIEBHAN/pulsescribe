//! Platform-independent domain logic and `~/.pulsescribe/` contracts for PulseScribe.
//!
//! This crate intentionally contains no OS or UI bindings so every module can be
//! tested identically on macOS, Windows, and Linux.

pub mod state;

pub use state::{AppState, DaemonErrorCode, DaemonStatusError};
