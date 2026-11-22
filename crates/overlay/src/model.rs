//! Platform-neutral overlay state, adaptive 60/24/0 Hz animation pacing, and layout view-model.
//!
//! Mirrors `ui/overlay.py` + `crates/core/src/{animation,overlay_feedback,overlay_style,overlay_text}.rs`
//! so macOS (`CALayer`) and Windows (`Direct2D`) renderers stay thin, identical, and 0 FPS idle.

use std::time::{Duration, Instant};

use pulsescribe_core::animation::{
    listening_bar_heights, listening_bar_rest_heights, listening_level, AnimationLogic, BAR_COUNT,
    BAR_MAX_HEIGHT, BAR_MIN_HEIGHT,
};
use pulsescribe_core::overlay_feedback::{
    describe_overlay_content, is_recording_clock_state, OverlayContent,
};
use pulsescribe_core::overlay_style::{
    shimmer_track, OverlayAction, OverlayBarMode, ShimmerTrack, LIVE_TEXT_HEIGHT, LIVE_TEXT_LINES,
    LIVE_TEXT_WIDTH, OVERLAY_HEIGHT, OVERLAY_WIDTH,
};
use pulsescribe_core::overlay_text::{format_recording_duration, wrap_interim_text, InterimLines};
use pulsescribe_core::{AppState, DaemonStatusError};
use pulsescribe_daemon::DaemonEvent;

/// Distance above the bottom edge of the active display (clears the macOS Dock / Windows taskbar).
pub const OVERLAY_MARGIN_BOTTOM: f64 = 110.0;

/// Skip per-bar GPU layer updates for subpixel changes smaller than this threshold.
pub const WAVE_HEIGHT_UPDATE_EPSILON: f64 = 0.25;

/// Active animation cadence when speech energy is present.
pub const ACTIVE_FPS: f64 = 60.0;

/// Lower cadence used while bars decay toward silence before stopping completely.
pub const DECAY_FPS: f64 = 24.0;

/// Conservative per-line character budget for the 2-line live interim transcript box inside
/// `LIVE_TEXT_WIDTH` (560 px) at 13 pt so newest trailing words never wrap onto a clipped 3rd line.
pub const LIVE_TEXT_MAX_COLS: usize = 44;

const LEVEL_SETTLED_EPSILON: f64 = 0.001;
const LEVEL_ACTIVE_THRESHOLD: f64 = 0.035;
const LEVEL_DECAY_THRESHOLD: f64 = 0.02;
const MAX_OVERLAY_STATUS_CHARS: usize = 72;

/// Commands sent from background Tokio/daemon threads to the native overlay thread.
#[derive(Debug, Clone, PartialEq)]
pub enum OverlayCommand {
    ApplyState {
        state: AppState,
        detail: Option<String>,
        error: Option<DaemonStatusError>,
    },
    AudioLevel(f32),
    InterimText(String),
    Dismiss,
    Shutdown,
}

impl OverlayCommand {
    /// Map a [`DaemonEvent`] to an [`OverlayCommand`] (`None` for non-visual events).
    pub fn from_daemon_event(event: &DaemonEvent) -> Option<Self> {
        match event {
            DaemonEvent::StateChanged {
                state,
                detail,
                error,
                ..
            } => Some(Self::ApplyState {
                state: *state,
                detail: detail.clone(),
                error: error.clone(),
            }),
            DaemonEvent::AudioLevel { level } => Some(Self::AudioLevel(*level)),
            DaemonEvent::InterimText { text, .. } => Some(Self::InterimText(text.clone())),
            DaemonEvent::TranscriptDelivered { .. }
            | DaemonEvent::TestTranscript { .. }
            | DaemonEvent::ConfigReloaded
            | DaemonEvent::OpenSettingsRequested => None,
        }
    }
}

/// Interactive click callbacks emitted by the overlay back to the desktop shell.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum OverlayActionEvent {
    OpenSettings,
    Dismissed,
}

/// Desired OS animation timer cadence (`Stopped` = 0 FPS, 0 wakeups).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Hash)]
pub enum OverlayTimerMode {
    #[default]
    Stopped,
    Decay24Hz,
    Active60Hz,
}

