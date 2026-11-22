//! Native macOS clipboard (`NSPasteboard` + `pbcopy` fallback) and layout-aware `Cmd+V` injector
//! (`CGEventPost` + `osascript` fallback).
//!
//! Mirrors `utils/hotkey.py` and `whisper_platform/clipboard.py`.

use std::ffi::{c_int, c_void};
use std::io::Write;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::time::Duration;

use objc2::rc::autoreleasepool;
use objc2_app_kit::{NSPasteboard, NSPasteboardTypeString};
use objc2_core_graphics::{CGEvent, CGEventFlags, CGEventTapLocation};
use objc2_foundation::NSString;
use pulsescribe_core::hotkey::CARBON_CMD_KEY;

use crate::macos::permissions::MacOsPermissionsChecker;
use crate::{ClipboardBackend, PasteKeySender, PermissionKind, PlatformError};

/// ANSI virtual key code for `'v'` (`kVK_ANSI_V`), used as fallback when TIS lookup fails.
const KVK_ANSI_V: u16 = 9;
const K_UC_KEY_ACTION_DOWN: u16 = 0;
const K_UC_KEY_TRANSLATE_NO_DEAD_KEYS_MASK: u32 = 1;
const CMD_MODIFIER_KEY_STATE: u32 = (CARBON_CMD_KEY >> 8) & 0xFF;
const MAIN_QUEUE_LAYOUT_LOOKUP_TIMEOUT: Duration = Duration::from_millis(100);

#[allow(unsafe_code)]
#[link(name = "Carbon", kind = "framework")]
extern "C" {
    static kTISPropertyUnicodeKeyLayoutData: *const c_void;
    fn TISCopyCurrentKeyboardLayoutInputSource() -> *mut c_void;
    fn TISCopyCurrentASCIICapableKeyboardLayoutInputSource() -> *mut c_void;
    fn TISGetInputSourceProperty(
        input_source: *const c_void,
        property_key: *const c_void,
    ) -> *const c_void;
    fn LMGetKbdType() -> u8;
    fn UCKeyTranslate(
        key_layout_ptr: *const c_void,
        virtual_key_code: u16,
        key_action: u16,
        modifier_key_state: u32,
        keyboard_type: u32,
        key_translate_options: u32,
        dead_key_state: *mut u32,
        max_string_length: usize,
        actual_string_length: *mut usize,
        unicode_string: *mut u16,
    ) -> i32;
}

#[allow(unsafe_code)]
#[link(name = "CoreFoundation", kind = "framework")]
extern "C" {
    fn CFRelease(cf: *const c_void);
    fn CFDataGetBytePtr(the_data: *const c_void) -> *const u8;
}

#[allow(unsafe_code)]
extern "C" {
    fn pthread_main_np() -> c_int;
    static _dispatch_main_q: c_void;
    fn dispatch_async_f(
        queue: *const c_void,
        context: *mut c_void,
        work: unsafe extern "C" fn(*mut c_void),
    );
}

/// Releases an owned CoreFoundation object on drop.
struct OwnedCf(*mut c_void);

impl Drop for OwnedCf {
    fn drop(&mut self) {
        if !self.0.is_null() {
            #[allow(unsafe_code)]
            // SAFETY: `self.0` was obtained from a CoreFoundation Copy/Create API and is released once.
            unsafe {
                CFRelease(self.0);
            }
        }
    }
}

