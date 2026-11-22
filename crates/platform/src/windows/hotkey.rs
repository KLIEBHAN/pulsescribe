//! Native Windows global hotkeys via low-level keyboard hook (`WH_KEYBOARD_LL`).
//!
//! Matches `pulsescribe_windows.py` semantics:
//! - Supports toggle and hold hotkeys, including modifier-only combos like `ctrl+win`.
//! - Dispatches callbacks on a dedicated FIFO worker thread so the Win32 hook callback
//!   never blocks and never risks `LowLevelHooksTimeout` eviction.
//! - Verifies carried-over tracked keys against live `GetAsyncKeyState` before starting a new
//!   shortcut cycle, and schedules a Win32 timer only while a hold is active so a release lost on
//!   a UAC secure desktop still emits `HoldUp` once the user returns to the interactive desktop.

use std::cell::RefCell;
use std::collections::HashSet;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
use std::thread::{self, JoinHandle};

use pulsescribe_core::hotkey::{normalize_windows_hotkey, windows_hotkeys_conflict};
use windows::Win32::Foundation::{LPARAM, LRESULT, WPARAM};
use windows::Win32::System::StationsAndDesktops::{
    CloseDesktop, OpenInputDesktop, DESKTOP_CONTROL_FLAGS, DESKTOP_READOBJECTS,
};
use windows::Win32::System::Threading::GetCurrentThreadId;
use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, GetKeyboardLayout, VkKeyScanExW, VK_CONTROL, VK_LCONTROL, VK_LMENU,
    VK_LSHIFT, VK_LWIN, VK_MENU, VK_RCONTROL, VK_RMENU, VK_RSHIFT, VK_RWIN, VK_SHIFT,
};
use windows::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, GetMessageW, KillTimer, PeekMessageW, PostThreadMessageW, SetTimer,
    SetWindowsHookExW, UnhookWindowsHookEx, HC_ACTION, HHOOK, KBDLLHOOKSTRUCT, LLKHF_INJECTED, MSG,
    PM_NOREMOVE, WH_KEYBOARD_LL, WM_KEYDOWN, WM_KEYUP, WM_QUIT, WM_SYSKEYDOWN, WM_SYSKEYUP,
    WM_TIMER,
};

use crate::{
    HotkeyBackend, HotkeyBinding, HotkeyCallback, HotkeyEvent, HotkeyRegistration, HotkeySource,
    PlatformError,
};

/// Reconciliation cadence while a hold shortcut is active (0 wakeups while idle).
const HOLD_RECONCILE_INTERVAL_MS: u32 = 250;

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

/// Returns `true` when the active input desktop is accessible to this process.
///
/// On the UAC secure desktop or lock screen, `OpenInputDesktop` fails with `ERROR_ACCESS_DENIED`
/// and `GetAsyncKeyState` reports `0` for all keys; callers must treat that state as unavailable
/// rather than a confirmed key release.
fn is_input_desktop_accessible() -> bool {
    #[allow(unsafe_code)]
    // SAFETY: `OpenInputDesktop` returns an owned `HDESK` immediately released by `CloseDesktop`.
    match unsafe { OpenInputDesktop(DESKTOP_CONTROL_FLAGS(0), false, DESKTOP_READOBJECTS) } {
        Ok(hdesk) => {
            #[allow(unsafe_code)]
            // SAFETY: `hdesk` was returned by `OpenInputDesktop` above and is closed once.
            unsafe {
                let _ = CloseDesktop(hdesk);
            }
            true
        }
        Err(_) => false,
    }
}

