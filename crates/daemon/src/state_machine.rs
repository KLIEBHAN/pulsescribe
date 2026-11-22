//! Pure, deterministic state machine for the unified PulseScribe dictation daemon.
//!
//! Coordinates [`AppState`], multi-source [`HoldHotkeyState`], toggle/hold hotkeys,
//! onboarding test dictations (direct and via File-IPC), stale-run/generation guards,
//! watchdog timeouts, and deferred config reloads without performing OS or network I/O.

use std::time::Duration;

use pulsescribe_core::daemon_status_feedback::build_daemon_status_label;
use pulsescribe_core::transcript_delivery::{done_status, TEST_COMPLETE};
use pulsescribe_core::{
    AppState, DaemonErrorCode, DaemonStatusError, DeliveryOutcome, HoldHotkeyState,
};
use pulsescribe_platform::{HotkeyEvent, SoundName};

use crate::config::DaemonTimings;
use crate::events::DaemonEvent;
use crate::ipc::{IpcCommandKind, IpcStatus};

/// Whether a dictation run is a normal user dictation or an isolated onboarding test run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RunKind {
    Normal,
    Test { ipc_cmd_id: Option<String> },
}

/// Metadata for the currently active recording/transcription run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActiveRun {
    pub run_id: u64,
    pub kind: RunKind,
    pub target_app: Option<String>,
    pub capture_ready: bool,
    pub delivering: bool,
    pub started_by_hold: bool,
}

impl ActiveRun {
    pub const fn is_test(&self) -> bool {
        matches!(self.kind, RunKind::Test { .. })
    }

    pub fn ipc_cmd_id(&self) -> Option<&str> {
        match &self.kind {
            RunKind::Normal => None,
            RunKind::Test { ipc_cmd_id } => ipc_cmd_id.as_deref(),
        }
    }
}

/// Side-effect commands returned by [`DaemonStateMachine`] transitions for the runtime to execute.
#[derive(Debug, Clone, PartialEq)]
pub enum DaemonAction {
    PublishEvent(DaemonEvent),
    PlaySound(SoundName),
    StartCapture {
        run_id: u64,
        is_test: bool,
    },
    StopCapture {
        run_id: u64,
    },
    CancelCapture {
        run_id: u64,
    },
    DeliverTranscript {
        run_id: u64,
        text: String,
        target_app: Option<String>,
        duration_secs: Option<f64>,
    },
    SaveHistory {
        text: String,
        target_app: Option<String>,
    },
    SendIpcResponse {
        cmd_id: String,
        status: IpcStatus,
        transcript: String,
        error: Option<String>,
    },
    ScheduleWatchdog {
        run_id: u64,
        timeout: Duration,
    },
    CancelWatchdog,
    ScheduleIdleReset {
        generation: u64,
        delay: Duration,
    },
    ApplyPendingConfigReload,
}

/// Pure dictation daemon state machine shared across macOS and Windows.
#[derive(Debug)]
pub struct DaemonStateMachine {
    state: AppState,
    state_generation: u64,
    next_run_id: u64,
    active_run: Option<ActiveRun>,
    hold_state: HoldHotkeyState,
    test_hotkey_mode: bool,
    pending_config_reload: bool,
    timings: DaemonTimings,
}

impl Default for DaemonStateMachine {
    fn default() -> Self {
        Self::new(DaemonTimings::default())
    }
}

impl DaemonStateMachine {
    pub fn new(timings: DaemonTimings) -> Self {
        Self {
            state: AppState::Idle,
            state_generation: 0,
            next_run_id: 1,
            active_run: None,
            hold_state: HoldHotkeyState::new(),
            test_hotkey_mode: false,
            pending_config_reload: false,
            timings,
        }
    }

    pub const fn state(&self) -> AppState {
        self.state
    }

    pub const fn state_generation(&self) -> u64 {
        self.state_generation
    }

    pub fn active_run(&self) -> Option<&ActiveRun> {
        self.active_run.as_ref()
    }

    pub const fn is_idle_like(&self) -> bool {
        matches!(
            self.state,
            AppState::Idle | AppState::Done | AppState::NoSpeech | AppState::Error
        )
    }

    pub const fn is_capturing(&self) -> bool {
        matches!(self.state, AppState::Listening | AppState::Recording)
    }

    pub const fn is_processing(&self) -> bool {
        matches!(self.state, AppState::Transcribing | AppState::Refining)
    }

    pub fn update_timings(&mut self, timings: DaemonTimings) {
        self.timings = timings;
    }

