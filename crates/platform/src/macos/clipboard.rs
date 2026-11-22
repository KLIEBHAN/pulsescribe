//! Native macOS clipboard (`NSPasteboard` + `pbcopy` fallback) and `Cmd+V` injector
//! (`CGEventPost` + `osascript` fallback).
//!
//! Mirrors `utils/hotkey.py` and `whisper_platform/clipboard.py`.

use std::io::Write;
use std::process::{Command, Stdio};

use objc2::rc::autoreleasepool;
use objc2_app_kit::{NSPasteboard, NSPasteboardTypeString};
use objc2_core_graphics::{CGEvent, CGEventFlags, CGEventTapLocation};
use objc2_foundation::NSString;

use crate::macos::permissions::MacOsPermissionsChecker;
use crate::{ClipboardBackend, PasteKeySender, PermissionKind, PlatformError};

/// ANSI virtual key code for `'v'` (`kVK_ANSI_V`).
const KVK_ANSI_V: u16 = 9;

/// In-process macOS clipboard via `NSPasteboard` with UTF-8 `pbcopy` fallback.
#[derive(Debug, Default, Clone, Copy)]
pub struct MacOsClipboard;

impl MacOsClipboard {
    fn write_via_pbcopy(text: &str) -> Result<(), PlatformError> {
        let mut child = Command::new("pbcopy")
            .env("LANG", "en_US.UTF-8")
            .env("LC_ALL", "en_US.UTF-8")
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|err| PlatformError::Clipboard(format!("failed to spawn pbcopy: {err}")))?;

        if let Some(mut stdin) = child.stdin.take() {
            stdin
                .write_all(text.as_bytes())
                .map_err(|err| PlatformError::Clipboard(format!("pbcopy stdin failed: {err}")))?;
        }
        let output = child
            .wait_with_output()
            .map_err(|err| PlatformError::Clipboard(format!("pbcopy wait failed: {err}")))?;
        if output.status.success() {
            Ok(())
        } else {
            let stderr = String::from_utf8_lossy(&output.stderr);
            Err(PlatformError::Clipboard(format!(
                "pbcopy exited with {}: {}",
                output.status,
                stderr.trim()
            )))
        }
    }
}

impl ClipboardBackend for MacOsClipboard {
    fn write_text(&self, text: &str) -> Result<(), PlatformError> {
        let copied = autoreleasepool(|_| {
            let pb = NSPasteboard::generalPasteboard();
            let _ = pb.clearContents();
            let ns_text = NSString::from_str(text);
            #[allow(unsafe_code)]
            // SAFETY: `NSPasteboardTypeString` is an immutable AppKit string constant.
            let string_type = unsafe { NSPasteboardTypeString };
            pb.setString_forType(&ns_text, string_type)
        });
        if copied {
            return Ok(());
        }
        tracing::debug!("NSPasteboard setString failed; falling back to pbcopy");
        Self::write_via_pbcopy(text)
    }

    fn read_text(&self) -> Result<Option<String>, PlatformError> {
        Ok(autoreleasepool(|_| {
            let pb = NSPasteboard::generalPasteboard();
            #[allow(unsafe_code)]
            // SAFETY: `NSPasteboardTypeString` is an immutable AppKit string constant.
            let string_type = unsafe { NSPasteboardTypeString };
            pb.stringForType(string_type).map(|s| s.to_string())
        }))
    }
}

/// Emits `Cmd+V` on macOS via Quartz `CGEventPost`, falling back to `osascript`.
#[derive(Debug, Default, Clone, Copy)]
pub struct MacOsPasteKeySender;

impl MacOsPasteKeySender {
    fn paste_via_quartz() -> Result<(), PlatformError> {
        let down = CGEvent::new_keyboard_event(None, KVK_ANSI_V, true).ok_or_else(|| {
            PlatformError::Paste("CGEventCreateKeyboardEvent(down) failed".into())
        })?;
        let up = CGEvent::new_keyboard_event(None, KVK_ANSI_V, false)
            .ok_or_else(|| PlatformError::Paste("CGEventCreateKeyboardEvent(up) failed".into()))?;
        CGEvent::set_flags(Some(&down), CGEventFlags::MaskCommand);
        CGEvent::set_flags(Some(&up), CGEventFlags::MaskCommand);
        CGEvent::post(CGEventTapLocation::HIDEventTap, Some(&down));
        CGEvent::post(CGEventTapLocation::HIDEventTap, Some(&up));
        Ok(())
    }

    fn paste_via_osascript() -> Result<(), PlatformError> {
        let output = Command::new("osascript")
            .args([
                "-e",
                r#"tell application "System Events" to keystroke "v" using command down"#,
            ])
            .stdin(Stdio::null())
            .output()
            .map_err(|err| PlatformError::Paste(format!("osascript failed to spawn: {err}")))?;
        if output.status.success() {
            Ok(())
        } else {
            let stderr = String::from_utf8_lossy(&output.stderr);
            Err(PlatformError::Paste(format!(
                "osascript failed: {}",
                stderr.trim()
            )))
        }
    }
}

impl PasteKeySender for MacOsPasteKeySender {
    fn send_paste_shortcut(&self) -> Result<(), PlatformError> {
        let trusted = MacOsPermissionsChecker::is_accessibility_trusted();
        if trusted && Self::paste_via_quartz().is_ok() {
            return Ok(());
        }
        Self::paste_via_osascript().map_err(|err| {
            if trusted {
                err
            } else {
                PlatformError::PermissionDenied(PermissionKind::Accessibility)
            }
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nspasteboard_read_succeeds_without_modifying_clipboard() {
        let clipboard = MacOsClipboard;
        let _ = clipboard
            .read_text()
            .expect("NSPasteboard read must succeed");
    }
}