impl OverlayTimerMode {
    pub fn interval(self) -> Option<Duration> {
        match self {
            Self::Stopped => None,
            Self::Decay24Hz => Some(Duration::from_secs_f64(1.0 / DECAY_FPS)),
            Self::Active60Hz => Some(Duration::from_secs_f64(1.0 / ACTIVE_FPS)),
        }
    }
}

/// Immutable snapshot of what the native renderer should display right now.
#[derive(Debug, Clone, PartialEq)]
pub struct OverlaySnapshot {
    pub visible: bool,
    pub state: AppState,
    pub content: OverlayContent,
    /// Displayed text (reflow-free wrapped live interim lines during `Recording`, else `content.text`).
    pub display_text: String,
    /// `true` when displaying live interim transcript (expands to 2-line wide pill).
    pub live_text_active: bool,
    /// Number of visible lines in `display_text` (`1` or `2`).
    pub live_line_count: usize,
    /// Displayed right-aligned meta (`m:ss` recording clock in `Listening`/`Recording`, else `content.meta`).
    pub display_meta: String,
    /// Clickable meta action (`OverlayAction::Settings`) when applicable.
    pub action: Option<OverlayAction>,
    /// `true` when mouse events should be enabled (clickable `Settings` link or `×` dismiss button).
    pub interactive: bool,
    /// Preferred base pill size `(width, height)`.
    pub base_size: (f64, f64),
    /// Whether persistent messages may grow horizontally beyond `base_size.0`.
    pub allow_grow: bool,
    /// Current heights for all 10 equalizer bars (`px`).
    pub bar_heights: [f64; BAR_COUNT],
    /// Shimmer track metadata when in `Line` / `Phase1` / `Phase2` processing modes.
    pub shimmer: Option<ShimmerTrack>,
    /// Required animation timer cadence (`Stopped` = 0 FPS).
    pub timer_mode: OverlayTimerMode,
    /// Whether the 1 Hz recording duration clock timer should run.
    pub clock_active: bool,
}

/// Incremental result returned by [`OverlayModel::tick_animation`].
#[derive(Debug, Clone, PartialEq)]
pub struct OverlayTick {
    /// Per-bar mask indicating which bars moved by at least [`WAVE_HEIGHT_UPDATE_EPSILON`].
    pub changed_bars: [bool; BAR_COUNT],
    pub any_bar_changed: bool,
    pub bar_heights: [f64; BAR_COUNT],
    pub timer_mode: OverlayTimerMode,
}

/// Pure, deterministic overlay controller shared across macOS and Windows native renderers.
#[derive(Debug, Clone)]
pub struct OverlayModel {
    state: AppState,
    content: OverlayContent,
    animation: AnimationLogic,
    latest_rms: f64,
    recording_started_at: Option<Instant>,
    anim_origin: Instant,
    interim_lines: InterimLines,
    bar_heights: [f64; BAR_COUNT],
    visible: bool,
    timer_mode: OverlayTimerMode,
    shutdown: bool,
}

impl OverlayModel {
    pub fn new(now: Instant) -> Self {
        Self {
            state: AppState::Idle,
            content: OverlayContent::default(),
            animation: AnimationLogic::new(),
            latest_rms: 0.0,
            recording_started_at: None,
            anim_origin: now,
            interim_lines: InterimLines::default(),
            bar_heights: [BAR_MIN_HEIGHT; BAR_COUNT],
            visible: false,
            timer_mode: OverlayTimerMode::Stopped,
            shutdown: false,
        }
    }

    pub fn is_shutdown(&self) -> bool {
        self.shutdown
    }

    pub fn visible(&self) -> bool {
        self.visible
    }

    pub fn state(&self) -> AppState {
        self.state
    }

    pub fn timer_mode(&self) -> OverlayTimerMode {
        self.timer_mode
    }

    pub fn bar_heights(&self) -> [f64; BAR_COUNT] {
        self.bar_heights
    }