fn query_vk_state_with_desktop_check(
    desktop_accessible: &mut Option<bool>,
    vk: u16,
) -> Option<bool> {
    #[allow(unsafe_code)]
    // SAFETY: `GetAsyncKeyState` is a thread-safe Win32 key-state query.
    let state = unsafe { GetAsyncKeyState(i32::from(vk)) };
    if (state as u16 & 0x8000) != 0 {
        return Some(true);
    }
    // Only query `OpenInputDesktop` when `GetAsyncKeyState` reports not-down, avoiding desktop
    // handle syscalls on the hot path while still distinguishing secure-desktop isolation.
    let accessible = *desktop_accessible.get_or_insert_with(is_input_desktop_accessible);
    if accessible {
        Some(false)
    } else {
        None
    }
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
    pressed_vks: HashSet<u16>,
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
            pressed_vks: HashSet::new(),
        })
    }

    fn has_active_hold(&self) -> bool {
        self.rules.iter().any(|r| r.is_hold && r.hold_active)
    }

    fn active_keys(&self) -> HashSet<WindowsKey> {
        self.pressed_vks
            .iter()
            .map(|&vk| WindowsKey::from_raw_vk(vk))
            .collect()
    }

    /// Reconciles tracked keys against live OS state (`Some(true)` = down, `Some(false)` = up,
    /// `None` = desktop state unavailable). Optionally excludes `transitioning_vk`, because Win32
    /// has not yet updated `GetAsyncKeyState` for the key currently transitioning in a hook event.
    fn reconcile_tracked_keys<F: FnMut(u16) -> Option<bool>>(
        &mut self,
        transitioning_vk: Option<u16>,
        mut query_state: F,
    ) -> Vec<HotkeyEvent> {
        let mut removed_any = false;
        self.pressed_vks.retain(|&vk| {
            if Some(vk) == transitioning_vk {
                return true;
            }
            if query_state(vk) == Some(false) {
                removed_any = true;
                false
            } else {
                true
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

    fn on_key_down<F: FnMut(u16) -> Option<bool>>(
        &mut self,
        vk: u16,
        query_state: F,
    ) -> Vec<HotkeyEvent> {
        // Validate all carried-over keys before checking for a new match so missed modifier
        // releases cannot trigger shortcuts on a subsequent unrelated keydown.
        let mut events = self.reconcile_tracked_keys(Some(vk), query_state);
        self.pressed_vks.insert(vk);

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

    /// Periodic hold reconciliation used when a hold shortcut is active, recovering releases lost
    /// on the UAC secure desktop without requiring another keydown.
    fn reconcile_active_holds<F: FnMut(u16) -> Option<bool>>(
        &mut self,
        query_state: F,
    ) -> Vec<HotkeyEvent> {
        if !self.has_active_hold() {
            return Vec::new();
        }
        self.reconcile_tracked_keys(None, query_state)
    }
}

struct HookThreadState {
    matcher: WindowsHotkeyMatcher,
    event_tx: mpsc::Sender<HotkeyEvent>,
    reconcile_timer_id: usize,
}

impl HookThreadState {
    fn sync_reconcile_timer(&mut self) {
        let should_run = self.matcher.has_active_hold();
        if should_run && self.reconcile_timer_id == 0 {
            #[allow(unsafe_code)]
            // SAFETY: Called on the hook thread's message-pump thread to schedule `WM_TIMER`.
            let id = unsafe { SetTimer(None, 0, HOLD_RECONCILE_INTERVAL_MS, None) };
            self.reconcile_timer_id = id;
        } else if !should_run && self.reconcile_timer_id != 0 {
            self.stop_reconcile_timer();
        }
    }

    fn stop_reconcile_timer(&mut self) {
        if self.reconcile_timer_id != 0 {
            let id = std::mem::replace(&mut self.reconcile_timer_id, 0);
            #[allow(unsafe_code)]
            // SAFETY: `id` was returned by `SetTimer(None, ...)` on this same thread.
            unsafe {
                let _ = KillTimer(None, id);
            }
        }
    }
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
                            let mut desktop_accessible = None;
                            state.matcher.on_key_down(vk, |other| {
                                query_vk_state_with_desktop_check(&mut desktop_accessible, other)
                            })
                        }
                        WM_KEYUP | WM_SYSKEYUP => state.matcher.on_key_up(vk),
                        _ => return,
                    };
                    for event in events {
                        let _ = state.event_tx.send(event);
                    }
                    state.sync_reconcile_timer();
                });
            }));
        }
    }
    // SAFETY: Passing `None` as `hhk` is standard on Windows NT+ and chains to the next hook.
    unsafe { CallNextHookEx(None, code, wparam, lparam) }
}

