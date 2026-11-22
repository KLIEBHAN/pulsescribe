//! Native 60 FPS non-activating floating recording overlay for PulseScribe (ADR-003 Phase 5).
//!
//! Splits pure state/animation/layout logic ([`model`]) from platform-native GPU renderers
//! (`macos`: AppKit `NSPanel` + Core Animation `CALayer`; `windows` added in PR 5.2 via Direct2D).
//! Keeps 0 FPS / 0 timer wakeups whenever the overlay is hidden, showing static status, or
//! settled on silence.

#[cfg(target_os = "macos")]
pub mod macos;
pub mod model;

use std::sync::Arc;

use pulsescribe_daemon::DaemonEvent;

pub use model::{
    OverlayActionEvent, OverlayCommand, OverlayModel, OverlaySnapshot, OverlayTick,
    OverlayTimerMode, OVERLAY_MARGIN_BOTTOM, WAVE_HEIGHT_UPDATE_EPSILON,
};

/// Internal transport trait letting [`OverlayHandle`] stay `Clone + Send + Sync` across backends.
pub(crate) trait OverlaySink: Send + Sync {
    fn send(&self, cmd: OverlayCommand);
}

struct NoopOverlaySink;

impl OverlaySink for NoopOverlaySink {
    fn send(&self, _cmd: OverlayCommand) {}
}

/// Thread-safe handle used by async daemon tasks to drive the native floating overlay.
#[derive(Clone)]
pub struct OverlayHandle {
    sink: Arc<dyn OverlaySink>,
}

impl OverlayHandle {
    /// Wrap a custom backend sink (used by native OS backends and unit tests).
    #[cfg(any(target_os = "macos", test))]
    pub(crate) fn from_sink(sink: Arc<dyn OverlaySink>) -> Self {
        Self { sink }
    }

    /// No-op overlay handle for headless daemons, tests, or unsupported platforms.
    pub fn noop() -> Self {
        Self {
            sink: Arc::new(NoopOverlaySink),
        }
    }

    /// Queue a typed [`OverlayCommand`] onto the native UI thread.
    pub fn send(&self, cmd: OverlayCommand) {
        self.sink.send(cmd);
    }

    /// Translate a [`DaemonEvent`] into the corresponding [`OverlayCommand`], if any.
    pub fn apply_daemon_event(&self, event: &DaemonEvent) {
        if let Some(cmd) = OverlayCommand::from_daemon_event(event) {
            self.send(cmd);
        }
    }

    /// Request dismissing any visible error/status overlay back to hidden.
    pub fn dismiss(&self) {
        self.send(OverlayCommand::Dismiss);
    }

    /// Tear down timers and hide the overlay cleanly on application shutdown.
    pub fn shutdown(&self) {
        self.send(OverlayCommand::Shutdown);
    }
}

impl Default for OverlayHandle {
    fn default() -> Self {
        Self::noop()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    struct RecordingSink(Mutex<Vec<OverlayCommand>>);

    impl OverlaySink for RecordingSink {
        fn send(&self, cmd: OverlayCommand) {
            self.0.lock().unwrap().push(cmd);
        }
    }

    #[test]
    fn handle_maps_daemon_events_and_control_helpers() {
        let sink = Arc::new(RecordingSink(Mutex::new(Vec::new())));
        let handle = OverlayHandle::from_sink(sink.clone());

        handle.apply_daemon_event(&DaemonEvent::audio_level(0.5));
        handle.apply_daemon_event(&DaemonEvent::ConfigReloaded);
        handle.dismiss();
        handle.shutdown();

        let recorded = sink.0.lock().unwrap();
        assert_eq!(
            *recorded,
            vec![
                OverlayCommand::AudioLevel(0.5),
                OverlayCommand::Dismiss,
                OverlayCommand::Shutdown,
            ]
        );
    }
}
