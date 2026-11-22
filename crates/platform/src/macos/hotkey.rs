//! Native macOS global hotkeys via Carbon (`RegisterEventHotKey`) and Quartz (`CGEventTap`).
//!
//! - Standard toggle shortcuts (e.g. `f19`, `cmd+shift+r`) use Carbon `RegisterEventHotKey`,
//!   which requires no Accessibility or Input Monitoring permissions.
//! - Modifier shortcuts (`fn`, `capslock`) and hold-to-record shortcuts use a `CGEventTap`
//!   running on a dedicated `CFRunLoop` thread with zero idle wakeups.

use std::collections::HashMap;
use std::ffi::c_void;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::ptr::{null_mut, NonNull};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{mpsc, Mutex, OnceLock};
use std::thread::{self, JoinHandle};

use objc2_core_foundation::{
    kCFRunLoopCommonModes, kCFRunLoopDefaultMode, CFMachPort, CFRetained, CFRunLoop,
    CFRunLoopActivity, CFRunLoopObserver, CFRunLoopObserverContext,
};
use objc2_core_graphics::{
    CGEvent, CGEventField, CGEventFlags, CGEventTapLocation, CGEventTapOptions,
    CGEventTapPlacement, CGEventTapProxy, CGEventType,
};
use pulsescribe_core::hotkey::{
    parse_macos_hotkey, CARBON_CMD_KEY, CARBON_CONTROL_KEY, CARBON_OPTION_KEY, CARBON_SHIFT_KEY,
};

use crate::{
    lock_unpoisoned, HotkeyBackend, HotkeyBinding, HotkeyCallback, HotkeyEvent, HotkeyRegistration,
    HotkeySource, PermissionKind, PlatformError,
};

const KVK_FUNCTION: u16 = 63;
const KVK_CAPS_LOCK: u16 = 57;
const CARBON_SIGNATURE: u32 = u32::from_be_bytes(*b"PLSC");
const K_EVENT_CLASS_KEYBOARD: u32 = u32::from_be_bytes(*b"keyb");
const K_EVENT_HOT_KEY_PRESSED: u32 = 5;
const K_EVENT_PARAM_DIRECT_OBJECT: u32 = u32::from_be_bytes(*b"----");
const TYPE_EVENT_HOT_KEY_ID: u32 = u32::from_be_bytes(*b"hkid");
const EVENT_NOT_HANDLED_ERR: i32 = -9874;

type ReadySender = mpsc::SyncSender<Result<SendRunLoop, PlatformError>>;

#[repr(C)]
#[derive(Clone, Copy)]
struct EventHotKeyID {
    signature: u32,
    id: u32,
}

#[repr(C)]
struct EventTypeSpec {
    event_class: u32,
    event_kind: u32,
}

type EventHandlerFn = unsafe extern "C" fn(*mut c_void, *mut c_void, *mut c_void) -> i32;

#[allow(unsafe_code)]
#[link(name = "Carbon", kind = "framework")]
extern "C" {
    fn GetEventDispatcherTarget() -> *mut c_void;
    fn InstallEventHandler(
        target: *mut c_void,
        handler: EventHandlerFn,
        num_types: usize,
        list: *const EventTypeSpec,
        user_data: *mut c_void,
        out_ref: *mut *mut c_void,
    ) -> i32;
    fn RegisterEventHotKey(
        hot_key_code: u32,
        hot_key_modifiers: u32,
        hot_key_id: EventHotKeyID,
        target: *mut c_void,
        options: u32,
        out_ref: *mut *mut c_void,
    ) -> i32;
    fn UnregisterEventHotKey(hot_key: *mut c_void) -> i32;
    fn GetEventParameter(
        event: *mut c_void,
        name: u32,
        desired_type: u32,
        out_actual_type: *mut u32,
        buffer_size: usize,
        out_actual_size: *mut usize,
        out_data: *mut c_void,
    ) -> i32;
}

static NEXT_CARBON_ID: AtomicU32 = AtomicU32::new(1);
static CARBON_HANDLERS: OnceLock<Mutex<HashMap<u32, HotkeyCallback>>> = OnceLock::new();
static CARBON_INSTALLED: OnceLock<Result<(), i32>> = OnceLock::new();

