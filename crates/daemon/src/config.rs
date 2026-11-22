//! Daemon configuration snapshot, `.env` parsing, and hot-reload tracking.
//!
//! Mirrors the settings resolution in `pulsescribe_daemon.py` and `pulsescribe_windows.py`
//! while keeping all configuration pure and reloadable without global mutable state.

use std::collections::BTreeMap;
use std::fs;
use std::io;
use std::path::PathBuf;
use std::time::Duration;

use pulsescribe_core::env::parse_bool;
use pulsescribe_core::overlay_feedback::ERROR_FEEDBACK_SECONDS;
use pulsescribe_core::paths::{build_file_signature, AppPaths, FileSignature};
use pulsescribe_core::preferences::PreferencesStore;
use pulsescribe_core::transcript_delivery::transcription_time_enabled_from_value;
use pulsescribe_core::{DaemonErrorCode, DaemonStatusError};
use pulsescribe_platform::HotkeyBinding;

/// Signal file touched by external settings/onboarding UIs to request a config reload.
pub const RELOAD_SIGNAL_FILENAME: &str = ".reload";

/// Default hold duration before `Done` resets back to `Idle` (`_DONE_DISPLAY_HOLD_SEC`).
pub const DEFAULT_DONE_DISPLAY_HOLD: Duration = Duration::from_millis(600);

/// Default hold duration before `NoSpeech` resets back to `Idle`.
pub const DEFAULT_NO_SPEECH_RESET_DELAY: Duration = Duration::from_millis(1_200);

/// Default watchdog timeout for stuck `Transcribing` / `Refining` states.
pub const DEFAULT_TRANSCRIBING_TIMEOUT: Duration = Duration::from_secs(45);

const MIN_TRANSCRIBING_TIMEOUT_SECS: f64 = 5.0;
const MAX_TRANSCRIBING_TIMEOUT_SECS: f64 = 300.0;

/// Supported transcription execution modes for the dictation daemon.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Hash)]
pub enum DaemonMode {
    #[default]
    Deepgram,
    OpenAi,
    Groq,
    OpenAiCompatible,
    Local,
}

impl DaemonMode {
    /// Parse a `PULSESCRIBE_MODE` value (`deepgram`, `openai`/`api`, `groq`,
    /// `openai_compatible`, `local`). Returns `None` for unrecognized names.
    pub fn parse(raw: &str) -> Option<Self> {
        match raw.trim().to_ascii_lowercase().as_str() {
            "deepgram" => Some(Self::Deepgram),
            "openai" | "api" => Some(Self::OpenAi),
            "groq" => Some(Self::Groq),
            "openai_compatible" => Some(Self::OpenAiCompatible),
            "local" => Some(Self::Local),
            _ => None,
        }
    }

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Deepgram => "deepgram",
            Self::OpenAi => "openai",
            Self::Groq => "groq",
            Self::OpenAiCompatible => "openai_compatible",
            Self::Local => "local",
        }
    }

    /// Returns `true` when this mode supports live WebSocket streaming (currently Deepgram).
    pub const fn supports_live_streaming(self) -> bool {
        matches!(self, Self::Deepgram)
    }
}

/// Configurable lifecycle durations used by [`crate::DaemonStateMachine`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DaemonTimings {
    pub done_display_hold: Duration,
    pub no_speech_reset_delay: Duration,
    pub error_reset_delay: Duration,
    pub transcribing_timeout: Duration,
}

impl Default for DaemonTimings {
    fn default() -> Self {
        Self {
            done_display_hold: DEFAULT_DONE_DISPLAY_HOLD,
            no_speech_reset_delay: DEFAULT_NO_SPEECH_RESET_DELAY,
            error_reset_delay: Duration::from_secs_f64(ERROR_FEEDBACK_SECONDS),
            transcribing_timeout: DEFAULT_TRANSCRIBING_TIMEOUT,
        }
    }
}