    /// Enter `Loading` during startup prewarm or local model load when currently idle.
    pub fn on_loading_started(&mut self, detail: Option<String>) -> Vec<DaemonAction> {
        if self.active_run.is_some() || !self.is_idle_like() {
            return Vec::new();
        }
        let mut actions = Vec::new();
        self.transition_to(AppState::Loading, detail, None, &mut actions);
        actions
    }

    /// Leave `Loading` back to `Idle` once startup prewarm or model load completes.
    pub fn on_loading_finished(&mut self) -> Vec<DaemonAction> {
        if self.state != AppState::Loading {
            return Vec::new();
        }
        let mut actions = Vec::new();
        self.transition_to(AppState::Idle, None, None, &mut actions);
        self.flush_pending_reload_if_idle(&mut actions);
        actions
    }

    /// Route global hotkey presses to isolated test dictations while onboarding is active.
    pub fn set_test_hotkey_mode(&mut self, enabled: bool) {
        self.test_hotkey_mode = enabled;
    }

    /// Handle a global hotkey event from `pulsescribe-platform`.
    pub fn on_hotkey(
        &mut self,
        event: HotkeyEvent,
        target_app: Option<String>,
    ) -> Vec<DaemonAction> {
        match event {
            HotkeyEvent::Toggle => self.on_toggle(target_app),
            HotkeyEvent::HoldDown { source } => {
                let source_id = source.as_source_id();
                if !self.hold_state.should_start(source_id) {
                    return Vec::new();
                }
                if self.is_capturing()
                    && self.active_run.as_ref().is_some_and(|r| r.started_by_hold)
                {
                    // Additional hold source reporting the same in-flight hold: keep it tracked
                    // so capture only stops once the last active hold source releases.
                    return Vec::new();
                }
                if !self.is_idle_like() {
                    // Release tracking for this press since we cannot start right now.
                    let _ = self.hold_state.should_stop(source_id);
                    return self.busy_actions();
                }
                let kind = if self.test_hotkey_mode {
                    RunKind::Test { ipc_cmd_id: None }
                } else {
                    RunKind::Normal
                };
                self.start_run(kind, target_app, true)
            }
            HotkeyEvent::HoldUp { source } => {
                let source_id = source.as_source_id();
                if !self.hold_state.should_stop(source_id) {
                    return Vec::new();
                }
                if self.is_capturing() {
                    self.stop_active_capture()
                } else {
                    Vec::new()
                }
            }
        }
    }

    /// Explicitly start a normal dictation run (e.g., from tray menu).
    pub fn on_start_recording(&mut self, target_app: Option<String>) -> Vec<DaemonAction> {
        if !self.is_idle_like() {
            return self.busy_actions();
        }
        self.start_run(RunKind::Normal, target_app, false)
    }

    /// Explicitly stop an active recording run (normal or test).
    pub fn on_stop_recording(&mut self) -> Vec<DaemonAction> {
        if !self.is_capturing() {
            return Vec::new();
        }
        self.stop_active_capture()
    }

    /// Start an isolated test dictation run (no auto-paste, no history entry).
    pub fn on_start_test(&mut self, ipc_cmd_id: Option<String>) -> Vec<DaemonAction> {
        if !self.is_idle_like() {
            let mut actions = Vec::new();
            if let Some(cmd_id) = ipc_cmd_id {
                actions.push(DaemonAction::SendIpcResponse {
                    cmd_id,
                    status: IpcStatus::Error,
                    transcript: String::new(),
                    error: Some("Already recording or transcribing".to_owned()),
                });
            }
            return actions;
        }
        self.start_run(RunKind::Test { ipc_cmd_id }, None, false)
    }

    /// Cancel an active recording or test run, discarding any pending transcript.
    pub fn on_cancel(&mut self) -> Vec<DaemonAction> {
        let Some(run) = self.active_run.take() else {
            return Vec::new();
        };
        self.hold_state.reset();

        let mut actions = vec![
            DaemonAction::CancelCapture { run_id: run.run_id },
            DaemonAction::CancelWatchdog,
        ];
        if let Some(cmd_id) = run.ipc_cmd_id() {
            actions.push(DaemonAction::SendIpcResponse {
                cmd_id: cmd_id.to_owned(),
                status: IpcStatus::Stopped,
                transcript: String::new(),
                error: None,
            });
        }
        self.transition_to(AppState::Idle, None, None, &mut actions);
        self.flush_pending_reload_if_idle(&mut actions);
        actions
    }

