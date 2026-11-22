//! OS abstraction traits and pure platform helpers for PulseScribe.
//!
//! Covers the five platform capabilities from ADR-003 (`Sound`, `Clipboard/Paste`,
//! `AppDetector`, `Permissions`, `Hotkey`) so `crates/daemon` stays platform-agnostic
//! while native macOS and Windows adapters live behind OS boundaries.

pub mod app_detection;
pub mod clipboard;
pub mod hotkey;
#[cfg(target_os = "macos")]
pub mod macos;
pub mod permissions;
pub mod sound;

use std::sync::{Mutex, MutexGuard};

use thiserror::Error;

pub use app_detection::{
    format_translation_code, strip_exe_suffix, AppDetector, NoopAppDetector, VERSION_FALLBACK_CODES,
};
pub use clipboard::{
    ArboardClipboard, ClipboardBackend, ClipboardPastePolicy, PasteKeySender,
    TranscriptPasteAdapter, MACOS_CLIPBOARD_RESTORE_DELAY, WINDOWS_CLIPBOARD_RESTORE_DELAY,
    WINDOWS_CLIPBOARD_SYNC_POLL,
};
pub use hotkey::{
    HotkeyBackend, HotkeyBinding, HotkeyCallback, HotkeyEvent, HotkeyRegistration, HotkeySource,
};
pub use permissions::{
    is_permission_related_message, CachedPermissionsChecker, DefaultPermissionsChecker,
    MicrophonePermissionStatus, PermissionKind, PermissionSnapshot, PermissionsChecker,
    PERMISSION_SNAPSHOT_TTL,
};
pub use sound::{
    encode_ready_cue_wav, synthesize_ready_cue_samples, NoopSoundPlayer, SoundName, SoundPlayer,
    READY_CUE_AMPLITUDE, READY_CUE_ATTACK_SEC, READY_CUE_DECAY_TAU_SEC, READY_CUE_DURATION_SEC,
    READY_CUE_FILENAME, READY_CUE_FREQ_HZ, READY_CUE_SAMPLE_RATE,
};

pub(crate) fn lock_unpoisoned<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|err| err.into_inner())
}

/// Operating system family recognized by PulseScribe's platform layer.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum PlatformKind {
    MacOs,
    Windows,
    Linux,
    Other,
}

impl PlatformKind {
    /// Returns the compile-time target OS family.
    pub const fn current() -> Self {
        if cfg!(target_os = "macos") {
            Self::MacOs
        } else if cfg!(target_os = "windows") {
            Self::Windows
        } else if cfg!(target_os = "linux") {
            Self::Linux
        } else {
            Self::Other
        }
    }

    pub const fn is_macos(self) -> bool {
        matches!(self, Self::MacOs)
    }

    pub const fn is_windows(self) -> bool {
        matches!(self, Self::Windows)
    }

    /// Shortcut hint shown to the user when auto-paste falls back to manual paste.
    pub const fn paste_shortcut_hint(self) -> &'static str {
        match self {
            Self::MacOs => "⌘V",
            Self::Windows | Self::Linux | Self::Other => "Ctrl+V",
        }
    }
}

/// Unified error type returned by OS adapters in `pulsescribe-platform`.
#[derive(Debug, Error)]
pub enum PlatformError {
    #[error("platform capability unavailable: {0}")]
    Unavailable(&'static str),
    #[error("permission denied: {0:?}")]
    PermissionDenied(PermissionKind),
    #[error("clipboard error: {0}")]
    Clipboard(String),
    #[error("paste error: {0}")]
    Paste(String),
    #[error("hotkey error: {0}")]
    Hotkey(String),
    #[error("sound error: {0}")]
    Sound(String),
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn platform_kind_hints_match_os_conventions() {
        assert_eq!(PlatformKind::MacOs.paste_shortcut_hint(), "⌘V");
        assert_eq!(PlatformKind::Windows.paste_shortcut_hint(), "Ctrl+V");
        assert_eq!(PlatformKind::Linux.paste_shortcut_hint(), "Ctrl+V");
        assert!(PlatformKind::MacOs.is_macos());
        assert!(PlatformKind::Windows.is_windows());
    }
}
