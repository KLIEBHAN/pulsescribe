//! Global hotkey registration trait and structured toggle/hold event contract.
//!
//! Designed to unify macOS Carbon (`RegisterEventHotKey`) + `CGEventTap` (`Fn` hold)
//! and Windows `WH_KEYBOARD_LL` (toggle + hold) behind one typed stream of [`HotkeyEvent`]s.

use std::sync::Arc;

use pulsescribe_core::HotkeyKind;

use crate::PlatformError;

/// Identifies which OS listener emitted a hold event (plugs into `HoldHotkeyState`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum HotkeySource {
    Carbon,
    EventTap,
    WindowsHook,
    Mock,
}

impl HotkeySource {
    /// Stable source identifier compatible with `pulsescribe_core::HoldHotkeyState`.
    pub const fn as_source_id(self) -> &'static str {
        match self {
            Self::Carbon => "carbon",
            Self::EventTap => "event_tap",
            Self::WindowsHook => "windows_hook",
            Self::Mock => "mock",
        }
    }
}

/// Typed event delivered by a [`HotkeyBackend`] to the daemon state machine.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum HotkeyEvent {
    /// Toggle hotkey pressed (start if idle, stop if recording).
    Toggle,
    /// Hold-to-record hotkey pressed by `source`.
    HoldDown { source: HotkeySource },
    /// Hold-to-record hotkey released by `source`.
    HoldUp { source: HotkeySource },
}

impl HotkeyEvent {
    pub const fn kind(self) -> HotkeyKind {
        match self {
            Self::Toggle => HotkeyKind::Toggle,
            Self::HoldDown { .. } | Self::HoldUp { .. } => HotkeyKind::Hold,
        }
    }
}

/// Configured global hotkey strings passed to a [`HotkeyBackend`].
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct HotkeyBinding {
    pub toggle: Option<String>,
    pub hold: Option<String>,
}

impl HotkeyBinding {
    pub fn new(toggle: Option<String>, hold: Option<String>) -> Self {
        Self {
            toggle: normalize_opt(toggle),
            hold: normalize_opt(hold),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.toggle.is_none() && self.hold.is_none()
    }
}

fn normalize_opt(value: Option<String>) -> Option<String> {
    value.and_then(|raw| {
        let trimmed = raw.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_owned())
        }
    })
}

/// Thread-safe callback invoked by OS hotkey hooks whenever a configured shortcut fires.
pub type HotkeyCallback = Arc<dyn Fn(HotkeyEvent) + Send + Sync>;

/// Handle representing an active global hotkey registration.
///
/// Implementations should also clean up OS registrations in `Drop`.
pub trait HotkeyRegistration: Send {
    fn unregister(&mut self) -> Result<(), PlatformError>;
}

/// Boundary for registering global toggle and hold hotkeys.
pub trait HotkeyBackend: Send + Sync {
    fn register(
        &self,
        binding: &HotkeyBinding,
        on_event: HotkeyCallback,
    ) -> Result<Box<dyn HotkeyRegistration>, PlatformError>;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lock_unpoisoned;
    use pulsescribe_core::HoldHotkeyState;
    use std::sync::Mutex;

    #[derive(Default)]
    struct StubHotkeyBackend {
        callback: Arc<Mutex<Option<HotkeyCallback>>>,
    }

    struct StubRegistration(Arc<Mutex<Option<HotkeyCallback>>>);

    impl HotkeyRegistration for StubRegistration {
        fn unregister(&mut self) -> Result<(), PlatformError> {
            *lock_unpoisoned(&self.0) = None;
            Ok(())
        }
    }

    impl HotkeyBackend for StubHotkeyBackend {
        fn register(
            &self,
            _binding: &HotkeyBinding,
            on_event: HotkeyCallback,
        ) -> Result<Box<dyn HotkeyRegistration>, PlatformError> {
            *lock_unpoisoned(&self.callback) = Some(on_event);
            Ok(Box::new(StubRegistration(Arc::clone(&self.callback))))
        }
    }

    #[test]
    fn hotkey_binding_trims_empty_entries() {
        let empty = HotkeyBinding::new(Some("   ".into()), None);
        assert!(empty.is_empty());

        let populated = HotkeyBinding::new(Some(" f19 ".into()), Some("fn".into()));
        assert!(!populated.is_empty());
        assert_eq!(populated.toggle.as_deref(), Some("f19"));
        assert_eq!(populated.hold.as_deref(), Some("fn"));
    }

    #[test]
    fn hotkey_events_integrate_with_core_hold_state() {
        let backend = StubHotkeyBackend::default();
        let hold_state = Arc::new(HoldHotkeyState::new());
        let observed = Arc::new(Mutex::new(Vec::new()));

        let hs = Arc::clone(&hold_state);
        let obs = Arc::clone(&observed);
        let mut reg = backend
            .register(
                &HotkeyBinding::new(Some("f19".into()), Some("fn".into())),
                Arc::new(move |event| {
                    lock_unpoisoned(&obs).push(event);
                    match event {
                        HotkeyEvent::Toggle => {}
                        HotkeyEvent::HoldDown { source } => {
                            if hs.should_start(source.as_source_id()) {
                                hs.mark_started();
                            }
                        }
                        HotkeyEvent::HoldUp { source } => {
                            let _ = hs.should_stop(source.as_source_id());
                        }
                    }
                }),
            )
            .unwrap();

        let emit = |ev| {
            if let Some(cb) = lock_unpoisoned(&backend.callback).clone() {
                cb(ev);
            }
        };

        emit(HotkeyEvent::HoldDown {
            source: HotkeySource::EventTap,
        });
        assert!(hold_state.is_active("event_tap"));
        assert!(hold_state.started_by_hold());

        emit(HotkeyEvent::HoldUp {
            source: HotkeySource::EventTap,
        });
        assert_eq!(hold_state.active_source_count(), 0);

        emit(HotkeyEvent::Toggle);
        assert_eq!(lock_unpoisoned(&observed).len(), 3);
        assert_eq!(HotkeyEvent::Toggle.kind(), HotkeyKind::Toggle);
        assert_eq!(
            HotkeyEvent::HoldDown {
                source: HotkeySource::WindowsHook
            }
            .kind(),
            HotkeyKind::Hold
        );

        reg.unregister().unwrap();
        assert!(lock_unpoisoned(&backend.callback).is_none());
    }
}