    /// Handle a command polled from `~/.pulsescribe/ipc_command.json`.
    pub fn on_ipc_command(&mut self, cmd_id: String, kind: IpcCommandKind) -> Vec<DaemonAction> {
        match kind {
            IpcCommandKind::StartTest => self.on_start_test(Some(cmd_id)),
            IpcCommandKind::StopTest => {
                if self.is_capturing() && self.active_run.as_ref().is_some_and(ActiveRun::is_test) {
                    self.stop_active_capture()
                } else {
                    vec![DaemonAction::SendIpcResponse {
                        cmd_id,
                        status: IpcStatus::Stopped,
                        transcript: String::new(),
                        error: None,
                    }]
                }
            }
            IpcCommandKind::OpenSettings => {
                vec![DaemonAction::PublishEvent(
                    DaemonEvent::OpenSettingsRequested,
                )]
            }
            IpcCommandKind::Unknown(raw) => vec![DaemonAction::SendIpcResponse {
                cmd_id,
                status: IpcStatus::Error,
                transcript: String::new(),
                error: Some(format!("Unknown command: {raw}")),
            }],
        }
    }

    /// Notify the state machine that microphone capture is live for `run_id`.
    ///
    /// If the run was already cancelled/stopped before capture finished opening, instructs the
    /// runtime to tear down the just-opened capture stream immediately.
    pub fn on_capture_started(&mut self, run_id: u64) -> Vec<DaemonAction> {
        if !self.is_capturing() {
            return vec![DaemonAction::CancelCapture { run_id }];
        }
        let Some(run) = self.active_run.as_mut() else {
            return vec![DaemonAction::CancelCapture { run_id }];
        };
        if run.run_id != run_id {
            return vec![DaemonAction::CancelCapture { run_id }];
        }

        run.capture_ready = true;
        let ipc_cmd_id = run.ipc_cmd_id().map(ToOwned::to_owned);
        let started_by_hold = run.started_by_hold;

        let mut actions = Vec::new();
        if let Some(cmd_id) = ipc_cmd_id {
            actions.push(DaemonAction::SendIpcResponse {
                cmd_id,
                status: IpcStatus::Recording,
                transcript: String::new(),
                error: None,
            });
        }

        // Guard against any hold release that raced ahead of `on_capture_started`.
        if started_by_hold && self.hold_state.active_source_count() == 0 {
            actions.extend(self.stop_active_capture());
            return actions;
        }

        actions.push(DaemonAction::PlaySound(SoundName::Ready));
        actions
    }

    /// Notify the state machine that microphone startup failed for `run_id`.
    pub fn on_capture_failed(&mut self, run_id: u64, err: DaemonStatusError) -> Vec<DaemonAction> {
        self.on_run_failed(run_id, err)
    }

    /// Transition `Listening` -> `Recording` once live audio/speech is active.
    pub fn on_speech_detected(&mut self, run_id: u64) -> Vec<DaemonAction> {
        let Some(run) = self.active_run.as_ref() else {
            return Vec::new();
        };
        if run.run_id != run_id || self.state != AppState::Listening {
            return Vec::new();
        }
        let mut actions = Vec::new();
        self.transition_to(AppState::Recording, None, None, &mut actions);
        actions
    }

    /// Publish live streaming interim text if it belongs to the active run.
    pub fn on_interim_text(&mut self, run_id: u64, text: String) -> Vec<DaemonAction> {
        let Some(run) = self.active_run.as_ref() else {
            return Vec::new();
        };
        if run.run_id != run_id || (!self.is_capturing() && self.state != AppState::Transcribing) {
            return Vec::new();
        }
        let mut actions = Vec::new();
        if self.state == AppState::Listening && !text.trim().is_empty() {
            self.transition_to(AppState::Recording, None, None, &mut actions);
        }
        actions.push(DaemonAction::PublishEvent(DaemonEvent::InterimText {
            run_id,
            text,
        }));
        actions
    }

    /// Transition `Transcribing` -> `Refining` before calling the LLM refinement provider.
    pub fn on_refining_started(&mut self, run_id: u64) -> Vec<DaemonAction> {
        let Some(run) = self.active_run.as_ref() else {
            return Vec::new();
        };
        if run.run_id != run_id || self.state != AppState::Transcribing || run.delivering {
            return Vec::new();
        }
        let mut actions = Vec::new();
        self.transition_to(AppState::Refining, None, None, &mut actions);
        actions
    }

