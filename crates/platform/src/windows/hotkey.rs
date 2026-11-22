//! Native Windows global hotkeys via low-level keyboard hook (`WH_KEYBOARD_LL`).
//!
//! Matches `pulsescribe_windows.py` semantics:
//! - Supports toggle and hold hotkeys, including modifier-only combos like `ctrl+win`.
//! - Dispatches callbacks on a dedicated FIFO worker thread so the Win32 hook callback
//!   never blocks and never risks `LowLevelHooksTimeout` eviction.
//! - Uses `GetAsyncKeyState` to verify stale keys so long holds (>2 s) never drop modifiers
//!   while lost releases (e.g. after UAC prompts) are still cleaned up.

use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::mpsc;
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use pulsescribe_core::hotkey::{normalize_windows_hotkey, windows_hotkeys_conflict};
use windows::Win32::Foundation::{LPARAM, LRESULT, WPARAM};
use windows::Win32::System::Threading::GetCurrentThreadId;
use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, GetKeyboardLayout, VkKeyScanExW, VK_CONTROL, VK_LCONTROL, VK_LMENU,
    VK_LSHIFT, VK_LWIN, VK_MENU, VK_RCONTROL, VK_RMENU, VK_RSHIFT, VK_RWIN, VK_SHIFT,
};
use windows::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, GetMessageW, PeekMessageW, PostThreadMessageW, SetWindowsHookExW,
    UnhookWindowsHookEx, HC_ACTION, HHOOK, KBDLLHOOKSTRUCT, LLKHF_INJECTED, MSG, PM_NOREMOVE,
    WH_KEYBOARD_LL, WM_KEYDOWN, WM_KEYUP, WM_QUIT, WM_SYSKEYDOWN, WM_SYSKEYUP,
};

use crate::{
    HotkeyBackend, HotkeyBinding, HotkeyCallback, HotkeyEvent, HotkeyRegistration, HotkeySource,
    PlatformError,
};

const KEY_STALE_TIMEOUT: Duration = Duration::from_secs(2);

/// Canonical key token matching `pulsescribe_core::hotkey::normalize_windows_hotkey`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum WindowsKey {
    Ctrl,
    Alt,
    Shift,
    Win,
    Vk(u16),
}

impl WindowsKey {
    fn from_raw_vk(vk: u16) -> Self {
        match vk {
            v if v == VK_CONTROL.0 || v == VK_LCONTROL.0 || v == VK_RCONTROL.0 => Self::Ctrl,
            v if v == VK_MENU.0 || v == VK_LMENU.0 || v == VK_RMENU.0 => Self::Alt,
            v if v == VK_SHIFT.0 || v == VK_LSHIFT.0 || v == VK_RSHIFT.0 => Self::Shift,
            v if v == VK_LWIN.0 || v == VK_RWIN.0 => Self::Win,
            other => Self::Vk(other),
        }
    }

    fn from_normalized_token(token: &str) -> Result<Self, PlatformError> {
        let key = match token {
            "ctrl" => Self::Ctrl,
            "alt" => Self::Alt,
            "shift" => Self::Shift,
            "win" => Self::Win,
            "space" => Self::Vk(0x20),
            "tab" => Self::Vk(0x09),
            "enter" => Self::Vk(0x0D),
            "esc" => Self::Vk(0x1B),
            "backspace" => Self::Vk(0x08),
            "delete" => Self::Vk(0x2E),
            "home" => Self::Vk(0x24),
            "end" => Self::Vk(0x23),
            "pageup" => Self::Vk(0x21),
            "pagedown" => Self::Vk(0x22),
            "left" => Self::Vk(0x25),
            "up" => Self::Vk(0x26),
            "right" => Self::Vk(0x27),
            "down" => Self::Vk(0x28),
            "capslock" => Self::Vk(0x14),
            _ => {
                if let Some(digits) = token.strip_prefix('f') {
                    if let Ok(n) = digits.parse::<u16>() {
                        if (1..=24).contains(&n) {
                            return Ok(Self::Vk(0x6F + n));
                        }
                    }
                }
                let mut chars = token.chars();
                if let (Some(ch), None) = (chars.next(), chars.next()) {
                    if ch.is_ascii_alphabetic() {
                        return Ok(Self::Vk(u16::from(ch.to_ascii_uppercase() as u8)));
                    }
                    if ch.is_ascii_digit() {
                        return Ok(Self::Vk(u16::from(ch as u8)));
                    }
                    if let Some(vk) = vk_from_char(ch) {
                        return Ok(Self::Vk(vk));
                    }
                }
                return Err(PlatformError::Hotkey(format!(
                    "unsupported Windows hotkey token: {token}"
                )));
            }
        };
        Ok(key)
    }
}

