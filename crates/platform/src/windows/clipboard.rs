//! Native Windows clipboard (`OpenClipboard` with contention retries and genuine single-attempt
//! `read_text_once`) and `Ctrl+V` shortcut injector (`SendInput`).
//!
//! Mirrors `whisper_platform/clipboard.py::WindowsClipboard` and `utils/hotkey.py`.

use std::ptr;
use std::thread;
use std::time::Duration;

use windows::Win32::Foundation::{GlobalFree, HANDLE, HGLOBAL};
use windows::Win32::System::DataExchange::{
    CloseClipboard, EmptyClipboard, GetClipboardData, IsClipboardFormatAvailable, OpenClipboard,
    SetClipboardData,
};
use windows::Win32::System::Memory::{
    GlobalAlloc, GlobalLock, GlobalSize, GlobalUnlock, GMEM_MOVEABLE,
};
use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetKeyboardLayout, SendInput, VkKeyScanExW, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT,
    KEYBD_EVENT_FLAGS, KEYEVENTF_KEYUP, VIRTUAL_KEY, VK_CONTROL, VK_V,
};
use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, GetWindowThreadProcessId};

use crate::{ClipboardBackend, PasteKeySender, PlatformError};

const CF_UNICODETEXT: u32 = 13;
const CLIPBOARD_OPEN_RETRIES: usize = 5;
const CLIPBOARD_OPEN_RETRY_DELAY: Duration = Duration::from_millis(50);

struct ClipboardGuard;

impl Drop for ClipboardGuard {
    fn drop(&mut self) {
        #[allow(unsafe_code)]
        // SAFETY: `ClipboardGuard` is only constructed immediately after `OpenClipboard` succeeds.
        unsafe {
            let _ = CloseClipboard();
        }
    }
}

struct GlobalLockGuard(HGLOBAL);

impl Drop for GlobalLockGuard {
    fn drop(&mut self) {
        #[allow(unsafe_code)]
        // SAFETY: `GlobalLockGuard` is only constructed after a non-null `GlobalLock`.
        unsafe {
            let _ = GlobalUnlock(self.0);
        }
    }
}

/// Frees an `HGLOBAL` unless ownership has been transferred to Windows via `SetClipboardData`.
struct OwnedHGlobal(Option<HGLOBAL>);

impl OwnedHGlobal {
    fn disarm(&mut self) {
        self.0 = None;
    }
}

impl Drop for OwnedHGlobal {
    fn drop(&mut self) {
        if let Some(hmem) = self.0.take() {
            #[allow(unsafe_code)]
            // SAFETY: Ownership was never transferred to Windows, so we must free `hmem`.
            unsafe {
                let _ = GlobalFree(Some(hmem));
            }
        }
    }
}

fn open_clipboard(attempts: usize) -> Result<ClipboardGuard, PlatformError> {
    let attempts = attempts.max(1);
    for attempt in 0..attempts {
        #[allow(unsafe_code)]
        // SAFETY: Passing `None` opens the clipboard associated with the current task.
        if unsafe { OpenClipboard(None) }.is_ok() {
            return Ok(ClipboardGuard);
        }
        if attempt + 1 < attempts {
            thread::sleep(CLIPBOARD_OPEN_RETRY_DELAY);
        }
    }
    Err(PlatformError::Clipboard(
        "OpenClipboard failed after retries".into(),
    ))
}

/// Native Win32 [`ClipboardBackend`] with lock-contention retries and single-attempt `read_text_once`.
#[derive(Debug, Default, Clone, Copy)]
pub struct WindowsClipboard;

impl WindowsClipboard {
    fn read_text_with_attempts(attempts: usize) -> Result<Option<String>, PlatformError> {
        let _guard = open_clipboard(attempts)?;
        #[allow(unsafe_code)]
        // SAFETY: Clipboard is open via `_guard`; missing `CF_UNICODETEXT` means non-text/empty.
        if unsafe { IsClipboardFormatAvailable(CF_UNICODETEXT) }.is_err() {
            return Ok(None);
        }

        #[allow(unsafe_code)]
        // SAFETY: Clipboard is open via `_guard`; handle remains valid until `CloseClipboard`.
        let handle = match unsafe { GetClipboardData(CF_UNICODETEXT) } {
            Ok(h) if !h.is_invalid() => h,
            _ => return Ok(None),
        };
        let hglobal = HGLOBAL(handle.0);
        #[allow(unsafe_code)]
        // SAFETY: `hglobal` is a valid clipboard global memory handle while clipboard is open.
        let byte_size = unsafe { GlobalSize(hglobal) };
        if byte_size < 2 {
            return Ok(None);
        }

        #[allow(unsafe_code)]
        // SAFETY: `hglobal` is valid while `_guard` holds the clipboard open.
        let ptr = unsafe { GlobalLock(hglobal) };
        if ptr.is_null() {
            return Err(PlatformError::Clipboard("GlobalLock failed".into()));
        }
        let _lock = GlobalLockGuard(hglobal);

        let max_units = byte_size / 2;
        let words = ptr.cast::<u16>();
        let mut units = Vec::with_capacity(max_units.min(1024));
        for idx in 0..max_units {
            #[allow(unsafe_code)]
            // SAFETY: `idx < byte_size / 2`, bounded by `GlobalSize(hglobal)`.
            let unit = unsafe { ptr::read_unaligned(words.add(idx)) };
            if unit == 0 {
                break;
            }
            units.push(unit);
        }
        Ok(Some(String::from_utf16_lossy(&units)))
    }
}

