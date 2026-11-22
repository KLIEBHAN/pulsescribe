//! Clipboard text access, paste-shortcut boundaries, and restore timing contracts.
//!
//! Mirrors `whisper_platform/clipboard.py` and the timing defaults from `utils/hotkey.py`.

use std::time::Duration;

use crate::{PlatformError, PlatformKind};

/// Default grace period before restoring the previous clipboard on macOS (`1.0 s`).
pub const MACOS_CLIPBOARD_RESTORE_DELAY: Duration = Duration::from_secs(1);
/// Default grace period before restoring the previous clipboard on Windows (`0.5 s`).
pub const WINDOWS_CLIPBOARD_RESTORE_DELAY: Duration = Duration::from_millis(500);
/// Poll step for confirming clipboard visibility before sending Ctrl+V on Windows (`5 ms`).
pub const WINDOWS_CLIPBOARD_SYNC_POLL: Duration = Duration::from_millis(5);

/// OS clipboard text access boundary.
pub trait ClipboardBackend: Send + Sync {
    /// Writes UTF-8 text into the system clipboard.
    fn write_text(&self, text: &str) -> Result<(), PlatformError>;

    /// Reads UTF-8 text from the system clipboard (`Ok(None)` when empty or non-text).
    fn read_text(&self) -> Result<Option<String>, PlatformError>;

    /// Single-attempt read without internal open-retry sleeps for the Windows paste-sync loop.
    fn read_text_once(&self) -> Result<Option<String>, PlatformError> {
        self.read_text()
    }
}

/// OS shortcut injector boundary (`Cmd+V` on macOS, `Ctrl+V` on Windows).
pub trait PasteKeySender: Send + Sync {
    /// Emits the platform paste shortcut to the currently focused window.
    fn send_paste_shortcut(&self) -> Result<(), PlatformError>;
}

/// Cross-platform [`ClipboardBackend`] powered by `arboard`.
///
/// Creates a transient `arboard::Clipboard` per call so the adapter stays `Send + Sync`
/// on every target. macOS and Windows retain clipboard contents after the handle drops;
/// Linux X11/Wayland requires a dedicated persistent clipboard owner backend.
#[derive(Debug, Default, Clone, Copy)]
pub struct ArboardClipboard;

impl ClipboardBackend for ArboardClipboard {
    fn write_text(&self, text: &str) -> Result<(), PlatformError> {
        let mut clipboard =
            arboard::Clipboard::new().map_err(|err| PlatformError::Clipboard(err.to_string()))?;
        clipboard
            .set_text(text.to_owned())
            .map_err(|err| PlatformError::Clipboard(err.to_string()))
    }

    fn read_text(&self) -> Result<Option<String>, PlatformError> {
        let mut clipboard =
            arboard::Clipboard::new().map_err(|err| PlatformError::Clipboard(err.to_string()))?;
        match clipboard.get_text() {
            Ok(text) => Ok(Some(text)),
            Err(arboard::Error::ContentNotAvailable) => Ok(None),
            Err(err) => Err(PlatformError::Clipboard(err.to_string())),
        }
    }
}

/// Timing and restoration policy for transcript paste adapters.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ClipboardPastePolicy {
    pub restore_clipboard: bool,
    pub restore_delay: Duration,
    pub paste_sync_timeout: Duration,
    pub paste_sync_poll: Duration,
}

impl ClipboardPastePolicy {
    /// Platform-default policy matching `utils/hotkey.py`.
    pub const fn for_platform(platform: PlatformKind, restore_clipboard: bool) -> Self {
        let restore_delay = match platform {
            PlatformKind::MacOs => MACOS_CLIPBOARD_RESTORE_DELAY,
            PlatformKind::Windows | PlatformKind::Linux | PlatformKind::Other => {
                WINDOWS_CLIPBOARD_RESTORE_DELAY
            }
        };
        Self {
            restore_clipboard,
            restore_delay,
            paste_sync_timeout: Duration::ZERO,
            paste_sync_poll: WINDOWS_CLIPBOARD_SYNC_POLL,
        }
    }
}

impl Default for ClipboardPastePolicy {
    fn default() -> Self {
        Self::for_platform(PlatformKind::current(), false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn paste_policy_defaults_match_platform_timings() {
        let mac = ClipboardPastePolicy::for_platform(PlatformKind::MacOs, true);
        assert!(mac.restore_clipboard);
        assert_eq!(mac.restore_delay, Duration::from_secs(1));
        assert_eq!(mac.paste_sync_timeout, Duration::ZERO);

        let win = ClipboardPastePolicy::for_platform(PlatformKind::Windows, false);
        assert!(!win.restore_clipboard);
        assert_eq!(win.restore_delay, Duration::from_millis(500));
        assert_eq!(win.paste_sync_poll, Duration::from_millis(5));
    }
}