    /// Apply one [`OverlayCommand`] without allocating a snapshot.
    ///
    /// Returns `true` if visual layout, visibility, or displayed text changed and the renderer
    /// needs a full [`OverlaySnapshot`] pass; returns `false` for hot-path `AudioLevel` updates
    /// that only need timer pacing sync.
    pub fn apply_command_mut(&mut self, cmd: OverlayCommand, now: Instant) -> bool {
        if self.shutdown {
            return false;
        }
        match cmd {
            OverlayCommand::Shutdown => {
                self.shutdown = true;
                self.reset_to_idle(now);
                true
            }
            OverlayCommand::Dismiss => {
                let was_visible = self.visible;
                self.reset_to_idle(now);
                was_visible
            }
            OverlayCommand::ApplyState {
                state,
                detail,
                error,
            } => self.apply_state(state, detail.as_deref(), error.as_ref(), now),
            OverlayCommand::AudioLevel(rms) => {
                self.apply_audio_level(f64::from(rms));
                false
            }
            OverlayCommand::InterimText(text) => {
                if self.state != AppState::Recording {
                    return false;
                }
                let prev = if self.interim_lines.source.is_empty() {
                    None
                } else {
                    Some(&self.interim_lines)
                };
                let wrapped = wrap_interim_text(
                    &text,
                    prev,
                    |line| line.chars().count() <= LIVE_TEXT_MAX_COLS,
                    LIVE_TEXT_LINES,
                );
                if wrapped.lines == self.interim_lines.lines {
                    self.interim_lines = wrapped;
                    return false;
                }
                self.interim_lines = wrapped;
                true
            }
        }
    }

    /// Convenience helper applying one command and immediately building the updated snapshot.
    pub fn apply_command(&mut self, cmd: OverlayCommand, now: Instant) -> OverlaySnapshot {
        let _ = self.apply_command_mut(cmd, now);
        self.snapshot(now)
    }

    fn reset_to_idle(&mut self, now: Instant) {
        self.state = AppState::Idle;
        self.content = OverlayContent::default();
        self.animation = AnimationLogic::new();
        self.latest_rms = 0.0;
        self.recording_started_at = None;
        self.anim_origin = now;
        self.interim_lines = InterimLines::default();
        self.bar_heights = [BAR_MIN_HEIGHT; BAR_COUNT];
        self.visible = false;
        self.timer_mode = OverlayTimerMode::Stopped;
    }

    fn apply_state(
        &mut self,
        state: AppState,
        detail: Option<&str>,
        error: Option<&DaemonStatusError>,
        now: Instant,
    ) -> bool {
        if state == AppState::Idle {
            // Preserve actionable/dismissible errors across the daemon's automatic Idle recovery
            // until the user explicitly dismisses them (`OverlayCommand::Dismiss`) or starts a new run.
            if self.visible && self.content.dismissible {
                return false;
            }
            let was_visible = self.visible;
            self.reset_to_idle(now);
            return was_visible;
        }

        let prev_state = self.state;
        let effective_detail =
            detail.or_else(|| error.map(|e| e.detail.as_deref().unwrap_or(e.code.as_str())));
        let content = describe_overlay_content(state, effective_detail, MAX_OVERLAY_STATUS_CHARS);

        self.state = state;
        self.content = content;
        self.visible = true;

        if is_recording_clock_state(state) {
            if !is_recording_clock_state(prev_state) {
                self.recording_started_at = Some(now);
                self.anim_origin = now;
                self.animation = AnimationLogic::new();
                self.latest_rms = 0.0;
            }
            if state == AppState::Listening {
                self.interim_lines = InterimLines::default();
                self.bar_heights = listening_bar_rest_heights();
                self.timer_mode = OverlayTimerMode::Stopped;
            } else if prev_state != AppState::Recording {
                // Keep bar heights when transitioning smoothly from Listening -> Recording;
                // only reset when entering Recording directly from a non-listening state.
                if prev_state != AppState::Listening {
                    self.bar_heights = [BAR_MIN_HEIGHT; BAR_COUNT];
                }
                self.timer_mode = self.compute_timer_mode();
            }
        } else {
            self.recording_started_at = None;
            self.interim_lines = InterimLines::default();
            self.latest_rms = 0.0;
            // Non-recording states use Core Animation / Direct2D shimmer (`Line`/`Phase1`/`Phase2`)
            // or static outcome badges (`Done`/`NoSpeech`/`Error`) -> 0 FPS wave timer.
            self.timer_mode = OverlayTimerMode::Stopped;
            self.bar_heights = [BAR_MIN_HEIGHT; BAR_COUNT];
        }
        true
    }