/// Finds the virtual keycode that produces `target` (case-insensitive) when `Cmd` is held
/// in the given `TISInputSourceRef` keyboard layout (`uchr` data).
fn resolve_cmd_char_keycode_in_source(
    source: *const c_void,
    kbd_type: u32,
    target: char,
) -> Option<u16> {
    if source.is_null() {
        return None;
    }
    #[allow(unsafe_code)]
    // SAFETY: `source` is a valid `TISInputSourceRef` and `kTISPropertyUnicodeKeyLayoutData` is
    // an immutable Carbon framework constant; the returned `CFDataRef` follows the Get rule.
    let layout_data =
        unsafe { TISGetInputSourceProperty(source, kTISPropertyUnicodeKeyLayoutData) };
    if layout_data.is_null() {
        return None;
    }
    #[allow(unsafe_code)]
    // SAFETY: `layout_data` is a non-null `CFDataRef` borrowed from `source` for this call.
    let layout_ptr = unsafe { CFDataGetBytePtr(layout_data) }.cast::<c_void>();
    if layout_ptr.is_null() {
        return None;
    }

    let expected = target.to_ascii_lowercase();
    let mut chars = [0u16; 4];
    for keycode in 0u16..128 {
        let mut dead_key_state = 0u32;
        let mut actual_len = 0usize;
        #[allow(unsafe_code)]
        // SAFETY: `layout_ptr` points to valid `uchr` data borrowed from `source`; output buffers
        // are valid stack memory sized to `chars.len()`.
        let status = unsafe {
            UCKeyTranslate(
                layout_ptr,
                keycode,
                K_UC_KEY_ACTION_DOWN,
                CMD_MODIFIER_KEY_STATE,
                kbd_type,
                K_UC_KEY_TRANSLATE_NO_DEAD_KEYS_MASK,
                &mut dead_key_state,
                chars.len(),
                &mut actual_len,
                chars.as_mut_ptr(),
            )
        };
        if status == 0 && actual_len == 1 {
            if let Some(ch) = char::from_u32(u32::from(chars[0])) {
                if ch.to_ascii_lowercase() == expected {
                    return Some(keycode);
                }
            }
        }
    }
    None
}

/// Resolves the virtual key code for `Cmd+V` on the current thread (must be the macOS main thread
/// in UI processes to satisfy HIToolbox/TIS main-queue assertions).
///
/// Handles QWERTY (`9`), QWERTZ (`9`), plain Dvorak (`47`), and Dvorak – QWERTY ⌘ (`9`),
/// falling back to the ASCII-capable layout and then [`KVK_ANSI_V`] for non-Latin layouts.
fn resolve_paste_keycode_on_main_thread() -> u16 {
    #[allow(unsafe_code)]
    // SAFETY: `LMGetKbdType` and `TISCopyCurrent*` are standard Carbon layout queries; owned
    // `TISInputSourceRef` pointers are wrapped in `OwnedCf` for deterministic release.
    let (kbd_type, current, ascii_fallback) = unsafe {
        (
            u32::from(LMGetKbdType()),
            OwnedCf(TISCopyCurrentKeyboardLayoutInputSource()),
            OwnedCf(TISCopyCurrentASCIICapableKeyboardLayoutInputSource()),
        )
    };
    resolve_cmd_char_keycode_in_source(current.0, kbd_type, 'v')
        .or_else(|| resolve_cmd_char_keycode_in_source(ascii_fallback.0, kbd_type, 'v'))
        .unwrap_or(KVK_ANSI_V)
}

#[allow(unsafe_code)]
unsafe extern "C" fn dispatch_resolve_keycode_work(context: *mut c_void) {
    if context.is_null() {
        return;
    }
    // SAFETY: `context` was created with `Box::into_raw` by `resolve_paste_keycode_with_timeout`
    // and is reclaimed exactly once on the main dispatch queue.
    let tx = unsafe { Box::from_raw(context.cast::<mpsc::SyncSender<u16>>()) };
    if let Ok(keycode) = catch_unwind(AssertUnwindSafe(resolve_paste_keycode_on_main_thread)) {
        let _ = tx.send(keycode);
    }
}

/// Resolves the active layout's `Cmd+V` virtual key code strictly on the macOS main thread.
///
/// - On the main thread: queries TIS synchronously (~10 µs).
/// - On a worker thread: dispatches the TIS query to the main dispatch queue and waits up to
///   `timeout`. Returns `None` if no main run loop is pumping so callers fall back to `osascript`
///   without deadlocking or violating macOS TIS main-thread assertions.
fn resolve_paste_keycode_with_timeout(timeout: Duration) -> Option<u16> {
    #[allow(unsafe_code)]
    // SAFETY: `pthread_main_np` is a thread-safe libSystem predicate taking no arguments.
    let is_main = unsafe { pthread_main_np() != 0 };
    if is_main {
        return Some(resolve_paste_keycode_on_main_thread());
    }

    let (tx, rx) = mpsc::sync_channel::<u16>(1);
    let ctx = Box::into_raw(Box::new(tx)).cast::<c_void>();
    #[allow(unsafe_code)]
    // SAFETY: `_dispatch_main_q` is libdispatch's static main queue; `dispatch_resolve_keycode_work`
    // always reclaims `ctx` via `Box::from_raw` even if the caller times out and drops `rx`.
    unsafe {
        dispatch_async_f(
            &raw const _dispatch_main_q,
            ctx,
            dispatch_resolve_keycode_work,
        );
    }
    rx.recv_timeout(timeout).ok()
}

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

        let write_err = if let Some(mut stdin) = child.stdin.take() {
            stdin.write_all(text.as_bytes()).err()
        } else {
            None
        };
        // Always reap `pbcopy` even when `write_all` fails (e.g. early child exit) to avoid zombies.
        let wait_result = child.wait_with_output();
        if let Some(err) = write_err {
            return Err(PlatformError::Clipboard(format!(
                "pbcopy stdin failed: {err}"
            )));
        }
        let output = wait_result
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

