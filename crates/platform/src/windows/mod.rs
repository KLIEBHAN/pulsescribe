//! Native Windows adapters for `pulsescribe-platform`.
//!
//! All `unsafe` Win32 FFI calls are confined to this module tree behind safe trait implementations.

pub mod app_detection;
pub mod clipboard;
pub mod hotkey;
pub mod sound;

pub use app_detection::WindowsAppDetector;
pub use clipboard::{WindowsClipboard, WindowsPasteKeySender};
pub use hotkey::WindowsHotkeyBackend;
pub use sound::WindowsSoundPlayer;

/// Windows has no macOS-style TCC permission prompts; reuse [`crate::DefaultPermissionsChecker`].
pub type WindowsPermissionsChecker = crate::DefaultPermissionsChecker;