fn carbon_handlers() -> &'static Mutex<HashMap<u32, HotkeyCallback>> {
    CARBON_HANDLERS.get_or_init(|| Mutex::new(HashMap::new()))
}

fn invoke_callback_guarded(cb: &HotkeyCallback, event: HotkeyEvent) {
    if catch_unwind(AssertUnwindSafe(|| cb(event))).is_err() {
        tracing::error!("hotkey callback panicked; suppressed across FFI boundary");
    }
}

#[allow(unsafe_code)]
unsafe extern "C" fn carbon_hotkey_callback(
    _next_handler: *mut c_void,
    event: *mut c_void,
    _user_data: *mut c_void,
) -> i32 {
    let mut hk_id = EventHotKeyID {
        signature: 0,
        id: 0,
    };
    // SAFETY: `event` is supplied by HIToolbox for this handler; `hk_id` is a valid out buffer.
    let status = unsafe {
        GetEventParameter(
            event,
            K_EVENT_PARAM_DIRECT_OBJECT,
            TYPE_EVENT_HOT_KEY_ID,
            null_mut(),
            std::mem::size_of::<EventHotKeyID>(),
            null_mut(),
            (&raw mut hk_id).cast(),
        )
    };
    if status != 0 || hk_id.signature != CARBON_SIGNATURE {
        return EVENT_NOT_HANDLED_ERR;
    }
    let callback = lock_unpoisoned(carbon_handlers()).get(&hk_id.id).cloned();
    if let Some(cb) = callback {
        invoke_callback_guarded(&cb, HotkeyEvent::Toggle);
        0
    } else {
        EVENT_NOT_HANDLED_ERR
    }
}

fn ensure_carbon_handler_installed() -> Result<(), PlatformError> {
    let result = *CARBON_INSTALLED.get_or_init(|| {
        let spec = EventTypeSpec {
            event_class: K_EVENT_CLASS_KEYBOARD,
            event_kind: K_EVENT_HOT_KEY_PRESSED,
        };
        let mut handler_ref: *mut c_void = null_mut();
        #[allow(unsafe_code)]
        // SAFETY: `GetEventDispatcherTarget` returns the process dispatcher target; the handler
        // function has `'static` lifetime and uses no raw `user_data` pointer.
        let status = unsafe {
            InstallEventHandler(
                GetEventDispatcherTarget(),
                carbon_hotkey_callback,
                1,
                &spec,
                null_mut(),
                &mut handler_ref,
            )
        };
        if status == 0 {
            Ok(())
        } else {
            Err(status)
        }
    });
    result.map_err(|status| PlatformError::Hotkey(format!("InstallEventHandler failed ({status})")))
}

/// Probe whether macOS Carbon `RegisterEventHotKey` currently accepts `hotkey`.
///
/// Immediately unregisters on success so no stray binding remains active.
pub fn probe_carbon_hotkey(hotkey: pulsescribe_core::CarbonHotkey) -> bool {
    let id = NEXT_CARBON_ID.fetch_add(1, Ordering::Relaxed);
    let hk_id = EventHotKeyID {
        signature: CARBON_SIGNATURE,
        id,
    };
    let mut raw_ref: *mut c_void = null_mut();
    #[allow(unsafe_code)]
    // SAFETY: `raw_ref` is a valid out-pointer; any registered handle is unregistered immediately.
    let status = unsafe {
        RegisterEventHotKey(
            u32::from(hotkey.virtual_key),
            hotkey.modifier_mask,
            hk_id,
            GetEventDispatcherTarget(),
            0,
            &mut raw_ref,
        )
    };
    if status == 0 && !raw_ref.is_null() {
        #[allow(unsafe_code)]
        // SAFETY: `raw_ref` was returned by `RegisterEventHotKey` immediately above.
        unsafe {
            let _ = UnregisterEventHotKey(raw_ref);
        }
        true
    } else {
        false
    }
}

struct CarbonRegistration {
    id: u32,
    raw_ref: *mut c_void,
}

#[allow(unsafe_code)]
// SAFETY: `raw_ref` is an opaque Carbon `EventHotKeyRef` owned exclusively by this struct
// and released once via `UnregisterEventHotKey`.
unsafe impl Send for CarbonRegistration {}

