//! Typed daemon event stream shared by tray, native overlay, Tauri WebView, and IPC.

use pulsescribe_core::{AppState, DaemonStatusError, DeliveryOutcome};
use serde::{Deserialize, Serialize};
use tokio::sync::broadcast;

/// Default bounded capacity for [`DaemonEventBus`].
pub const DEFAULT_EVENT_BUS_CAPACITY: usize = 128;

/// Events emitted by the dictation daemon to subscribed UI and telemetry consumers.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum DaemonEvent {
    /// Lifecycle state transition (carries monotonically increasing `generation` for stale-timer safety).
    StateChanged {
        state: AppState,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        detail: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        error: Option<DaemonStatusError>,
        generation: u64,
    },
    /// Normalized microphone RMS/peak level in `[0.0, 1.0]` for the live waveform overlay.
    AudioLevel { level: f32 },
    /// Live streaming preview transcript for an active recording run.
    InterimText { run_id: u64, text: String },
    /// Completed normal dictation result after clipboard/paste delivery.
    TranscriptDelivered {
        run_id: u64,
        text: String,
        outcome: DeliveryOutcome,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        target_app: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        duration_secs: Option<f64>,
    },
    /// Completed onboarding/wizard test dictation result (never auto-pasted).
    TestTranscript {
        run_id: u64,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        ipc_cmd_id: Option<String>,
        text: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        error: Option<String>,
    },
    /// Active daemon configuration was reloaded from `~/.pulsescribe/.env`.
    ConfigReloaded,
    /// An external process (e.g., onboarding wizard via IPC) asked to open Settings.
    OpenSettingsRequested,
}

impl DaemonEvent {
    /// Construct a sanitized `AudioLevel` clamped to `[0.0, 1.0]` (mapping NaN/Inf to `0.0`).
    pub fn audio_level(level: f32) -> Self {
        let clamped = if level.is_finite() {
            level.clamp(0.0, 1.0)
        } else {
            0.0
        };
        Self::AudioLevel { level: clamped }
    }
}

/// Non-blocking multi-subscriber broadcast hub for [`DaemonEvent`]s.
#[derive(Debug, Clone)]
pub struct DaemonEventBus {
    tx: broadcast::Sender<DaemonEvent>,
}

impl Default for DaemonEventBus {
    fn default() -> Self {
        Self::new(DEFAULT_EVENT_BUS_CAPACITY)
    }
}

impl DaemonEventBus {
    pub fn new(capacity: usize) -> Self {
        let (tx, _) = broadcast::channel(capacity.max(1));
        Self { tx }
    }

    /// Publish an event to all current subscribers. Succeeds even when no subscriber is attached.
    pub fn publish(&self, event: DaemonEvent) {
        let _ = self.tx.send(event);
    }

    /// Subscribe to future daemon events.
    pub fn subscribe(&self) -> broadcast::Receiver<DaemonEvent> {
        self.tx.subscribe()
    }

    pub fn subscriber_count(&self) -> usize {
        self.tx.receiver_count()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn event_bus_fans_out_and_handles_zero_subscribers() {
        let bus = DaemonEventBus::default();
        // Must not panic or block with no receivers.
        bus.publish(DaemonEvent::ConfigReloaded);

        let mut rx1 = bus.subscribe();
        let mut rx2 = bus.subscribe();
        assert_eq!(bus.subscriber_count(), 2);

        let evt = DaemonEvent::StateChanged {
            state: AppState::Listening,
            detail: None,
            error: None,
            generation: 1,
        };
        bus.publish(evt.clone());

        assert_eq!(rx1.recv().await.unwrap(), evt);
        assert_eq!(rx2.recv().await.unwrap(), evt);

        assert_eq!(
            DaemonEvent::audio_level(f32::NAN),
            DaemonEvent::AudioLevel { level: 0.0 }
        );
        assert_eq!(
            DaemonEvent::audio_level(1.5),
            DaemonEvent::AudioLevel { level: 1.0 }
        );
        assert_eq!(
            DaemonEvent::audio_level(-0.2),
            DaemonEvent::AudioLevel { level: 0.0 }
        );
    }
}