    /// Handle a finished transcription (and optional refinement) result for `run_id`.
    pub fn on_transcript_ready(
        &mut self,
        run_id: u64,
        text: String,
        duration_secs: Option<f64>,
    ) -> Vec<DaemonAction> {
        if !self.is_processing() {
            return Vec::new();
        }
        let Some(run) = self.active_run.as_ref() else {
            return Vec::new();
        };
        if run.run_id != run_id || run.delivering {
            return Vec::new();
        }

        let mut actions = vec![DaemonAction::CancelWatchdog];
        let trimmed = text.trim();

        if trimmed.is_empty() {
            let run = self.active_run.take().expect("checked above");
            self.hold_state.reset();
            if let Some(cmd_id) = run.ipc_cmd_id() {
                actions.push(DaemonAction::SendIpcResponse {
                    cmd_id: cmd_id.to_owned(),
                    status: IpcStatus::Done,
                    transcript: String::new(),
                    error: None,
                });
            }
            if run.is_test() {
                actions.push(DaemonAction::PublishEvent(DaemonEvent::TestTranscript {
                    run_id,
                    ipc_cmd_id: run.ipc_cmd_id().map(ToOwned::to_owned),
                    text: String::new(),
                    error: None,
                }));
            }
            let gen = self.transition_to(AppState::NoSpeech, None, None, &mut actions);
            actions.push(DaemonAction::ScheduleIdleReset {
                generation: gen,
                delay: self.timings.no_speech_reset_delay,
            });
            return actions;
        }

        if run.is_test() {
            let run = self.active_run.take().expect("checked above");
            self.hold_state.reset();
            let transcript = trimmed.to_owned();
            if let Some(cmd_id) = run.ipc_cmd_id() {
                actions.push(DaemonAction::SendIpcResponse {
                    cmd_id: cmd_id.to_owned(),
                    status: IpcStatus::Done,
                    transcript: transcript.clone(),
                    error: None,
                });
            }
            actions.push(DaemonAction::PublishEvent(DaemonEvent::TestTranscript {
                run_id,
                ipc_cmd_id: run.ipc_cmd_id().map(ToOwned::to_owned),
                text: transcript,
                error: None,
            }));
            actions.push(DaemonAction::PlaySound(SoundName::Done));
            let gen = self.transition_to(
                AppState::Done,
                Some(TEST_COMPLETE.to_owned()),
                None,
                &mut actions,
            );
            actions.push(DaemonAction::ScheduleIdleReset {
                generation: gen,
                delay: self.timings.done_display_hold,
            });
            return actions;
        }

        let run = self.active_run.as_mut().expect("checked above");
        run.delivering = true;
        let target_app = run.target_app.clone();
        let transcript = trimmed.to_owned();
        actions.push(DaemonAction::SaveHistory {
            text: transcript.clone(),
            target_app: target_app.clone(),
        });
        actions.push(DaemonAction::DeliverTranscript {
            run_id,
            text: transcript,
            target_app,
            duration_secs,
        });
        actions
    }

    /// Complete a normal dictation run after `TranscriptDelivery::deliver` finishes.
    pub fn on_delivery_finished(
        &mut self,
        run_id: u64,
        text: String,
        outcome: DeliveryOutcome,
        target_app: Option<String>,
        duration_secs: Option<f64>,
        show_rtf: bool,
    ) -> Vec<DaemonAction> {
        let Some(run) = self.active_run.as_ref() else {
            return Vec::new();
        };
        if run.run_id != run_id || !run.delivering {
            return Vec::new();
        }
        self.active_run = None;
        self.hold_state.reset();

        let mut actions = Vec::new();
        let display_secs = show_rtf.then_some(duration_secs).flatten();
        let status_text = done_status(&outcome.feedback, target_app.as_deref(), display_secs);

        actions.push(DaemonAction::PublishEvent(
            DaemonEvent::TranscriptDelivered {
                run_id,
                text,
                outcome: outcome.clone(),
                target_app,
                duration_secs,
            },
        ));

        if outcome.ok {
            actions.push(DaemonAction::PlaySound(SoundName::Done));
            let gen = self.transition_to(AppState::Done, Some(status_text), None, &mut actions);
            actions.push(DaemonAction::ScheduleIdleReset {
                generation: gen,
                delay: self.timings.done_display_hold,
            });
        } else {
            actions.push(DaemonAction::PlaySound(SoundName::Error));
            let gen = self.transition_to(AppState::Error, Some(status_text), None, &mut actions);
            actions.push(DaemonAction::ScheduleIdleReset {
                generation: gen,
                delay: self.timings.error_reset_delay,
            });
        }
        actions
    }