fn handle_reconcile_timer(timer_wparam: usize) {
    HOOK_STATE.with(|cell| {
        let Ok(mut slot) = cell.try_borrow_mut() else {
            return;
        };
        let Some(state) = slot.as_mut() else {
            return;
        };
        if state.reconcile_timer_id == 0 || timer_wparam != state.reconcile_timer_id {
            return;
        }
        let mut desktop_accessible = None;
        let events = state.matcher.reconcile_active_holds(|vk| {
            query_vk_state_with_desktop_check(&mut desktop_accessible, vk)
        });
        for event in events {
            let _ = state.event_tx.send(event);
        }
        state.sync_reconcile_timer();
    });
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
            if let Some(mut state) = cell.borrow_mut().take() {
                state.stop_reconcile_timer();
            }
        });
    }
}

struct WindowsHotkeyHandle {
    stopped: Arc<AtomicBool>,
    hook_thread_id: Option<u32>,
    hook_thread: Option<JoinHandle<()>>,
    dispatch_thread: Option<JoinHandle<()>>,
}

impl WindowsHotkeyHandle {
    fn stop(&mut self) {
        // Prevent queued events from invoking `on_event` after `unregister()` returns,
        // including when `unregister()` is called from inside the callback itself.
        self.stopped.store(true, Ordering::Release);
        if let Some(tid) = self.hook_thread_id.take() {
            #[allow(unsafe_code)]
            // SAFETY: `tid` is the Win32 thread ID whose message queue was created before startup
            // completed; `WM_QUIT` terminates `GetMessageW` cleanly.
            unsafe {
                let _ = PostThreadMessageW(tid, WM_QUIT, WPARAM(0), LPARAM(0));
            }
        }
        if let Some(handle) = self.hook_thread.take() {
            if handle.thread().id() != thread::current().id() {
                let _ = handle.join();
            }
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
        let stopped = Arc::new(AtomicBool::new(false));
        if binding.is_empty() {
            return Ok(Box::new(WindowsHotkeyHandle {
                stopped,
                hook_thread_id: None,
                hook_thread: None,
                dispatch_thread: None,
            }));
        }

        let matcher = WindowsHotkeyMatcher::from_binding(binding)?;
        let (event_tx, event_rx) = mpsc::channel::<HotkeyEvent>();
        let dispatch_stopped = Arc::clone(&stopped);
        let dispatch_thread = thread::Builder::new()
            .name("pulsescribe-win-hotkey-dispatch".to_owned())
            .spawn(move || {
                while let Ok(event) = event_rx.recv() {
                    if dispatch_stopped.load(Ordering::Acquire) {
                        break;
                    }
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
                    *cell.borrow_mut() = Some(HookThreadState {
                        matcher,
                        event_tx,
                        reconcile_timer_id: 0,
                    });
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
                    if msg.message == WM_TIMER {
                        handle_reconcile_timer(msg.wParam.0);
                    }
                }
                drop(hook);
            })
            .map_err(|err| PlatformError::Hotkey(format!("failed to spawn hook thread: {err}")))?;

        match ready_rx.recv() {
            Ok(Ok(tid)) => Ok(Box::new(WindowsHotkeyHandle {
                stopped,
                hook_thread_id: Some(tid),
                hook_thread: Some(hook_thread),
                dispatch_thread: Some(dispatch_thread),
            })),
            Ok(Err(err)) => {
                stopped.store(true, Ordering::Release);
                let _ = hook_thread.join();
                let _ = dispatch_thread.join();
                Err(err)
            }
            Err(_) => {
                stopped.store(true, Ordering::Release);
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

    #[test]
    fn matcher_handles_modifier_only_hold_and_long_holds() {
        let binding = HotkeyBinding::new(Some("ctrl+alt+r".into()), Some("ctrl+win".into()));
        let mut matcher = WindowsHotkeyMatcher::from_binding(&binding).unwrap();

        assert!(matcher
            .on_key_down(VK_LCONTROL.0, |_| Some(true))
            .is_empty());
        assert_eq!(
            matcher.on_key_down(VK_LWIN.0, |_| Some(true)),
            vec![HotkeyEvent::HoldDown {
                source: HotkeySource::WindowsHook
            }]
        );
        assert!(matcher.has_active_hold());
        // Auto-repeat while held does not re-trigger.
        assert!(matcher.on_key_down(VK_LWIN.0, |_| Some(true)).is_empty());

        // Releasing Left Ctrl while Right Ctrl is held keeps the hold active.
        assert!(matcher
            .on_key_down(VK_RCONTROL.0, |_| Some(true))
            .is_empty());
        assert!(matcher.on_key_up(VK_LCONTROL.0).is_empty());
        assert!(matcher.has_active_hold());

        // Releasing Right Ctrl ends the hold.
        assert_eq!(
            matcher.on_key_up(VK_RCONTROL.0),
            vec![HotkeyEvent::HoldUp {
                source: HotkeySource::WindowsHook
            }]
        );
        assert!(!matcher.has_active_hold());
    }

    #[test]
    fn matcher_drops_carried_over_modifiers_immediately_when_physically_up() {
        let binding = HotkeyBinding::new(Some("ctrl+alt+r".into()), None);
        let mut matcher = WindowsHotkeyMatcher::from_binding(&binding).unwrap();

        // Simulate Ctrl+Alt pressed earlier, then releases missed by the hook.
        assert!(matcher
            .on_key_down(VK_LCONTROL.0, |_| Some(true))
            .is_empty());
        assert!(matcher.on_key_down(VK_LMENU.0, |_| Some(true)).is_empty());

        // Pressing R while Ctrl & Alt are confirmed up must NOT fire ctrl+alt+r.
        assert!(matcher.on_key_down(b'R' as u16, |_| Some(false)).is_empty());
        assert!(matcher.on_key_up(b'R' as u16).is_empty());

        // Pressing Ctrl+Alt+R while Ctrl & Alt are confirmed down fires Toggle and suppresses repeat.
        assert!(matcher
            .on_key_down(VK_LCONTROL.0, |_| Some(true))
            .is_empty());
        assert!(matcher.on_key_down(VK_LMENU.0, |_| Some(true)).is_empty());
        assert_eq!(
            matcher.on_key_down(b'R' as u16, |_| Some(true)),
            vec![HotkeyEvent::Toggle]
        );
        assert!(matcher.on_key_down(b'R' as u16, |_| Some(true)).is_empty());
        assert!(matcher.on_key_up(b'R' as u16).is_empty());
        assert_eq!(
            matcher.on_key_down(b'R' as u16, |_| Some(true)),
            vec![HotkeyEvent::Toggle]
        );
    }

    #[test]
    fn matcher_reconciles_lost_hold_release_only_when_desktop_state_is_available() {
        let binding = HotkeyBinding::new(None, Some("ctrl+win".into()));
        let mut matcher = WindowsHotkeyMatcher::from_binding(&binding).unwrap();

        assert!(matcher
            .on_key_down(VK_LCONTROL.0, |_| Some(true))
            .is_empty());
        assert_eq!(
            matcher.on_key_down(VK_LWIN.0, |_| Some(true)),
            vec![HotkeyEvent::HoldDown {
                source: HotkeySource::WindowsHook
            }]
        );

        // Keys still down -> hold stays active.
        assert!(matcher.reconcile_active_holds(|_| Some(true)).is_empty());
        assert!(matcher.has_active_hold());

        // Secure desktop active (`None`) -> do NOT treat unavailable state as confirmed release.
        assert!(matcher.reconcile_active_holds(|_| None).is_empty());
        assert!(matcher.has_active_hold());

        // Back on interactive desktop with keys released -> emits exactly one HoldUp.
        assert_eq!(
            matcher.reconcile_active_holds(|_| Some(false)),
            vec![HotkeyEvent::HoldUp {
                source: HotkeySource::WindowsHook
            }]
        );
        assert!(!matcher.has_active_hold());
        // Subsequent reconciliations do not emit duplicates.
        assert!(matcher.reconcile_active_holds(|_| Some(false)).is_empty());
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