fn vk_from_char(ch: char) -> Option<u16> {
    let mut utf16 = [0u16; 2];
    let encoded = ch.encode_utf16(&mut utf16);
    if encoded.len() != 1 {
        return None;
    }
    #[allow(unsafe_code)]
    // SAFETY: `GetKeyboardLayout(0)` queries the current thread layout; `VkKeyScanExW` is pure.
    let scan = unsafe { VkKeyScanExW(encoded[0], GetKeyboardLayout(0)) };
    if scan == -1 || (scan & 0xFF) == 0xFF {
        None
    } else {
        Some((scan & 0xFF) as u16)
    }
}

fn is_vk_physically_down(vk: u16) -> bool {
    #[allow(unsafe_code)]
    // SAFETY: `GetAsyncKeyState` is a thread-safe Win32 key-state query.
    let state = unsafe { GetAsyncKeyState(i32::from(vk)) };
    (state as u16 & 0x8000) != 0
}

#[derive(Debug, Clone)]
struct HotkeyRule {
    keys: HashSet<WindowsKey>,
    is_hold: bool,
    triggered_in_cycle: bool,
    hold_active: bool,
}

impl HotkeyRule {
    fn parse(raw: &str, is_hold: bool) -> Result<Self, PlatformError> {
        let normalized = normalize_windows_hotkey(Some(raw)).map_err(PlatformError::Hotkey)?;
        if normalized.is_empty() {
            return Err(PlatformError::Hotkey("empty Windows hotkey".into()));
        }
        let mut keys = HashSet::new();
        for token in normalized.split('+') {
            keys.insert(WindowsKey::from_normalized_token(token)?);
        }
        Ok(Self {
            keys,
            is_hold,
            triggered_in_cycle: false,
            hold_active: false,
        })
    }
}

/// Pure, testable state machine for Windows toggle and hold hotkeys.
struct WindowsHotkeyMatcher {
    rules: Vec<HotkeyRule>,
    pressed_vks: HashMap<u16, Instant>,
}

impl WindowsHotkeyMatcher {
    fn from_binding(binding: &HotkeyBinding) -> Result<Self, PlatformError> {
        if windows_hotkeys_conflict(binding.toggle.as_deref(), binding.hold.as_deref()) {
            return Err(PlatformError::Hotkey(
                "toggle and hold hotkeys overlap".into(),
            ));
        }
        let mut rules = Vec::new();
        if let Some(ref toggle) = binding.toggle {
            rules.push(HotkeyRule::parse(toggle, false)?);
        }
        if let Some(ref hold) = binding.hold {
            rules.push(HotkeyRule::parse(hold, true)?);
        }
        Ok(Self {
            rules,
            pressed_vks: HashMap::new(),
        })
    }

    fn active_keys(&self) -> HashSet<WindowsKey> {
        self.pressed_vks
            .keys()
            .map(|&vk| WindowsKey::from_raw_vk(vk))
            .collect()
    }

    fn prune_stale_keys<F: Fn(u16) -> bool>(
        &mut self,
        now: Instant,
        is_down: F,
    ) -> Vec<HotkeyEvent> {
        let mut removed_any = false;
        self.pressed_vks.retain(|&vk, pressed_at| {
            let expired = now.saturating_duration_since(*pressed_at) > KEY_STALE_TIMEOUT;
            if !expired {
                return true;
            }
            if is_down(vk) {
                // Still physically held: refresh timestamp to avoid repeated GetAsyncKeyState calls.
                *pressed_at = now;
                true
            } else {
                removed_any = true;
                false
            }
        });

        let mut events = Vec::new();
        if removed_any {
            let active = self.active_keys();
            for rule in &mut self.rules {
                if !rule.keys.is_subset(&active) {
                    rule.triggered_in_cycle = false;
                    if rule.is_hold && rule.hold_active {
                        rule.hold_active = false;
                        events.push(HotkeyEvent::HoldUp {
                            source: HotkeySource::WindowsHook,
                        });
                    }
                }
            }
        }
        events
    }