/// Emits `Cmd+V` on macOS via layout-aware Quartz `CGEventPost`, falling back to `osascript`.
///
/// Safe to call from either the main thread or worker threads: TIS keyboard-layout queries are
/// routed to the macOS main dispatch queue when invoked off the main thread, falling back to
/// layout-independent `osascript` if no main run loop is pumping.
#[derive(Debug, Default, Clone, Copy)]
pub struct MacOsPasteKeySender;

impl MacOsPasteKeySender {
    fn paste_via_quartz() -> Result<(), PlatformError> {
        let keycode = resolve_paste_keycode_with_timeout(MAIN_QUEUE_LAYOUT_LOOKUP_TIMEOUT)
            .ok_or_else(|| {
                PlatformError::Paste(
                    "main run loop unavailable for macOS keyboard layout resolution".into(),
                )
            })?;
        let down = CGEvent::new_keyboard_event(None, keycode, true).ok_or_else(|| {
            PlatformError::Paste("CGEventCreateKeyboardEvent(down) failed".into())
        })?;
        let up = CGEvent::new_keyboard_event(None, keycode, false)
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
    use objc2_core_foundation::{CFDictionary, CFString};

    #[allow(unsafe_code)]
    #[link(name = "Carbon", kind = "framework")]
    extern "C" {
        static kTISPropertyInputSourceID: &'static CFString;
        fn TISCreateInputSourceList(
            properties: Option<&CFDictionary<CFString, CFString>>,
            include_all_installed: bool,
        ) -> *mut c_void;
    }

    #[allow(unsafe_code)]
    #[link(name = "CoreFoundation", kind = "framework")]
    extern "C" {
        fn CFArrayGetCount(the_array: *const c_void) -> isize;
        fn CFArrayGetValueAtIndex(the_array: *const c_void, idx: isize) -> *const c_void;
    }

    fn keycode_for_installed_layout(layout_id: &str) -> Option<u16> {
        let id_str = CFString::from_str(layout_id);
        #[allow(unsafe_code)]
        unsafe {
            let filter =
                CFDictionary::from_slices(&[kTISPropertyInputSourceID], &[id_str.as_ref()]);
            let list = OwnedCf(TISCreateInputSourceList(Some(&filter), true));
            if list.0.is_null() || CFArrayGetCount(list.0) == 0 {
                return None;
            }
            let source = CFArrayGetValueAtIndex(list.0, 0);
            resolve_cmd_char_keycode_in_source(source, u32::from(LMGetKbdType()), 'v')
        }
    }

    #[test]
    fn nspasteboard_read_succeeds_without_modifying_clipboard() {
        let clipboard = MacOsClipboard;
        let _ = clipboard
            .read_text()
            .expect("NSPasteboard read must succeed");
    }

    #[test]
    fn resolve_paste_keycode_handles_qwerty_plain_dvorak_and_dvorak_qwerty_cmd() {
        assert_eq!(
            keycode_for_installed_layout("com.apple.keylayout.US"),
            Some(9)
        );
        assert_eq!(
            keycode_for_installed_layout("com.apple.keylayout.Dvorak"),
            Some(47)
        );
        assert_eq!(
            keycode_for_installed_layout("com.apple.keylayout.DVORAK-QWERTYCMD"),
            Some(9)
        );
        assert!(resolve_paste_keycode_on_main_thread() < 128);
    }

    #[test]
    fn resolve_paste_keycode_off_main_thread_times_out_cleanly_without_pumping_main_queue() {
        let result =
            std::thread::spawn(|| resolve_paste_keycode_with_timeout(Duration::from_millis(15)))
                .join()
                .expect("worker thread must not panic");
        assert_eq!(result, None);
    }
}