    fn apply_audio_level(&mut self, rms: f64) {
        let clamped = if rms.is_finite() {
            rms.clamp(0.0, 1.0)
        } else {
            0.0
        };
        self.latest_rms = clamped;

        if matches!(self.state, AppState::Recording | AppState::Listening) {
            self.timer_mode = self.compute_timer_mode();
        }
    }

    /// Advance the wave or listening-meter animation by one timer tick.
    ///
    /// Automatically transitions `timer_mode` back to [`OverlayTimerMode::Stopped`] once all bars
    /// have settled so silence consumes 0 FPS / 0 CPU wakeups.
    pub fn tick_animation(&mut self, now: Instant) -> OverlayTick {
        let mut changed_bars = [false; BAR_COUNT];
        let mut any_bar_changed = false;

        match self.content.bars {
            OverlayBarMode::Level if self.state == AppState::Recording => {
                self.animation.update_level(self.latest_rms);
                self.animation.update_agc();
                let t = now
                    .saturating_duration_since(self.anim_origin)
                    .as_secs_f64();
                let next_heights = self.animation.calculate_frame_heights(
                    t,
                    AppState::Recording,
                    BAR_MIN_HEIGHT,
                    BAR_MAX_HEIGHT,
                );
                any_bar_changed = self.commit_bar_heights(&next_heights, &mut changed_bars);
                self.timer_mode = self.compute_timer_mode();
            }
            OverlayBarMode::Low if self.state == AppState::Listening => {
                self.animation.update_level(self.latest_rms);
                let next_heights = self.animation.listening_frame_heights();
                any_bar_changed = self.commit_bar_heights(&next_heights, &mut changed_bars);
                self.timer_mode = self.compute_timer_mode();
            }
            _ => {
                self.timer_mode = OverlayTimerMode::Stopped;
            }
        }

        OverlayTick {
            changed_bars,
            any_bar_changed,
            bar_heights: self.bar_heights,
            timer_mode: self.timer_mode,
        }
    }

    /// Format the current `m:ss` recording clock if in `Listening` or `Recording`.
    pub fn recording_clock_text(&self, now: Instant) -> Option<String> {
        if !is_recording_clock_state(self.state) {
            return None;
        }
        let elapsed = self
            .recording_started_at
            .map(|start| now.saturating_duration_since(start).as_secs_f64())
            .unwrap_or(0.0);
        Some(format_recording_duration(elapsed))
    }

    /// Produce a full [`OverlaySnapshot`] reflecting current state, text, geometry, and timer mode.
    pub fn snapshot(&self, now: Instant) -> OverlaySnapshot {
        let live_text_active =
            self.state == AppState::Recording && !self.interim_lines.lines.is_empty();
        let (display_text, live_line_count) = if live_text_active {
            let mut joined = String::new();
            for (idx, (confirmed, fresh)) in self.interim_lines.lines.iter().enumerate() {
                if idx > 0 {
                    joined.push('\n');
                }
                joined.push_str(confirmed);
                joined.push_str(fresh);
            }
            (joined, self.interim_lines.lines.len())
        } else {
            (self.content.text.clone(), 1)
        };

        let clock_active = self.visible && is_recording_clock_state(self.state);
        let display_meta = if clock_active {
            self.recording_clock_text(now).unwrap_or_default()
        } else {
            self.content.meta.clone()
        };

        let base_size = if live_text_active {
            (LIVE_TEXT_WIDTH, LIVE_TEXT_HEIGHT)
        } else {
            (OVERLAY_WIDTH, OVERLAY_HEIGHT)
        };
        let allow_grow = self.content.dismissible && !live_text_active;
        let interactive =
            self.visible && (self.content.dismissible || self.content.action.is_some());

        OverlaySnapshot {
            visible: self.visible,
            state: self.state,
            content: self.content.clone(),
            display_text,
            live_text_active,
            live_line_count,
            display_meta,
            action: self.content.action,
            interactive,
            base_size,
            allow_grow,
            bar_heights: self.bar_heights,
            shimmer: shimmer_track(self.content.bars),
            timer_mode: self.timer_mode,
            clock_active,
        }
    }

    fn commit_bar_heights(
        &mut self,
        next: &[f64; BAR_COUNT],
        changed: &mut [bool; BAR_COUNT],
    ) -> bool {
        let mut any = false;
        for i in 0..BAR_COUNT {
            if (self.bar_heights[i] - next[i]).abs() >= WAVE_HEIGHT_UPDATE_EPSILON {
                self.bar_heights[i] = next[i];
                changed[i] = true;
                any = true;
            }
        }
        any
    }