    fn on_key_down<F: Fn(u16) -> bool>(
        &mut self,
        vk: u16,
        now: Instant,
        is_down: F,
    ) -> Vec<HotkeyEvent> {
        let mut events = self.prune_stale_keys(now, is_down);
        self.pressed_vks.insert(vk, now);

        let normalized = WindowsKey::from_raw_vk(vk);
        let active = self.active_keys();
        for rule in &mut self.rules {
            if !rule.keys.contains(&normalized) || !rule.keys.is_subset(&active) {
                continue;
            }
            if rule.triggered_in_cycle {
                continue;
            }
            rule.triggered_in_cycle = true;
            if rule.is_hold {
                rule.hold_active = true;
                events.push(HotkeyEvent::HoldDown {
                    source: HotkeySource::WindowsHook,
                });
            } else {
                events.push(HotkeyEvent::Toggle);
            }
        }
        events
    }

    fn on_key_up(&mut self, vk: u16) -> Vec<HotkeyEvent> {
        self.pressed_vks.remove(&vk);
        let active = self.active_keys();
        let mut events = Vec::new();

        for rule in &mut self.rules {
            if !rule.keys.is_subset(&active) {
                rule.triggered_in_cycle = false;
                if rule.is_hold && rule.hold_active {
                    rule.hold_active = false;
                    events.push(HotkeyEvent::HoldUp {
                        source: HotkeySource::WindowsHook,
                    });
                }
            }
        }
        events
    }
}

struct HookThreadState {
    matcher: WindowsHotkeyMatcher,
    event_tx: mpsc::Sender<HotkeyEvent>,
}

thread_local! {
    static HOOK_STATE: RefCell<Option<HookThreadState>> = const { RefCell::new(None) };
}

#[allow(unsafe_code)]
unsafe extern "system" fn low_level_keyboard_proc(
    code: i32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    if code == HC_ACTION as i32 && lparam.0 != 0 {
        // SAFETY: For `WH_KEYBOARD_LL` with `code == HC_ACTION`, `lparam` points to a valid
        // `KBDLLHOOKSTRUCT` for the duration of this hook callback.
        let kb = unsafe { &*(lparam.0 as *const KBDLLHOOKSTRUCT) };
        if !kb.flags.contains(LLKHF_INJECTED) {
            let msg = wparam.0 as u32;
            let vk = kb.vkCode as u16;
            let _ = catch_unwind(AssertUnwindSafe(|| {
                HOOK_STATE.with(|cell| {
                    let Ok(mut slot) = cell.try_borrow_mut() else {
                        return;
                    };
                    let Some(state) = slot.as_mut() else {
                        return;
                    };
                    let events = match msg {
                        WM_KEYDOWN | WM_SYSKEYDOWN => {
                            state
                                .matcher
                                .on_key_down(vk, Instant::now(), is_vk_physically_down)
                        }
                        WM_KEYUP | WM_SYSKEYUP => state.matcher.on_key_up(vk),
                        _ => return,
                    };
                    for event in events {
                        let _ = state.event_tx.send(event);
                    }
                });
            }));
        }
    }
    // SAFETY: Passing `None` as `hhk` is standard on Windows NT+ and chains to the next hook.
    unsafe { CallNextHookEx(None, code, wparam, lparam) }
}

struct UnhookGuard(HHOOK);

impl Drop for UnhookGuard {
    fn drop(&mut self) {
        if !self.0.is_invalid() {
            #[allow(unsafe_code)]
            // SAFETY: `self.0` was installed on this thread via `SetWindowsHookExW`.
            unsafe {
                let _ = UnhookWindowsHookEx(self.0);
            }
        }
    }
}

struct ClearHookStateGuard;

impl Drop for ClearHookStateGuard {
    fn drop(&mut self) {
        HOOK_STATE.with(|cell| {
            *cell.borrow_mut() = None;
        });
    }
}

struct WindowsHotkeyHandle {
    hook_thread_id: Option<u32>,
    hook_thread: Option<JoinHandle<()>>,
    dispatch_thread: Option<JoinHandle<()>>,
}