impl CarbonRegistration {
    fn register(
        virtual_key: u16,
        modifier_mask: u32,
        on_event: HotkeyCallback,
    ) -> Result<Self, PlatformError> {
        ensure_carbon_handler_installed()?;
        let id = NEXT_CARBON_ID.fetch_add(1, Ordering::Relaxed);
        lock_unpoisoned(carbon_handlers()).insert(id, on_event);

        let hk_id = EventHotKeyID {
            signature: CARBON_SIGNATURE,
            id,
        };
        let mut raw_ref: *mut c_void = null_mut();
        #[allow(unsafe_code)]
        // SAFETY: `raw_ref` is a valid out-pointer; failure cleans up the handler map entry.
        let status = unsafe {
            RegisterEventHotKey(
                u32::from(virtual_key),
                modifier_mask,
                hk_id,
                GetEventDispatcherTarget(),
                0,
                &mut raw_ref,
            )
        };
        if status != 0 || raw_ref.is_null() {
            lock_unpoisoned(carbon_handlers()).remove(&id);
            return Err(PlatformError::Hotkey(format!(
                "RegisterEventHotKey failed (OSStatus={status})"
            )));
        }
        Ok(Self { id, raw_ref })
    }

    fn unregister(&mut self) {
        let raw_ref = std::mem::replace(&mut self.raw_ref, null_mut());
        if !raw_ref.is_null() {
            #[allow(unsafe_code)]
            // SAFETY: `raw_ref` was returned by `RegisterEventHotKey` and is unregistered once.
            unsafe {
                let _ = UnregisterEventHotKey(raw_ref);
            }
        }
        lock_unpoisoned(carbon_handlers()).remove(&self.id);
    }
}

impl Drop for CarbonRegistration {
    fn drop(&mut self) {
        self.unregister();
    }
}

/// Pure, testable matcher for `CGEventTap` keyboard and modifier-flag events.
#[derive(Debug)]
struct EventTapRule {
    keycode: u16,
    required_flags: CGEventFlags,
    modifier_flag: Option<CGEventFlags>,
    is_hold: bool,
    toggle_on_down_only: bool,
    active: bool,
    /// Tracks last observed modifier-flag state so toggling Caps Lock works even if it
    /// was already enabled before registration.
    last_modifier_state: Option<bool>,
}

impl EventTapRule {
    fn from_hotkey(raw: &str, is_hold: bool) -> Result<Self, PlatformError> {
        let parsed =
            parse_macos_hotkey(raw).map_err(|err| PlatformError::Hotkey(err.to_string()))?;
        let (modifier_flag, toggle_on_down_only) = match parsed.virtual_key {
            KVK_FUNCTION => (Some(CGEventFlags::MaskSecondaryFn), true),
            KVK_CAPS_LOCK => (Some(CGEventFlags::MaskAlphaShift), false),
            _ => (None, true),
        };
        Ok(Self {
            keycode: parsed.virtual_key,
            required_flags: carbon_modifiers_to_cg_flags(parsed.modifier_mask),
            modifier_flag,
            is_hold,
            toggle_on_down_only,
            active: false,
            last_modifier_state: None,
        })
    }

    fn handle(
        &mut self,
        event_type: CGEventType,
        keycode: u16,
        flags: CGEventFlags,
    ) -> Option<HotkeyEvent> {
        let mods_ok = flags.contains(self.required_flags);
        if let Some(mod_flag) = self.modifier_flag {
            if event_type != CGEventType::FlagsChanged {
                return None;
            }
            let is_down = flags.contains(mod_flag);
            let combo_down = is_down && mods_ok;

            if self.is_hold {
                if self.active {
                    if !combo_down {
                        self.active = false;
                        return Some(HotkeyEvent::HoldUp {
                            source: HotkeySource::EventTap,
                        });
                    }
                    return None;
                }
                let trigger_matches = keycode == self.keycode || !self.required_flags.is_empty();
                if combo_down && trigger_matches {
                    self.active = true;
                    return Some(HotkeyEvent::HoldDown {
                        source: HotkeySource::EventTap,
                    });
                }
                return None;
            }

            if keycode != self.keycode {
                if !combo_down {
                    self.active = false;
                }
                return None;
            }
            if self.toggle_on_down_only {
                let was_active = self.active;
                self.active = combo_down;
                return (combo_down && !was_active).then_some(HotkeyEvent::Toggle);
            }
            let changed = self.last_modifier_state != Some(is_down);
            self.last_modifier_state = Some(is_down);
            return (changed && mods_ok).then_some(HotkeyEvent::Toggle);
        }

        if event_type == CGEventType::FlagsChanged {
            if self.is_hold && self.active && !mods_ok {
                self.active = false;
                return Some(HotkeyEvent::HoldUp {
                    source: HotkeySource::EventTap,
                });
            }
            return None;
        }
        if keycode != self.keycode {
            return None;
        }
        match event_type {
            CGEventType::KeyDown if mods_ok && !self.active => {
                self.active = true;
                Some(if self.is_hold {
                    HotkeyEvent::HoldDown {
                        source: HotkeySource::EventTap,
                    }
                } else {
                    HotkeyEvent::Toggle
                })
            }
            CGEventType::KeyUp if self.active => {
                self.active = false;
                self.is_hold.then_some(HotkeyEvent::HoldUp {
                    source: HotkeySource::EventTap,
                })
            }
            _ => None,
        }
    }
}