    /// Handle any runtime/provider/capture error for `run_id`.
    pub fn on_run_failed(&mut self, run_id: u64, err: DaemonStatusError) -> Vec<DaemonAction> {
        let Some(run) = self.active_run.as_ref() else {
            return Vec::new();
        };
        if run.run_id != run_id {
            return Vec::new();
        }
        let run = self.active_run.take().expect("checked above");
        self.hold_state.reset();

        let mut actions = vec![DaemonAction::CancelWatchdog];
        let raw_msg = err.to_string();
        if let Some(cmd_id) = run.ipc_cmd_id() {
            actions.push(DaemonAction::SendIpcResponse {
                cmd_id: cmd_id.to_owned(),
                status: IpcStatus::Error,
                transcript: String::new(),
                error: Some(raw_msg.clone()),
            });
        }
        if run.is_test() {
            actions.push(DaemonAction::PublishEvent(DaemonEvent::TestTranscript {
                run_id,
                ipc_cmd_id: run.ipc_cmd_id().map(ToOwned::to_owned),
                text: String::new(),
                error: Some(raw_msg),
            }));
        }

        let label = build_daemon_status_label(AppState::Error, &err, true, 80);
        actions.push(DaemonAction::PlaySound(SoundName::Error));
        let gen = self.transition_to(AppState::Error, Some(label), Some(err), &mut actions);
        actions.push(DaemonAction::ScheduleIdleReset {
            generation: gen,
            delay: self.timings.error_reset_delay,
        });
        actions
    }

    /// Handle a transcribing/refining watchdog timeout if `run_id` is still processing.
    pub fn on_watchdog_timeout(&mut self, run_id: u64) -> Vec<DaemonAction> {
        let Some(run) = self.active_run.as_ref() else {
            return Vec::new();
        };
        if run.run_id != run_id || !self.is_processing() || run.delivering {
            return Vec::new();
        }
        let mut actions = vec![DaemonAction::CancelCapture { run_id }];
        let err = DaemonStatusError::new(
            DaemonErrorCode::Timeout,
            Some("Transcription timed out".to_owned()),
        );
        actions.extend(self.on_run_failed(run_id, err));
        actions
    }

    /// Return to `Idle` when a scheduled feedback timer fires and no newer state change occurred.
    pub fn on_reset_timer(&mut self, generation: u64) -> Vec<DaemonAction> {
        if self.state_generation != generation
            || !self.is_idle_like()
            || self.state == AppState::Idle
        {
            return Vec::new();
        }
        let mut actions = Vec::new();
        self.transition_to(AppState::Idle, None, None, &mut actions);
        self.flush_pending_reload_if_idle(&mut actions);
        actions
    }

    /// Request a configuration reload; applied immediately when idle or deferred until idle.
    pub fn on_config_reload_requested(&mut self) -> Vec<DaemonAction> {
        if self.active_run.is_none() && self.is_idle_like() {
            self.pending_config_reload = false;
            vec![DaemonAction::ApplyPendingConfigReload]
        } else {
            self.pending_config_reload = true;
            Vec::new()
        }
    }

    fn on_toggle(&mut self, target_app: Option<String>) -> Vec<DaemonAction> {
        if self.is_capturing() {
            return self.stop_active_capture();
        }
        if !self.is_idle_like() {
            return self.busy_actions();
        }
        let kind = if self.test_hotkey_mode {
            RunKind::Test { ipc_cmd_id: None }
        } else {
            RunKind::Normal
        };
        self.start_run(kind, target_app, false)
    }

    fn start_run(
        &mut self,
        kind: RunKind,
        target_app: Option<String>,
        started_by_hold: bool,
    ) -> Vec<DaemonAction> {
        let run_id = self.next_run_id;
        self.next_run_id = self.next_run_id.saturating_add(1);
        let is_test = matches!(kind, RunKind::Test { .. });

        if started_by_hold {
            self.hold_state.mark_started();
        } else {
            self.hold_state.reset();
        }

        self.active_run = Some(ActiveRun {
            run_id,
            kind,
            target_app,
            capture_ready: false,
            delivering: false,
            started_by_hold,
        });

        let mut actions = Vec::new();
        self.transition_to(AppState::Listening, None, None, &mut actions);
        actions.push(DaemonAction::StartCapture { run_id, is_test });
        actions
    }