/// Immutable configuration snapshot consumed by the daemon state machine and runtime.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DaemonConfig {
    pub mode: DaemonMode,
    /// Preserves an unrecognized `PULSESCRIBE_MODE` value so the daemon can still start and
    /// surface `DaemonErrorCode::InvalidProvider` cleanly instead of silently rewriting it.
    pub invalid_mode: Option<String>,
    pub model: Option<String>,
    pub language: Option<String>,
    pub refine: bool,
    pub refine_provider: Option<String>,
    pub refine_model: Option<String>,
    pub context: Option<String>,
    pub streaming: bool,
    pub deepgram_warm_websocket: bool,
    pub overlay_enabled: bool,
    pub sound_enabled: bool,
    pub auto_paste: bool,
    pub show_rtf: bool,
    pub input_device: Option<String>,
    pub hotkeys: HotkeyBinding,
    pub timings: DaemonTimings,
}

impl Default for DaemonConfig {
    fn default() -> Self {
        Self {
            mode: DaemonMode::Deepgram,
            invalid_mode: None,
            model: None,
            language: None,
            refine: false,
            refine_provider: None,
            refine_model: None,
            context: None,
            streaming: true,
            deepgram_warm_websocket: true,
            overlay_enabled: true,
            sound_enabled: true,
            auto_paste: true,
            show_rtf: false,
            input_device: None,
            hotkeys: HotkeyBinding::default(),
            timings: DaemonTimings::default(),
        }
    }
}

impl DaemonConfig {
    /// Parse a daemon configuration snapshot from an environment key/value map.
    pub fn from_env_map(env: &BTreeMap<String, String>) -> Self {
        let (mode, invalid_mode) = match get_trimmed(env, "PULSESCRIBE_MODE") {
            None => (DaemonMode::default(), None),
            Some(raw) => match DaemonMode::parse(&raw) {
                Some(parsed) => (parsed, None),
                None => (DaemonMode::default(), Some(raw)),
            },
        };

        let model = match mode {
            DaemonMode::Local => get_trimmed(env, "PULSESCRIBE_LOCAL_MODEL")
                .or_else(|| get_trimmed(env, "PULSESCRIBE_MODEL")),
            _ => get_trimmed(env, "PULSESCRIBE_MODEL"),
        };

        let language = get_trimmed(env, "PULSESCRIBE_LANGUAGE")
            .filter(|lang| !lang.eq_ignore_ascii_case("auto"));

        let refine = get_bool(env, "PULSESCRIBE_REFINE", false);
        let refine_provider = get_trimmed(env, "PULSESCRIBE_REFINE_PROVIDER");
        let refine_model = get_trimmed(env, "PULSESCRIBE_REFINE_MODEL");
        let context = get_trimmed(env, "PULSESCRIBE_CONTEXT");

        let streaming = get_bool(env, "PULSESCRIBE_STREAMING", true);
        let deepgram_warm_websocket = get_bool(env, "PULSESCRIBE_DEEPGRAM_WARM_WEBSOCKET", true);
        let overlay_enabled = get_bool(env, "PULSESCRIBE_OVERLAY", true);
        let sound_enabled = get_bool(env, "PULSESCRIBE_SOUND", true);
        let auto_paste = get_bool(env, "PULSESCRIBE_AUTO_PASTE", true);
        let show_rtf = transcription_time_enabled_from_value(
            env.get("PULSESCRIBE_SHOW_RTF").map(String::as_str),
        );
        let input_device = get_trimmed(env, "PULSESCRIBE_INPUT_DEVICE");

        let hotkeys = resolve_hotkeys(env);

        let mut timings = DaemonTimings::default();
        if let Some(secs) = get_trimmed(env, "PULSESCRIBE_TRANSCRIBING_TIMEOUT")
            .and_then(|s| s.parse::<f64>().ok())
            .filter(|v| v.is_finite())
        {
            let clamped = secs.clamp(MIN_TRANSCRIBING_TIMEOUT_SECS, MAX_TRANSCRIBING_TIMEOUT_SECS);
            timings.transcribing_timeout = Duration::from_secs_f64(clamped);
        }

        Self {
            mode,
            invalid_mode,
            model,
            language,
            refine,
            refine_provider,
            refine_model,
            context,
            streaming,
            deepgram_warm_websocket,
            overlay_enabled,
            sound_enabled,
            auto_paste,
            show_rtf,
            input_device,
            hotkeys,
            timings,
        }
    }