impl ClipboardBackend for WindowsClipboard {
    fn write_text(&self, text: &str) -> Result<(), PlatformError> {
        let wide: Vec<u16> = text.encode_utf16().chain(std::iter::once(0)).collect();
        let byte_len = wide
            .len()
            .checked_mul(std::mem::size_of::<u16>())
            .ok_or_else(|| PlatformError::Clipboard("clipboard text size overflow".into()))?;

        // Prepare memory before opening the clipboard so allocation errors never empty it.
        #[allow(unsafe_code)]
        // SAFETY: `GlobalAlloc` returns an owned `HGLOBAL` wrapped in `OwnedHGlobal`.
        let hmem = unsafe { GlobalAlloc(GMEM_MOVEABLE, byte_len) }
            .map_err(|err| PlatformError::Clipboard(format!("GlobalAlloc failed: {err}")))?;
        let mut owned = OwnedHGlobal(Some(hmem));

        {
            #[allow(unsafe_code)]
            // SAFETY: `hmem` is validly allocated for `byte_len` bytes.
            let dst = unsafe { GlobalLock(hmem) };
            if dst.is_null() {
                return Err(PlatformError::Clipboard("GlobalLock failed".into()));
            }
            let _lock = GlobalLockGuard(hmem);
            #[allow(unsafe_code)]
            // SAFETY: `dst` has `byte_len` writable bytes; `wide` has `byte_len` readable bytes
            // and does not overlap with the freshly allocated global buffer.
            unsafe {
                ptr::copy_nonoverlapping(wide.as_ptr().cast::<u8>(), dst.cast::<u8>(), byte_len);
            }
        }

        let _clipboard = open_clipboard(CLIPBOARD_OPEN_RETRIES)?;
        #[allow(unsafe_code)]
        // SAFETY: Clipboard is open via `_clipboard`.
        unsafe { EmptyClipboard() }
            .map_err(|err| PlatformError::Clipboard(format!("EmptyClipboard failed: {err}")))?;

        #[allow(unsafe_code)]
        // SAFETY: `hmem` is unlocked, movable, NUL-terminated UTF-16, and owned by us until
        // `SetClipboardData` succeeds (after which Windows takes ownership).
        unsafe { SetClipboardData(CF_UNICODETEXT, Some(HANDLE(hmem.0))) }
            .map_err(|err| PlatformError::Clipboard(format!("SetClipboardData failed: {err}")))?;
        owned.disarm();
        Ok(())
    }

    fn read_text(&self) -> Result<Option<String>, PlatformError> {
        Self::read_text_with_attempts(CLIPBOARD_OPEN_RETRIES)
    }

    fn read_text_once(&self) -> Result<Option<String>, PlatformError> {
        Self::read_text_with_attempts(1)
    }
}

fn resolve_paste_vk() -> VIRTUAL_KEY {
    #[allow(unsafe_code)]
    // SAFETY: `GetForegroundWindow`, `GetWindowThreadProcessId`, `GetKeyboardLayout`, and
    // `VkKeyScanExW` are pure Win32 queries with no memory ownership requirements.
    unsafe {
        let fg_tid = {
            let hwnd = GetForegroundWindow();
            if hwnd.0.is_null() {
                0
            } else {
                GetWindowThreadProcessId(hwnd, None)
            }
        };
        let layout = GetKeyboardLayout(fg_tid);
        let scan = VkKeyScanExW(u16::from(b'v'), layout);
        if scan != -1 && ((scan >> 8) & 0xFF) == 0 {
            VIRTUAL_KEY((scan & 0xFF) as u16)
        } else {
            VK_V
        }
    }
}

fn keyboard_input(vk: VIRTUAL_KEY, flags: KEYBD_EVENT_FLAGS) -> INPUT {
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: vk,
                wScan: 0,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}

/// Emits `Ctrl+V` atomically on Windows via `SendInput`.
#[derive(Debug, Default, Clone, Copy)]
pub struct WindowsPasteKeySender;

impl PasteKeySender for WindowsPasteKeySender {
    fn send_paste_shortcut(&self) -> Result<(), PlatformError> {
        let vk_v = resolve_paste_vk();
        let inputs = [
            keyboard_input(VK_CONTROL, KEYBD_EVENT_FLAGS(0)),
            keyboard_input(vk_v, KEYBD_EVENT_FLAGS(0)),
            keyboard_input(vk_v, KEYEVENTF_KEYUP),
            keyboard_input(VK_CONTROL, KEYEVENTF_KEYUP),
        ];
        #[allow(unsafe_code)]
        // SAFETY: `inputs` is a valid contiguous slice of `INPUT` structs sized by `size_of::<INPUT>()`.
        let sent = unsafe { SendInput(&inputs, std::mem::size_of::<INPUT>() as i32) };
        if sent as usize == inputs.len() {
            Ok(())
        } else {
            Err(PlatformError::Paste(format!(
                "SendInput injected {sent}/{} events (blocked by UIPI or desktop lock)",
                inputs.len()
            )))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn windows_clipboard_read_and_vk_resolution_work_live() {
        let clipboard = WindowsClipboard;
        // Tolerate transient external clipboard contention on shared runners while verifying
        // neither `read_text` nor `read_text_once` returns `Unavailable`.
        assert!(matches!(
            clipboard.read_text(),
            Ok(_) | Err(PlatformError::Clipboard(_))
        ));
        assert!(matches!(
            clipboard.read_text_once(),
            Ok(_) | Err(PlatformError::Clipboard(_))
        ));
        assert_ne!(resolve_paste_vk().0, 0);
    }
}