    fn stop_active_capture(&mut self) -> Vec<DaemonAction> {
        let Some(run) = self.active_run.as_ref() else {
            return Vec::new();
        };
        if !run.capture_ready {
            // Stop arrived before the microphone stream opened (0 ms captured). Cancel cleanly
            // instead of entering `Transcribing` where no audio stream exists to deliver a result.
            let run = self.active_run.take().expect("checked above");
            self.hold_state.reset();
            let mut actions = vec![DaemonAction::CancelCapture { run_id: run.run_id }];
            if let Some(cmd_id) = run.ipc_cmd_id() {
                actions.push(DaemonAction::SendIpcResponse {
                    cmd_id: cmd_id.to_owned(),
                    status: IpcStatus::Stopped,
                    transcript: String::new(),
                    error: None,
                });
            }
            if run.is_test() {
                self.transition_to(AppState::Idle, None, None, &mut actions);
                self.flush_pending_reload_if_idle(&mut actions);
            } else {
                let gen = self.transition_to(AppState::NoSpeech, None, None, &mut actions);
                actions.push(DaemonAction::ScheduleIdleReset {
                    generation: gen,
                    delay: self.timings.no_speech_reset_delay,
                });
            }
            return actions;
        }

        let run_id = run.run_id;
        self.hold_state.reset();

        let mut actions = Vec::new();
        actions.push(DaemonAction::PlaySound(SoundName::Stop));
        self.transition_to(AppState::Transcribing, None, None, &mut actions);
        actions.push(DaemonAction::StopCapture { run_id });
        actions.push(DaemonAction::ScheduleWatchdog {
            run_id,
            timeout: self.timings.transcribing_timeout,
        });
        actions
    }

    fn busy_actions(&self) -> Vec<DaemonAction> {
        // Non-destructive feedback: keep current transcribing/refining state untouched.
        vec![DaemonAction::PlaySound(SoundName::Error)]
    }

    fn transition_to(
        &mut self,
        next: AppState,
        detail: Option<String>,
        error: Option<DaemonStatusError>,
        actions: &mut Vec<DaemonAction>,
    ) -> u64 {
        self.state = next;
        self.state_generation = self.state_generation.saturating_add(1);
        let generation = self.state_generation;
        actions.push(DaemonAction::PublishEvent(DaemonEvent::StateChanged {
            state: next,
            detail,
            error,
            generation,
        }));
        generation
    }