    fn compute_timer_mode(&self) -> OverlayTimerMode {
        if !self.visible {
            return OverlayTimerMode::Stopped;
        }
        match self.content.bars {
            OverlayBarMode::Level if self.state == AppState::Recording => {
                let quiet = self.latest_rms <= LEVEL_SETTLED_EPSILON
                    && self.animation.smoothed_level() <= LEVEL_SETTLED_EPSILON
                    && self.animation.level_smoothed() <= LEVEL_SETTLED_EPSILON
                    && self
                        .bar_heights
                        .iter()
                        .all(|&h| (h - BAR_MIN_HEIGHT).abs() < 2.0 * WAVE_HEIGHT_UPDATE_EPSILON);
                if quiet {
                    return OverlayTimerMode::Stopped;
                }
                let peak = self.latest_rms.max(self.animation.normalized_level());
                match self.timer_mode {
                    OverlayTimerMode::Active60Hz if peak >= LEVEL_DECAY_THRESHOLD => {
                        OverlayTimerMode::Active60Hz
                    }
                    _ if peak >= LEVEL_ACTIVE_THRESHOLD => OverlayTimerMode::Active60Hz,
                    _ => OverlayTimerMode::Decay24Hz,
                }
            }
            OverlayBarMode::Low if self.state == AppState::Listening => {
                let target_heights = listening_bar_heights(listening_level(self.latest_rms));
                let settled = self.animation.listening_is_settled(self.latest_rms)
                    && self
                        .bar_heights
                        .iter()
                        .zip(target_heights.iter())
                        .all(|(cur, goal)| (cur - goal).abs() < 2.0 * WAVE_HEIGHT_UPDATE_EPSILON);
                if settled {
                    OverlayTimerMode::Stopped
                } else {
                    OverlayTimerMode::Active60Hz
                }
            }
            _ => OverlayTimerMode::Stopped,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pulsescribe_core::DaemonErrorCode;

    #[test]
    fn idle_processing_and_done_states_never_run_animation_timer() {
        let t0 = Instant::now();
        let mut model = OverlayModel::new(t0);
        assert!(!model.visible());
        assert_eq!(model.timer_mode(), OverlayTimerMode::Stopped);

        let snap = model.apply_command(
            OverlayCommand::ApplyState {
                state: AppState::Transcribing,
                detail: Some("Transcribing, then refining".to_owned()),
                error: None,
            },
            t0,
        );
        assert!(snap.visible);
        assert_eq!(snap.content.bars, OverlayBarMode::Phase1);
        assert!(snap.shimmer.is_some());
        assert_eq!(snap.display_meta, "1 of 2");
        assert_eq!(snap.timer_mode, OverlayTimerMode::Stopped);
        assert!(!snap.clock_active);
        assert!(!snap.interactive);

        let done = model.apply_command(
            OverlayCommand::ApplyState {
                state: AppState::Done,
                detail: Some("Transcript pasted into Notes · 0.8 s".to_owned()),
                error: None,
            },
            t0 + Duration::from_secs(1),
        );
        assert!(done.visible);
        assert_eq!(done.content.badge, "Pasted");
        assert_eq!(done.display_text, "into Notes");
        assert_eq!(done.display_meta, "0.8 s");
        assert_eq!(done.timer_mode, OverlayTimerMode::Stopped);

        let idle = model.apply_command(
            OverlayCommand::ApplyState {
                state: AppState::Idle,
                detail: None,
                error: None,
            },
            t0 + Duration::from_secs(2),
        );
        assert!(!idle.visible);
        assert_eq!(idle.timer_mode, OverlayTimerMode::Stopped);
    }

    #[test]
    fn recording_animates_on_speech_avoids_layout_churn_and_settles_to_zero_fps() {
        let t0 = Instant::now();
        let mut model = OverlayModel::new(t0);

        let listen = model.apply_command(
            OverlayCommand::ApplyState {
                state: AppState::Listening,
                detail: None,
                error: None,
            },
            t0,
        );
        assert!(listen.visible);
        assert!(listen.clock_active);
        assert_eq!(listen.display_meta, "0:00");
        assert_eq!(listen.timer_mode, OverlayTimerMode::Stopped);

        let rec = model.apply_command(
            OverlayCommand::ApplyState {
                state: AppState::Recording,
                detail: None,
                error: None,
            },
            t0 + Duration::from_secs(2),
        );
        assert_eq!(rec.display_meta, "0:02");
        // Smooth handoff from Listening preserves resting heights rather than jumping to 3 px.
        assert_eq!(rec.bar_heights, listening_bar_rest_heights());

        // AudioLevel must NOT trigger full visual layout re-renders, only wake timer pacing.
        assert!(!model.apply_command_mut(
            OverlayCommand::AudioLevel(0.25),
            t0 + Duration::from_millis(2050),
        ));
        assert_eq!(model.timer_mode(), OverlayTimerMode::Active60Hz);

        let mut moved = false;
        for step in 1..=10 {
            let tick = model.tick_animation(t0 + Duration::from_millis(2050 + step * 16));
            if tick.any_bar_changed {
                moved = true;
            }
        }
        assert!(moved, "bars should animate during active speech");

        model.apply_command_mut(OverlayCommand::AudioLevel(0.0), t0 + Duration::from_secs(3));
        let mut stopped = false;
        for step in 1..=160 {
            let tick = model.tick_animation(t0 + Duration::from_millis(3000 + step * 40));
            if tick.timer_mode == OverlayTimerMode::Stopped {
                stopped = true;
                break;
            }
        }
        assert!(stopped, "bars must settle to 0 FPS on silence");
    }

    #[test]
    fn long_interim_keeps_newest_two_lines_and_dismissible_errors_persist_across_idle() {
        let t0 = Instant::now();
        let mut model = OverlayModel::new(t0);
        model.apply_command_mut(
            OverlayCommand::ApplyState {
                state: AppState::Recording,
                detail: None,
                error: None,
            },
            t0,
        );

        let long_transcript = "Dies ist ein sehr langer diktierter Satz der ueber mehr als zwei Zeilen hinausgeht und am Ende unbedingt das allerletzte Wort sichtbar behalten muss";
        let live = model.apply_command(
            OverlayCommand::InterimText(long_transcript.to_owned()),
            t0 + Duration::from_millis(500),
        );
        assert!(live.live_text_active);
        assert_eq!(live.live_line_count, LIVE_TEXT_LINES);
        assert_eq!(live.base_size, (LIVE_TEXT_WIDTH, LIVE_TEXT_HEIGHT));
        assert!(
            live.display_text.ends_with("behalten muss"),
            "newest trailing words must remain visible in the last 2 lines: {:?}",
            live.display_text
        );
        assert!(live
            .display_text
            .lines()
            .all(|l| l.chars().count() <= LIVE_TEXT_MAX_COLS));

        // Dismissible error persists across daemon Idle timeout until explicit Dismiss or new run.
        let err = model.apply_command(
            OverlayCommand::ApplyState {
                state: AppState::Error,
                detail: None,
                error: Some(DaemonStatusError::new(
                    DaemonErrorCode::MissingApiKey,
                    Some("DEEPGRAM_API_KEY missing".to_owned()),
                )),
            },
            t0 + Duration::from_secs(1),
        );
        assert!(err.interactive);
        assert!(err.content.dismissible);
        assert_eq!(err.action, Some(OverlayAction::Settings));

        assert!(!model.apply_command_mut(
            OverlayCommand::ApplyState {
                state: AppState::Idle,
                detail: None,
                error: None,
            },
            t0 + Duration::from_secs(5),
        ));
        assert!(
            model.visible(),
            "dismissible error must stay visible across Idle"
        );

        let dismissed = model.apply_command(OverlayCommand::Dismiss, t0 + Duration::from_secs(6));
        assert!(!dismissed.visible);
        assert!(!dismissed.interactive);

        // Once shutdown, subsequent commands are ignored cleanly.
        model.apply_command_mut(OverlayCommand::Shutdown, t0 + Duration::from_secs(7));
        assert!(!model.apply_command_mut(
            OverlayCommand::ApplyState {
                state: AppState::Listening,
                detail: None,
                error: None,
            },
            t0 + Duration::from_secs(8),
        ));
        assert!(!model.visible());
    }
}