    /// Validate that the active provider mode is recognized before starting a dictation run.
    pub fn validate(&self) -> Result<(), DaemonStatusError> {
        if let Some(ref raw) = self.invalid_mode {
            return Err(DaemonStatusError::new(
                DaemonErrorCode::InvalidProvider,
                Some(format!("Unsupported provider: {raw}")),
            ));
        }
        Ok(())
    }

    /// Load configuration from `~/.pulsescribe/.env` without process-environment overrides
    /// (used for hot-reloads after a settings UI writes `.env`).
    pub fn from_store_env_file(store: &PreferencesStore) -> Self {
        Self::from_env_map(&store.read_env_file())
    }

    /// Load configuration merging `~/.pulsescribe/.env` with current process environment overrides
    /// (`Process Env > ~/.pulsescribe/.env`, matching `utils/env.py`).
    pub fn from_store_with_process_env(store: &PreferencesStore) -> Self {
        let mut merged = store.read_env_file();
        for (key, value) in std::env::vars() {
            if key.starts_with("PULSESCRIBE_") {
                merged.insert(key, value);
            }
        }
        Self::from_env_map(&merged)
    }

    /// Returns `true` when live WebSocket streaming should be used for the configured mode.
    pub const fn uses_live_streaming(&self) -> bool {
        self.streaming && self.mode.supports_live_streaming()
    }
}

/// Lightweight file-signature & `.reload`-signal detector for settings hot-reload.
///
/// Requires no OS filesystem-watcher dependency and works identically on macOS, Windows, and Linux.
#[derive(Debug, Clone)]
pub struct ReloadTracker {
    env_file: PathBuf,
    reload_signal_file: PathBuf,
    last_env_signature: Option<FileSignature>,
}

impl ReloadTracker {
    /// Construct a tracker for the given `AppPaths`, capturing the current `.env` signature as
    /// baseline so the initial state is not reported as a spurious change.
    pub fn new(paths: &AppPaths) -> Self {
        let env_file = paths.env_file();
        let reload_signal_file = paths.config_dir().join(RELOAD_SIGNAL_FILENAME);
        let last_env_signature = build_file_signature(&env_file).ok();
        Self {
            env_file,
            reload_signal_file,
            last_env_signature,
        }
    }

    /// Check whether `.reload` exists or `.env` changed since the previous poll.
    ///
    /// Consumes `.reload` best-effort when present.
    pub fn poll_changed(&mut self) -> bool {
        let mut changed = false;

        match fs::remove_file(&self.reload_signal_file) {
            Ok(()) => changed = true,
            Err(err) if err.kind() == io::ErrorKind::NotFound => {}
            Err(_) => changed = true,
        }

        let current_sig = build_file_signature(&self.env_file).ok();
        if current_sig != self.last_env_signature {
            self.last_env_signature = current_sig;
            changed = true;
        }

        changed
    }

    pub fn reload_signal_file(&self) -> &PathBuf {
        &self.reload_signal_file
    }
}

fn get_trimmed(env: &BTreeMap<String, String>, key: &str) -> Option<String> {
    env.get(key).and_then(|raw| {
        let trimmed = raw.trim();
        (!trimmed.is_empty()).then(|| trimmed.to_owned())
    })
}

fn get_bool(env: &BTreeMap<String, String>, key: &str, default: bool) -> bool {
    env.get(key)
        .and_then(|raw| parse_bool(Some(raw.as_str())))
        .unwrap_or(default)
}