    fn flush_pending_reload_if_idle(&mut self, actions: &mut Vec<DaemonAction>) {
        if self.pending_config_reload && self.active_run.is_none() && self.is_idle_like() {
            self.pending_config_reload = false;
            actions.push(DaemonAction::ApplyPendingConfigReload);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pulsescribe_core::transcript_delivery::{PASTED, PASTE_FAILED_MACOS};
    use pulsescribe_platform::HotkeySource;

    #[test]
    fn toggle_happy_path_delivers_saves_history_and_resets_to_idle() {
        let mut sm = DaemonStateMachine::default();
        assert_eq!(sm.state(), AppState::Idle);

        let start = sm.on_hotkey(HotkeyEvent::Toggle, Some("Mail".into()));
        let run_id = sm.active_run().unwrap().run_id;
        assert_eq!(sm.state(), AppState::Listening);
        assert!(start.contains(&DaemonAction::StartCapture {
            run_id,
            is_test: false,
        }));

        let ready = sm.on_capture_started(run_id);
        assert!(ready.contains(&DaemonAction::PlaySound(SoundName::Ready)));

        let speech = sm.on_speech_detected(run_id);
        assert_eq!(sm.state(), AppState::Recording);
        assert_eq!(speech.len(), 1);

        let stop = sm.on_hotkey(HotkeyEvent::Toggle, None);
        assert_eq!(sm.state(), AppState::Transcribing);
        assert!(stop.contains(&DaemonAction::StopCapture { run_id }));
        assert!(stop.iter().any(
            |a| matches!(a, DaemonAction::ScheduleWatchdog { run_id: r, .. } if *r == run_id)
        ));

        // Toggle while transcribing does not clobber active run.
        let busy = sm.on_hotkey(HotkeyEvent::Toggle, None);
        assert_eq!(sm.state(), AppState::Transcribing);
        assert_eq!(busy, vec![DaemonAction::PlaySound(SoundName::Error)]);

        let _ = sm.on_refining_started(run_id);
        assert_eq!(sm.state(), AppState::Refining);

        let transcript = sm.on_transcript_ready(run_id, "  Hallo Welt  ".into(), Some(0.8));
        assert!(transcript.contains(&DaemonAction::CancelWatchdog));
        assert!(transcript.contains(&DaemonAction::SaveHistory {
            text: "Hallo Welt".into(),
            target_app: Some("Mail".into()),
        }));
        assert!(transcript.contains(&DaemonAction::DeliverTranscript {
            run_id,
            text: "Hallo Welt".into(),
            target_app: Some("Mail".into()),
            duration_secs: Some(0.8),
        }));

        let delivered = sm.on_delivery_finished(
            run_id,
            "Hallo Welt".into(),
            DeliveryOutcome {
                ok: true,
                feedback: PASTED.into(),
            },
            Some("Mail".into()),
            Some(0.8),
            true,
        );
        assert_eq!(sm.state(), AppState::Done);
        assert!(sm.active_run().is_none());
        let done_gen = sm.state_generation();
        assert!(delivered.iter().any(|a| matches!(
            a,
            DaemonAction::PublishEvent(DaemonEvent::StateChanged {
                state: AppState::Done,
                detail: Some(d),
                ..
            }) if d == "Transcript pasted into Mail · 0.8 s"
        )));

        // Stale reset generation is ignored; matching generation returns to Idle.
        assert!(sm.on_reset_timer(done_gen - 1).is_empty());
        assert_eq!(sm.state(), AppState::Done);
        let _ = sm.on_reset_timer(done_gen);
        assert_eq!(sm.state(), AppState::Idle);
    }

    #[test]
    fn hold_handles_multi_source_and_early_release_before_capture_started() {
        let mut sm = DaemonStateMachine::default();

        // Press from Carbon and EventTap: releasing only one keeps recording alive.
        let _ = sm.on_hotkey(
            HotkeyEvent::HoldDown {
                source: HotkeySource::Carbon,
            },
            None,
        );
        let run_id = sm.active_run().unwrap().run_id;
        let _ = sm.on_capture_started(run_id);
        let _ = sm.on_hotkey(
            HotkeyEvent::HoldDown {
                source: HotkeySource::EventTap,
            },
            None,
        );
        assert!(sm
            .on_hotkey(
                HotkeyEvent::HoldUp {
                    source: HotkeySource::Carbon
                },
                None
            )
            .is_empty());
        assert_eq!(sm.state(), AppState::Listening);

        let stop = sm.on_hotkey(
            HotkeyEvent::HoldUp {
                source: HotkeySource::EventTap,
            },
            None,
        );
        assert_eq!(sm.state(), AppState::Transcribing);
        assert!(stop.contains(&DaemonAction::StopCapture { run_id }));

        // Finish first run cleanly.
        let _ = sm.on_transcript_ready(run_id, String::new(), None);
        assert_eq!(sm.state(), AppState::NoSpeech);

        // Press & release BEFORE on_capture_started arrives (early-release race):
        // must cancel cleanly to NoSpeech instead of getting stuck in Transcribing.
        let _ = sm.on_hotkey(
            HotkeyEvent::HoldDown {
                source: HotkeySource::WindowsHook,
            },
            None,
        );
        let run2 = sm.active_run().unwrap().run_id;
        let stop_early = sm.on_hotkey(
            HotkeyEvent::HoldUp {
                source: HotkeySource::WindowsHook,
            },
            None,
        );
        assert_eq!(sm.state(), AppState::NoSpeech);
        assert!(sm.active_run().is_none());
        assert!(stop_early.contains(&DaemonAction::CancelCapture { run_id: run2 }));

        // Late on_capture_started for already-cancelled run tears down stream without touching state.
        assert_eq!(
            sm.on_capture_started(run2),
            vec![DaemonAction::CancelCapture { run_id: run2 }]
        );
        assert_eq!(sm.state(), AppState::NoSpeech);
    }

    #[test]
    fn ipc_test_run_bypasses_paste_and_history_and_reports_status() {
        let mut sm = DaemonStateMachine::default();

        let start = sm.on_ipc_command("cmd12345".into(), IpcCommandKind::StartTest);
        let run_id = sm.active_run().unwrap().run_id;
        assert!(start.contains(&DaemonAction::StartCapture {
            run_id,
            is_test: true,
        }));

        // Concurrent StartTest while active returns IPC error without interrupting current run.
        let dup = sm.on_ipc_command("cmd99999".into(), IpcCommandKind::StartTest);
        assert!(dup.iter().any(|a| matches!(
            a,
            DaemonAction::SendIpcResponse {
                cmd_id,
                status: IpcStatus::Error,
                ..
            } if cmd_id == "cmd99999"
        )));

        let ready = sm.on_capture_started(run_id);
        assert!(ready.contains(&DaemonAction::SendIpcResponse {
            cmd_id: "cmd12345".into(),
            status: IpcStatus::Recording,
            transcript: String::new(),
            error: None,
        }));

        let _ = sm.on_ipc_command("stop0001".into(), IpcCommandKind::StopTest);
        assert_eq!(sm.state(), AppState::Transcribing);

        let done = sm.on_transcript_ready(run_id, "Test eins zwei".into(), Some(0.4));
        assert!(!done
            .iter()
            .any(|a| matches!(a, DaemonAction::DeliverTranscript { .. })));
        assert!(!done
            .iter()
            .any(|a| matches!(a, DaemonAction::SaveHistory { .. })));
        assert!(done.contains(&DaemonAction::SendIpcResponse {
            cmd_id: "cmd12345".into(),
            status: IpcStatus::Done,
            transcript: "Test eins zwei".into(),
            error: None,
        }));
        assert_eq!(sm.state(), AppState::Done);
    }

    #[test]
    fn stale_watchdog_and_deferred_config_reload_are_safe() {
        let mut sm = DaemonStateMachine::default();
        let _ = sm.on_start_recording(None);
        let run1 = sm.active_run().unwrap().run_id;
        let _ = sm.on_capture_started(run1);

        // Config reload during recording is deferred until Idle.
        assert!(sm.on_config_reload_requested().is_empty());

        let _ = sm.on_stop_recording();
        assert_eq!(sm.state(), AppState::Transcribing);

        // Watchdog fires for run1 -> transitions to Error and cancels capture.
        let timed_out = sm.on_watchdog_timeout(run1);
        assert_eq!(sm.state(), AppState::Error);
        assert!(timed_out.contains(&DaemonAction::CancelCapture { run_id: run1 }));
        assert!(sm.active_run().is_none());

        // Start a new run while Error is displayed; stale watchdog from run1 is ignored.
        let _ = sm.on_start_recording(None);
        let run2 = sm.active_run().unwrap().run_id;
        assert!(sm.on_watchdog_timeout(run1).is_empty());
        assert_eq!(sm.state(), AppState::Listening);

        // Cancel run2 -> immediately resets to Idle AND flushes deferred config reload!
        let cancelled = sm.on_cancel();
        assert_eq!(sm.state(), AppState::Idle);
        assert!(cancelled.contains(&DaemonAction::CancelCapture { run_id: run2 }));
        assert!(cancelled.contains(&DaemonAction::ApplyPendingConfigReload));
    }

    #[test]
    fn paste_fallback_failure_uses_error_hold_duration() {
        let mut sm = DaemonStateMachine::default();
        let _ = sm.on_start_recording(None);
        let run_id = sm.active_run().unwrap().run_id;
        let _ = sm.on_capture_started(run_id);
        let _ = sm.on_stop_recording();
        let _ = sm.on_transcript_ready(run_id, "Text".into(), None);

        let actions = sm.on_delivery_finished(
            run_id,
            "Text".into(),
            DeliveryOutcome {
                ok: false,
                feedback: PASTE_FAILED_MACOS.into(),
            },
            None,
            None,
            false,
        );
        assert_eq!(sm.state(), AppState::Error);
        assert!(actions.contains(&DaemonAction::PlaySound(SoundName::Error)));
        assert!(actions.iter().any(|a| matches!(
            a,
            DaemonAction::ScheduleIdleReset { delay, .. } if *delay == sm.timings.error_reset_delay
        )));
    }

    #[test]
    fn delivering_guards_against_duplicate_transcript_and_late_watchdog_and_stop_test_ignores_normal(
    ) {
        let mut sm = DaemonStateMachine::default();

        // Loading lifecycle transitions cleanly and blocks recording until finished.
        let _ = sm.on_loading_started(Some("Starting up...".into()));
        assert_eq!(sm.state(), AppState::Loading);
        assert_eq!(
            sm.on_start_recording(None),
            vec![DaemonAction::PlaySound(SoundName::Error)]
        );
        let _ = sm.on_loading_finished();
        assert_eq!(sm.state(), AppState::Idle);

        // Normal recording is not interrupted by an external IPC StopTest command.
        let _ = sm.on_start_recording(Some("Notes".into()));
        let run_id = sm.active_run().unwrap().run_id;
        let _ = sm.on_capture_started(run_id);
        let stop_test = sm.on_ipc_command("ipc-stop".into(), IpcCommandKind::StopTest);
        assert_eq!(sm.state(), AppState::Listening);
        assert!(stop_test.contains(&DaemonAction::SendIpcResponse {
            cmd_id: "ipc-stop".into(),
            status: IpcStatus::Stopped,
            transcript: String::new(),
            error: None,
        }));

        // Premature transcript while still capturing is ignored.
        assert!(sm
            .on_transcript_ready(run_id, "Too early".into(), None)
            .is_empty());

        let _ = sm.on_stop_recording();
        let first = sm.on_transcript_ready(run_id, "Delivered once".into(), None);
        assert!(first
            .iter()
            .any(|a| matches!(a, DaemonAction::DeliverTranscript { .. })));

        // Duplicate transcript or late watchdog during delivery are both ignored.
        assert!(sm
            .on_transcript_ready(run_id, "Duplicate".into(), None)
            .is_empty());
        assert!(sm.on_watchdog_timeout(run_id).is_empty());
    }
}