fn carbon_modifiers_to_cg_flags(mask: u32) -> CGEventFlags {
    let mut flags = CGEventFlags::empty();
    if mask & CARBON_CMD_KEY != 0 {
        flags |= CGEventFlags::MaskCommand;
    }
    if mask & CARBON_SHIFT_KEY != 0 {
        flags |= CGEventFlags::MaskShift;
    }
    if mask & CARBON_OPTION_KEY != 0 {
        flags |= CGEventFlags::MaskAlternate;
    }
    if mask & CARBON_CONTROL_KEY != 0 {
        flags |= CGEventFlags::MaskControl;
    }
    flags
}

struct EventTapContext {
    rules: Vec<EventTapRule>,
    on_event: HotkeyCallback,
    tap_port: Option<CFRetained<CFMachPort>>,
}

/// Owns a heap-allocated `EventTapContext` strictly via raw pointer so `CGEvent::tap_create`
/// never aliases a live `Box` reference, and always frees it after the mach port is invalidated.
struct RawEventTapContext(*mut EventTapContext);

impl Drop for RawEventTapContext {
    fn drop(&mut self) {
        #[allow(unsafe_code)]
        // SAFETY: `self.0` was created via `Box::into_raw` and is only dropped after the tap
        // mach port has been invalidated and removed from its run loop.
        unsafe {
            drop(Box::from_raw(self.0));
        }
    }
}

#[allow(unsafe_code)]
unsafe extern "C-unwind" fn event_tap_callback(
    _proxy: CGEventTapProxy,
    event_type: CGEventType,
    event: NonNull<CGEvent>,
    user_info: *mut c_void,
) -> *mut CGEvent {
    if user_info.is_null() {
        return event.as_ptr();
    }
    // SAFETY: `user_info` points to the `EventTapContext` exclusively accessed on this run-loop
    // thread until `tap.invalidate()` runs after `CFRunLoop::run()` returns.
    let ctx = unsafe { &mut *user_info.cast::<EventTapContext>() };

    if event_type == CGEventType::TapDisabledByTimeout
        || event_type == CGEventType::TapDisabledByUserInput
    {
        if let Some(ref port) = ctx.tap_port {
            CGEvent::tap_enable(port, true);
        }
        return event.as_ptr();
    }

    // SAFETY: `event` is a valid `CGEvent` reference supplied by CoreGraphics for this callback.
    let event_ref = unsafe { event.as_ref() };
    let keycode =
        CGEvent::integer_value_field(Some(event_ref), CGEventField::KeyboardEventKeycode) as u16;
    let flags = CGEvent::flags(Some(event_ref));

    for rule in &mut ctx.rules {
        if let Some(hotkey_event) = rule.handle(event_type, keycode, flags) {
            invoke_callback_guarded(&ctx.on_event, hotkey_event);
        }
    }
    event.as_ptr()
}

struct SendRunLoop(CFRetained<CFRunLoop>);

#[allow(unsafe_code)]
// SAFETY: Apple documents `CFRunLoopStop` as thread-safe; this wrapper is only used to call `stop`.
unsafe impl Send for SendRunLoop {}