fn resolve_hotkeys(env: &BTreeMap<String, String>) -> HotkeyBinding {
    let mut toggle = get_trimmed(env, "PULSESCRIBE_TOGGLE_HOTKEY");
    let mut hold = get_trimmed(env, "PULSESCRIBE_HOLD_HOTKEY");

    // Preserve legacy `PULSESCRIBE_HOTKEY` + `PULSESCRIBE_HOTKEY_MODE` fallback when neither
    // modern slot is explicitly configured.
    if toggle.is_none() && hold.is_none() {
        if let Some(legacy_key) = get_trimmed(env, "PULSESCRIBE_HOTKEY") {
            let mode = get_trimmed(env, "PULSESCRIBE_HOTKEY_MODE")
                .map(|m| m.to_ascii_lowercase())
                .unwrap_or_default();
            if mode == "hold" {
                hold = Some(legacy_key);
            } else {
                toggle = Some(legacy_key);
            }
        }
    }

    HotkeyBinding::new(toggle, hold)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn unique_temp_dir(tag: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "pulsescribe-daemon-cfg-{tag}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn from_env_map_resolves_modes_models_hotkeys_and_bounds() {
        let mut env = BTreeMap::new();
        env.insert("PULSESCRIBE_MODE".into(), "api".into());
        env.insert("PULSESCRIBE_MODEL".into(), "gpt-4o-transcribe".into());
        env.insert("PULSESCRIBE_LANGUAGE".into(), "auto".into());
        env.insert("PULSESCRIBE_REFINE".into(), "true".into());
        env.insert("PULSESCRIBE_SHOW_RTF".into(), "1".into());
        env.insert("PULSESCRIBE_TOGGLE_HOTKEY".into(), " f19 ".into());
        env.insert("PULSESCRIBE_HOLD_HOTKEY".into(), " fn ".into());
        env.insert("PULSESCRIBE_TRANSCRIBING_TIMEOUT".into(), "999".into());

        let cfg = DaemonConfig::from_env_map(&env);
        assert_eq!(cfg.mode, DaemonMode::OpenAi);
        assert_eq!(cfg.model.as_deref(), Some("gpt-4o-transcribe"));
        assert_eq!(cfg.language, None);
        assert!(cfg.refine);
        assert!(cfg.show_rtf);
        assert!(!cfg.uses_live_streaming());
        assert_eq!(cfg.hotkeys.toggle.as_deref(), Some("f19"));
        assert_eq!(cfg.hotkeys.hold.as_deref(), Some("fn"));
        assert_eq!(cfg.timings.transcribing_timeout, Duration::from_secs(300));
    }

    #[test]
    fn legacy_hotkey_fallback_and_local_model_precedence() {
        let mut env = BTreeMap::new();
        env.insert("PULSESCRIBE_MODE".into(), "local".into());
        env.insert("PULSESCRIBE_LOCAL_MODEL".into(), "large-v3-turbo".into());
        env.insert("PULSESCRIBE_MODEL".into(), "ignored-cloud-model".into());
        env.insert("PULSESCRIBE_HOTKEY".into(), "ctrl+win".into());
        env.insert("PULSESCRIBE_HOTKEY_MODE".into(), "hold".into());

        let cfg = DaemonConfig::from_env_map(&env);
        assert!(cfg.validate().is_ok());
        assert_eq!(cfg.mode, DaemonMode::Local);
        assert_eq!(cfg.model.as_deref(), Some("large-v3-turbo"));
        assert_eq!(cfg.hotkeys.toggle, None);
        assert_eq!(cfg.hotkeys.hold.as_deref(), Some("ctrl+win"));

        let mut bad = BTreeMap::new();
        bad.insert("PULSESCRIBE_MODE".into(), "bogus_provider".into());
        let bad_cfg = DaemonConfig::from_env_map(&bad);
        let err = bad_cfg.validate().unwrap_err();
        assert_eq!(err.code, DaemonErrorCode::InvalidProvider);
    }

    #[test]
    fn reload_tracker_detects_signal_file_and_env_edits() {
        let dir = unique_temp_dir("reload");
        let paths = AppPaths::new(&dir);
        fs::write(paths.env_file(), "PULSESCRIBE_MODE=deepgram\n").unwrap();

        let mut tracker = ReloadTracker::new(&paths);
        assert!(!tracker.poll_changed());

        fs::write(tracker.reload_signal_file(), "1").unwrap();
        assert!(tracker.poll_changed());
        assert!(!tracker.reload_signal_file().exists());
        assert!(!tracker.poll_changed());

        fs::write(
            paths.env_file(),
            "PULSESCRIBE_MODE=openai\nPULSESCRIBE_REFINE=true\n",
        )
        .unwrap();
        assert!(tracker.poll_changed());
        assert!(!tracker.poll_changed());

        let _ = fs::remove_dir_all(&dir);
    }
}
