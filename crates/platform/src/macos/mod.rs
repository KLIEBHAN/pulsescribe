//! Native macOS adapters for `pulsescribe-platform`.
//!
//! All `unsafe` FFI calls are confined to this module tree behind safe trait implementations.

pub mod app_detection;
pub mod clipboard;
pub mod hotkey;
pub mod permissions;
pub mod sound;

pub use app_detection::MacOsAppDetector;
pub use clipboard::{MacOsClipboard, MacOsPasteKeySender};
pub use hotkey::MacOsHotkeyBackend;
pub use permissions::MacOsPermissionsChecker;
pub use sound::MacOsSoundPlayer;