#[allow(unsafe_code)]
unsafe extern "C-unwind" fn on_runloop_entry(
    _observer: *mut CFRunLoopObserver,
    _activity: CFRunLoopActivity,
    info: *mut c_void,
) {
    if info.is_null() {
        return;
    }
    // SAFETY: `info` points to `ready_tx: Option<ReadySender>` on the tap thread's stack,
    // which outlives `CFRunLoop::run()` and is only accessed on this run-loop thread.
    let slot = unsafe { &mut *info.cast::<Option<ReadySender>>() };
    if let Some(tx) = slot.take() {
        if let Some(rl) = CFRunLoop::current() {
            let _ = tx.send(Ok(SendRunLoop(rl)));
        }
    }
}

struct EventTapRegistration {
    run_loop: Option<SendRunLoop>,
    thread: Option<JoinHandle<()>>,
}

impl EventTapRegistration {
    fn start(rules: Vec<EventTapRule>, on_event: HotkeyCallback) -> Result<Self, PlatformError> {
        let needs_keys = rules.iter().any(|r| r.modifier_flag.is_none());
        let mut mask = 1u64 << CGEventType::FlagsChanged.0;
        if needs_keys {
            mask |= (1u64 << CGEventType::KeyDown.0) | (1u64 << CGEventType::KeyUp.0);
        }

        let (tx, rx) = mpsc::sync_channel::<Result<SendRunLoop, PlatformError>>(1);
        let handle = thread::Builder::new()
            .name("pulsescribe-macos-event-tap".to_owned())
            .spawn(move || Self::run_tap_thread(rules, on_event, mask, tx))
            .map_err(|err| {
                PlatformError::Hotkey(format!("failed to spawn event-tap thread: {err}"))
            })?;

        match rx.recv() {
            Ok(Ok(run_loop)) => Ok(Self {
                run_loop: Some(run_loop),
                thread: Some(handle),
            }),
            Ok(Err(err)) => {
                let _ = handle.join();
                Err(err)
            }
            Err(_) => {
                let _ = handle.join();
                Err(PlatformError::Hotkey(
                    "event-tap startup channel closed".into(),
                ))
            }
        }
    }

    fn run_tap_thread(
        rules: Vec<EventTapRule>,
        on_event: HotkeyCallback,
        mask: u64,
        tx: ReadySender,
    ) {
        let ctx = RawEventTapContext(Box::into_raw(Box::new(EventTapContext {
            rules,
            on_event,
            tap_port: None,
        })));

        #[allow(unsafe_code)]
        // SAFETY: `ctx.0` stays alive via `RawEventTapContext` until after `tap.invalidate()`.
        let tap = unsafe {
            CGEvent::tap_create(
                CGEventTapLocation::HIDEventTap,
                CGEventTapPlacement::HeadInsertEventTap,
                CGEventTapOptions::ListenOnly,
                mask,
                Some(event_tap_callback),
                ctx.0.cast::<c_void>(),
            )
        };
        let Some(tap) = tap else {
            let _ = tx.send(Err(PlatformError::PermissionDenied(
                PermissionKind::InputMonitoring,
            )));
            return;
        };
        #[allow(unsafe_code)]
        // SAFETY: Run loop has not started yet, so no callback can access `ctx.0` concurrently.
        unsafe {
            (*ctx.0).tap_port = Some(tap.clone());
        }

        let Some(source) = CFMachPort::new_run_loop_source(None, Some(&tap), 0) else {
            tap.invalidate();
            let _ = tx.send(Err(PlatformError::Hotkey(
                "CFMachPortCreateRunLoopSource failed".into(),
            )));
            return;
        };
        let Some(run_loop) = CFRunLoop::current() else {
            tap.invalidate();
            let _ = tx.send(Err(PlatformError::Hotkey(
                "CFRunLoopGetCurrent failed".into(),
            )));
            return;
        };

        let mut ready_tx = Some(tx);
        #[allow(unsafe_code)]
        // SAFETY: `ready_tx` stays on this thread's stack across `CFRunLoop::run()`, and the
        // non-repeating `Entry` observer fires on this same thread before `remove_observer`.
        let observer = unsafe {
            run_loop.add_source(Some(&source), kCFRunLoopCommonModes);
            CGEvent::tap_enable(&tap, true);
            let mut obs_ctx = CFRunLoopObserverContext {
                version: 0,
                info: (&raw mut ready_tx).cast::<c_void>(),
                retain: None,
                release: None,
                copyDescription: None,
            };
            let obs = CFRunLoopObserver::new(
                None,
                CFRunLoopActivity::Entry.0,
                false,
                0,
                Some(on_runloop_entry),
                &mut obs_ctx,
            );
            let Some(obs) = obs else {
                run_loop.remove_source(Some(&source), kCFRunLoopCommonModes);
                tap.invalidate();
                if let Some(tx) = ready_tx.take() {
                    let _ = tx.send(Err(PlatformError::Hotkey(
                        "CFRunLoopObserverCreate failed".into(),
                    )));
                }
                return;
            };
            run_loop.add_observer(Some(&obs), kCFRunLoopDefaultMode);
            obs
        };

        CFRunLoop::run();

        #[allow(unsafe_code)]
        // SAFETY: Tear down observer, source, and mach port before `ctx` drops.
        unsafe {
            run_loop.remove_observer(Some(&observer), kCFRunLoopDefaultMode);
            run_loop.remove_source(Some(&source), kCFRunLoopCommonModes);
            tap.invalidate();
            (*ctx.0).tap_port = None;
        }
    }