impl WindowsHotkeyHandle {
    fn stop(&mut self) {
        if let Some(tid) = self.hook_thread_id.take() {
            #[allow(unsafe_code)]
            // SAFETY: `tid` is the Win32 thread ID whose message queue was created before startup
            // completed; `WM_QUIT` terminates `GetMessageW` cleanly.
            unsafe {
                let _ = PostThreadMessageW(tid, WM_QUIT, WPARAM(0), LPARAM(0));
            }
        }
        if let Some(handle) = self.hook_thread.take() {
            let _ = handle.join();
        }
        if let Some(handle) = self.dispatch_thread.take() {
            if handle.thread().id() != thread::current().id() {
                let _ = handle.join();
            }
        }
    }
}

impl HotkeyRegistration for WindowsHotkeyHandle {
    fn unregister(&mut self) -> Result<(), PlatformError> {
        self.stop();
        Ok(())
    }
}

impl Drop for WindowsHotkeyHandle {
    fn drop(&mut self) {
        self.stop();
    }
}

/// Native Windows [`HotkeyBackend`] powered by `WH_KEYBOARD_LL` + FIFO callback dispatcher.
#[derive(Debug, Default, Clone, Copy)]
pub struct WindowsHotkeyBackend;

impl HotkeyBackend for WindowsHotkeyBackend {
    fn register(
        &self,
        binding: &HotkeyBinding,
        on_event: HotkeyCallback,
    ) -> Result<Box<dyn HotkeyRegistration>, PlatformError> {
        if binding.is_empty() {
            return Ok(Box::new(WindowsHotkeyHandle {
                hook_thread_id: None,
                hook_thread: None,
                dispatch_thread: None,
            }));
        }

        let matcher = WindowsHotkeyMatcher::from_binding(binding)?;
        let (event_tx, event_rx) = mpsc::channel::<HotkeyEvent>();
        let dispatch_thread = thread::Builder::new()
            .name("pulsescribe-win-hotkey-dispatch".to_owned())
            .spawn(move || {
                while let Ok(event) = event_rx.recv() {
                    if catch_unwind(AssertUnwindSafe(|| on_event(event))).is_err() {
                        tracing::error!("Windows hotkey callback panicked");
                    }
                }
            })
            .map_err(|err| {
                PlatformError::Hotkey(format!("failed to spawn hotkey dispatcher: {err}"))
            })?;

        let (ready_tx, ready_rx) = mpsc::sync_channel::<Result<u32, PlatformError>>(1);
        let hook_thread = thread::Builder::new()
            .name("pulsescribe-win-hotkey-hook".to_owned())
            .spawn(move || {
                let _clear_guard = ClearHookStateGuard;
                HOOK_STATE.with(|cell| {
                    *cell.borrow_mut() = Some(HookThreadState { matcher, event_tx });
                });

                // Force-create the thread's Win32 message queue before sharing our thread ID so
                // `PostThreadMessageW(WM_QUIT)` can never race against queue creation.
                let mut msg = MSG::default();
                #[allow(unsafe_code)]
                // SAFETY: `msg` is a valid out-pointer; `PM_NOREMOVE` ensures queue creation.
                unsafe {
                    let _ = PeekMessageW(&mut msg, None, 0, 0, PM_NOREMOVE);
                }

                #[allow(unsafe_code)]
                // SAFETY: `low_level_keyboard_proc` is a valid `HOOKPROC`; `UnhookGuard` unhooks
                // it on this thread before `_clear_guard` clears thread-local `HOOK_STATE`.
                let hook = match unsafe {
                    SetWindowsHookExW(WH_KEYBOARD_LL, Some(low_level_keyboard_proc), None, 0)
                } {
                    Ok(h) => UnhookGuard(h),
                    Err(err) => {
                        let _ = ready_tx.send(Err(PlatformError::Hotkey(format!(
                            "SetWindowsHookExW failed: {err}"
                        ))));
                        return;
                    }
                };

                #[allow(unsafe_code)]
                // SAFETY: `GetCurrentThreadId` takes no parameters and returns this thread's ID.
                let tid = unsafe { GetCurrentThreadId() };
                if ready_tx.send(Ok(tid)).is_err() {
                    drop(hook);
                    return;
                }

                loop {
                    #[allow(unsafe_code)]
                    // SAFETY: `msg` is a valid stack `MSG`; `GetMessageW` returns > 0 for normal
                    // messages, 0 on `WM_QUIT`, and -1 on error.
                    let ret = unsafe { GetMessageW(&mut msg, None, 0, 0) };
                    if ret.0 <= 0 {
                        break;
                    }
                }
                drop(hook);
            })
            .map_err(|err| PlatformError::Hotkey(format!("failed to spawn hook thread: {err}")))?;

        match ready_rx.recv() {
            Ok(Ok(tid)) => Ok(Box::new(WindowsHotkeyHandle {
                hook_thread_id: Some(tid),
                hook_thread: Some(hook_thread),
                dispatch_thread: Some(dispatch_thread),
            })),
            Ok(Err(err)) => {
                let _ = hook_thread.join();
                let _ = dispatch_thread.join();
                Err(err)
            }
            Err(_) => {
                let _ = hook_thread.join();
                let _ = dispatch_thread.join();
                Err(PlatformError::Hotkey(
                    "Windows hotkey hook startup channel closed".into(),
                ))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    #[test]
    fn matcher_handles_modifier_only_hold_and_long_holds() {
        let binding = HotkeyBinding::new(Some("ctrl+alt+r".into()), Some("ctrl+win".into()));
        let mut matcher = WindowsHotkeyMatcher::from_binding(&binding).unwrap();
        let t0 = Instant::now();

        assert!(matcher.on_key_down(VK_LCONTROL.0, t0, |_| true).is_empty());
        assert_eq!(
            matcher.on_key_down(VK_LWIN.0, t0, |_| true),
            vec![HotkeyEvent::HoldDown {
                source: HotkeySource::WindowsHook
            }]
        );
        // Auto-repeat while held does not re-trigger.
        assert!(matcher
            .on_key_down(VK_LWIN.0, t0 + Duration::from_millis(50), |_| true)
            .is_empty());

        // Long hold (> 2s): `is_down` confirms Ctrl is still physically down, so it stays active.
        assert!(matcher
            .on_key_down(VK_LWIN.0, t0 + Duration::from_secs(5), |_| true)
            .is_empty());

        // Releasing Left Ctrl while Right Ctrl is held keeps the hold active.
        assert!(matcher
            .on_key_down(VK_RCONTROL.0, t0 + Duration::from_secs(5), |_| true)
            .is_empty());
        assert!(matcher.on_key_up(VK_LCONTROL.0).is_empty());

        // Releasing Right Ctrl ends the hold.
        assert_eq!(
            matcher.on_key_up(VK_RCONTROL.0),
            vec![HotkeyEvent::HoldUp {
                source: HotkeySource::WindowsHook
            }]
        );
    }

    #[test]
    fn matcher_prunes_stale_lost_releases_and_suppresses_toggle_autorepeat() {
        let binding = HotkeyBinding::new(Some("ctrl+alt+r".into()), None);
        let mut matcher = WindowsHotkeyMatcher::from_binding(&binding).unwrap();
        let t0 = Instant::now();

        assert!(matcher.on_key_down(VK_LCONTROL.0, t0, |_| false).is_empty());
        assert!(matcher.on_key_down(VK_LMENU.0, t0, |_| false).is_empty());
        assert_eq!(
            matcher.on_key_down(b'R' as u16, t0, |_| false),
            vec![HotkeyEvent::Toggle]
        );
        // Auto-repeat suppressed until release.
        assert!(matcher
            .on_key_down(b'R' as u16, t0 + Duration::from_millis(40), |_| false)
            .is_empty());
        assert!(matcher.on_key_up(b'R' as u16).is_empty());
        // Immediately re-triggerable after release (no fixed time debounce).
        assert_eq!(
            matcher.on_key_down(b'R' as u16, t0 + Duration::from_millis(60), |_| false),
            vec![HotkeyEvent::Toggle]
        );

        // Lost release (> 2s later, physically up): stale Ctrl/Alt are pruned cleanly.
        assert!(matcher
            .on_key_down(b'R' as u16, t0 + Duration::from_secs(4), |_| false)
            .is_empty());
    }

    #[test]
    fn matcher_rejects_overlapping_toggle_and_hold() {
        let binding = HotkeyBinding::new(Some("ctrl+win+r".into()), Some("ctrl+win".into()));
        assert!(WindowsHotkeyMatcher::from_binding(&binding).is_err());
    }

    #[test]
    fn hook_backend_starts_and_stops_cleanly_on_windows() {
        let backend = WindowsHotkeyBackend;
        let binding = HotkeyBinding::new(Some("ctrl+alt+f19".into()), Some("ctrl+win".into()));
        let mut handle = backend
            .register(&binding, Arc::new(|_| {}))
            .expect("WH_KEYBOARD_LL registration should succeed on Windows");
        handle.unregister().unwrap();
    }
}