    fn stop(&mut self) {
        if let Some(rl) = self.run_loop.take() {
            rl.0.stop();
        }
        if let Some(handle) = self.thread.take() {
            let _ = handle.join();
        }
    }
}

impl Drop for EventTapRegistration {
    fn drop(&mut self) {
        self.stop();
    }
}

struct MacOsHotkeyHandle {
    carbon: Option<CarbonRegistration>,
    event_tap: Option<EventTapRegistration>,
}

impl HotkeyRegistration for MacOsHotkeyHandle {
    fn unregister(&mut self) -> Result<(), PlatformError> {
        if let Some(mut carbon) = self.carbon.take() {
            carbon.unregister();
        }
        if let Some(mut tap) = self.event_tap.take() {
            tap.stop();
        }
        Ok(())
    }
}

/// Native macOS [`HotkeyBackend`] combining Carbon (toggle) and Quartz `CGEventTap` (hold/modifiers).
#[derive(Debug, Default, Clone, Copy)]
pub struct MacOsHotkeyBackend;

impl HotkeyBackend for MacOsHotkeyBackend {
    fn register(
        &self,
        binding: &HotkeyBinding,
        on_event: HotkeyCallback,
    ) -> Result<Box<dyn HotkeyRegistration>, PlatformError> {
        let mut carbon = None;
        let mut tap_rules = Vec::new();

        if let Some(ref toggle) = binding.toggle {
            let parsed =
                parse_macos_hotkey(toggle).map_err(|err| PlatformError::Hotkey(err.to_string()))?;
            if parsed.virtual_key == KVK_FUNCTION || parsed.virtual_key == KVK_CAPS_LOCK {
                tap_rules.push(EventTapRule::from_hotkey(toggle, false)?);
            } else {
                carbon = Some(CarbonRegistration::register(
                    parsed.virtual_key,
                    parsed.modifier_mask,
                    on_event.clone(),
                )?);
            }
        }

        if let Some(ref hold) = binding.hold {
            tap_rules.push(EventTapRule::from_hotkey(hold, true)?);
        }

        let event_tap = if tap_rules.is_empty() {
            None
        } else {
            Some(EventTapRegistration::start(tap_rules, on_event)?)
        };

        Ok(Box::new(MacOsHotkeyHandle { carbon, event_tap }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    #[test]
    fn event_tap_rule_handles_fn_capslock_and_combo_hold() {
        let mut fn_hold = EventTapRule::from_hotkey("fn", true).unwrap();
        assert_eq!(
            fn_hold.handle(
                CGEventType::FlagsChanged,
                KVK_FUNCTION,
                CGEventFlags::MaskSecondaryFn
            ),
            Some(HotkeyEvent::HoldDown {
                source: HotkeySource::EventTap
            })
        );
        assert_eq!(
            fn_hold.handle(
                CGEventType::FlagsChanged,
                KVK_FUNCTION,
                CGEventFlags::empty()
            ),
            Some(HotkeyEvent::HoldUp {
                source: HotkeySource::EventTap
            })
        );

        let mut caps_toggle = EventTapRule::from_hotkey("capslock", false).unwrap();
        assert_eq!(
            caps_toggle.handle(
                CGEventType::FlagsChanged,
                KVK_CAPS_LOCK,
                CGEventFlags::MaskAlphaShift
            ),
            Some(HotkeyEvent::Toggle)
        );
        assert_eq!(
            caps_toggle.handle(
                CGEventType::FlagsChanged,
                KVK_CAPS_LOCK,
                CGEventFlags::empty()
            ),
            Some(HotkeyEvent::Toggle)
        );

        // Caps Lock already enabled before registration: first press disables it and must still toggle.
        let mut caps_pre_enabled = EventTapRule::from_hotkey("capslock", false).unwrap();
        assert_eq!(
            caps_pre_enabled.handle(
                CGEventType::FlagsChanged,
                KVK_CAPS_LOCK,
                CGEventFlags::empty()
            ),
            Some(HotkeyEvent::Toggle)
        );
        // Repeated unchanged state must not re-fire.
        assert_eq!(
            caps_pre_enabled.handle(
                CGEventType::FlagsChanged,
                KVK_CAPS_LOCK,
                CGEventFlags::empty()
            ),
            None
        );

        // Modified Fn hold requires Command and ends when Command is released first.
        let mut cmd_fn_hold = EventTapRule::from_hotkey("cmd+fn", true).unwrap();
        assert_eq!(
            cmd_fn_hold.handle(
                CGEventType::FlagsChanged,
                KVK_FUNCTION,
                CGEventFlags::MaskSecondaryFn
            ),
            None
        );
        assert_eq!(
            cmd_fn_hold.handle(
                CGEventType::FlagsChanged,
                KVK_FUNCTION,
                CGEventFlags::MaskCommand | CGEventFlags::MaskSecondaryFn
            ),
            Some(HotkeyEvent::HoldDown {
                source: HotkeySource::EventTap
            })
        );
        assert_eq!(
            cmd_fn_hold.handle(
                CGEventType::FlagsChanged,
                55, // Command released while Fn stays held
                CGEventFlags::MaskSecondaryFn
            ),
            Some(HotkeyEvent::HoldUp {
                source: HotkeySource::EventTap
            })
        );

        // Modified Fn toggle ignores plain Fn.
        let mut cmd_fn_toggle = EventTapRule::from_hotkey("cmd+fn", false).unwrap();
        assert_eq!(
            cmd_fn_toggle.handle(
                CGEventType::FlagsChanged,
                KVK_FUNCTION,
                CGEventFlags::MaskSecondaryFn
            ),
            None
        );
        assert_eq!(
            cmd_fn_toggle.handle(
                CGEventType::FlagsChanged,
                KVK_FUNCTION,
                CGEventFlags::MaskCommand | CGEventFlags::MaskSecondaryFn
            ),
            Some(HotkeyEvent::Toggle)
        );

        let mut combo_hold = EventTapRule::from_hotkey("alt+space", true).unwrap();
        assert_eq!(
            combo_hold.handle(CGEventType::KeyDown, 49, CGEventFlags::MaskAlternate),
            Some(HotkeyEvent::HoldDown {
                source: HotkeySource::EventTap
            })
        );
        // Releasing modifier before the key still emits HoldUp.
        assert_eq!(
            combo_hold.handle(CGEventType::FlagsChanged, 58, CGEventFlags::empty()),
            Some(HotkeyEvent::HoldUp {
                source: HotkeySource::EventTap
            })
        );
    }

    #[test]
    fn carbon_hotkey_registers_and_unregisters_f19_live() {
        let backend = MacOsHotkeyBackend;
        let binding = HotkeyBinding::new(Some("f19".into()), None);
        let mut handle = backend
            .register(&binding, Arc::new(|_| {}))
            .expect("Carbon F19 registration should succeed without Accessibility");
        handle.unregister().unwrap();
    }

    #[test]
    fn fn_event_tap_starts_and_stops_cleanly_or_reports_permission_denied() {
        let backend = MacOsHotkeyBackend;
        let binding = HotkeyBinding::new(None, Some("fn".into()));
        match backend.register(&binding, Arc::new(|_| {})) {
            Ok(mut handle) => handle.unregister().unwrap(),
            Err(PlatformError::PermissionDenied(PermissionKind::InputMonitoring)) => {}
            Err(other) => panic!("unexpected event-tap error: {other}"),
        }
    }
}
