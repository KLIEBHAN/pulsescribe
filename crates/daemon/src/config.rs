//! Daemon configuration snapshot, `.env` parsing, and hot-reload tracking.
//!
//! Mirrors the settings resolution in `pulsescribe_daemon.py` and `pulsescribe_windows.py`
//! while keeping all configuration pure and reloadable without global mutable state.

use std::collections::BTreeMap;
use std::fmt;
use std::fs;
use std::io;
use std::path::PathBuf;
use std::time::Duration;

use pulsescribe_core::env::{current_process_env, parse_bool, resolve_environment};
use pulsescribe_core::overlay_feedback::ERROR_FEEDBACK_SECONDS;
use pulsescribe_core::paths::{build_file_signature, AppPaths, FileSignature};
use pulsescribe_core::preferences::PreferencesStore;
use pulsescribe_core::transcript_delivery::transcription_time_enabled_from_value;
use pulsescribe_core::{DaemonErrorCode, DaemonStatusError};
use pulsescribe_platform::{HotkeyBinding, PlatformKind};

/// Default macOS hold-to-record hotkey when no hotkey env vars are configured.
pub const DEFAULT_MACOS_HOLD_HOTKEY: &str = "fn";

/// Default Windows toggle hotkey when no hotkey env vars are configured.
pub const DEFAULT_WINDOWS_TOGGLE_HOTKEY: &str = "ctrl+alt+r";

/// Default Windows hold-to-record hotkey when no hotkey env vars are configured.
pub const DEFAULT_WINDOWS_HOLD_HOTKEY: &str = "ctrl+win";

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

/// Environment keys retained inside [`DaemonConfig::provider_env`] for hot-reloadable provider
/// credentials, endpoint overrides, refinement routing, and Deepgram stream timeouts without
/// mutating process-global environment variables.
const PROVIDER_ENV_KEYS: &[&str] = &[
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
    "DEEPGRAM_API_KEY",
    "OPENAI_COMPATIBLE_BASE_URL",
    "OPENAI_COMPATIBLE_API_KEY",
    "OPENAI_COMPATIBLE_MODEL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_PROVIDER_ORDER",
    "OPENROUTER_ALLOW_FALLBACKS",
    "GEMINI_API_KEY",
    "PULSESCRIBE_APP_CONTEXTS",
    "PULSESCRIBE_VOICE_COMMANDS",
    "PULSESCRIBE_DEEPGRAM_CLOSE_TIMEOUT",
    "PULSESCRIBE_DEEPGRAM_TAIL_PADDING_SECONDS",
    "PULSESCRIBE_DEEPGRAM_EMPTY_FINALIZE_GRACE_SECONDS",
    "PULSESCRIBE_DEEPGRAM_KEEPALIVE_INTERVAL_SECONDS",
    "PULSESCRIBE_DEEPGRAM_FINALIZE_SEND_TIMEOUT",
    "PULSESCRIBE_DEEPGRAM_CLOSE_STREAM_SEND_TIMEOUT",
    "PULSESCRIBE_DEEPGRAM_KEEPALIVE_SEND_TIMEOUT",
];

/// Secret-safe environment subset carried inside [`DaemonConfig`].
///
/// Always prints `[REDACTED]` in [`fmt::Debug`] so logging or panicking with a `DaemonConfig`
/// never exposes API keys.
#[derive(Clone, Default, PartialEq, Eq)]
pub struct RedactedEnv {
    values: BTreeMap<String, String>,
}

impl RedactedEnv {
    pub fn from_env_map(env: &BTreeMap<String, String>) -> Self {
        let mut values = BTreeMap::new();
        for &key in PROVIDER_ENV_KEYS {
            if let Some(val) = get_trimmed(env, key) {
                values.insert(key.to_owned(), val);
            }
        }
        Self { values }
    }

    pub fn get(&self, key: &str) -> Option<&str> {
        self.values.get(key).map(String::as_str)
    }

    pub fn insert(&mut self, key: impl Into<String>, value: impl Into<String>) {
        let trimmed = value.into().trim().to_owned();
        if trimmed.is_empty() {
            self.values.remove(&key.into());
        } else {
            self.values.insert(key.into(), trimmed);
        }
    }

    pub fn is_empty(&self) -> bool {
        self.values.is_empty()
    }
}

impl fmt::Debug for RedactedEnv {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "RedactedEnv([REDACTED; {} keys])", self.values.len())
    }
}

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

/// Configurable lifecycle durations used by the daemon state machine.
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
    pub provider_env: RedactedEnv,
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
            provider_env: RedactedEnv::default(),
        }
    }
}

impl DaemonConfig {
    /// Parse a daemon configuration snapshot from an environment key/value map using the
    /// compile-time host platform's default hotkeys when no hotkey slots are configured.
    pub fn from_env_map(env: &BTreeMap<String, String>) -> Self {
        Self::from_env_map_for_platform(env, PlatformKind::current())
    }

    /// Parse a daemon configuration snapshot using `platform`'s default hotkeys when unset.
    pub fn from_env_map_for_platform(
        env: &BTreeMap<String, String>,
        platform: PlatformKind,
    ) -> Self {
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
            DaemonMode::OpenAiCompatible => get_trimmed(env, "OPENAI_COMPATIBLE_MODEL")
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
            get_trimmed(env, "PULSESCRIBE_SHOW_RTF").as_deref(),
        );
        let input_device = get_trimmed(env, "PULSESCRIBE_INPUT_DEVICE");

        let hotkeys = resolve_hotkeys_for_platform(env, platform);

        let mut timings = DaemonTimings::default();
        if let Some(secs) = get_trimmed(env, "PULSESCRIBE_TRANSCRIBING_TIMEOUT")
            .and_then(|s| s.parse::<f64>().ok())
            .filter(|v| v.is_finite())
        {
            let clamped = secs.clamp(MIN_TRANSCRIBING_TIMEOUT_SECS, MAX_TRANSCRIBING_TIMEOUT_SECS);
            timings.transcribing_timeout = Duration::from_secs_f64(clamped);
        }

        let provider_env = RedactedEnv::from_env_map(env);

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
            provider_env,
        }
    }

    /// Return a cloned configuration with an added/updated provider environment value.
    pub fn with_provider_env(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.provider_env.insert(key, value);
        self
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

    /// Resolve configuration from `~/.pulsescribe/.env` and an explicit process-environment map
    /// using `pulsescribe_core::env::resolve_environment` (including `${VAR}` dotenv expansion).
    ///
    /// - `override_existing == false` (startup): `process_env > user .env`
    /// - `override_existing == true` (hot-reload): `user .env > process_env` while preserving
    ///   process-only `PULSESCRIBE_*` settings not defined in `.env`.
    pub fn from_store_with_env(
        store: &PreferencesStore,
        process_env: &BTreeMap<String, String>,
        override_existing: bool,
    ) -> Self {
        let merged = resolve_environment(
            process_env,
            store.paths().config_dir(),
            None,
            override_existing,
        );
        Self::from_env_map(&merged)
    }

    /// Load startup configuration (`process_env > ~/.pulsescribe/.env`), skipping non-UTF-8 OS
    /// environment entries safely instead of panicking.
    pub fn from_store_startup(store: &PreferencesStore) -> Self {
        Self::from_store_with_env(store, &current_process_env(), false)
    }

    /// Load hot-reload configuration (`~/.pulsescribe/.env > process_env`), preserving process-only
    /// overrides that are absent from `.env`.
    pub fn from_store_reload(store: &PreferencesStore) -> Self {
        Self::from_store_with_env(store, &current_process_env(), true)
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
            Err(err) => {
                tracing::debug!(
                    path = %self.reload_signal_file.display(),
                    error = %err,
                    "deferring reload signal consumption after remove error"
                );
            }
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

fn resolve_hotkeys_for_platform(
    env: &BTreeMap<String, String>,
    platform: PlatformKind,
) -> HotkeyBinding {
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

    // Apply platform defaults only when no hotkey slot is configured at all, matching
    // `pulsescribe_daemon.py::_should_default_to_fn_hold` and `pulsescribe_windows.py`.
    if toggle.is_none() && hold.is_none() {
        match platform {
            PlatformKind::MacOs => {
                hold = Some(DEFAULT_MACOS_HOLD_HOTKEY.to_owned());
            }
            PlatformKind::Windows | PlatformKind::Linux | PlatformKind::Other => {
                toggle = Some(DEFAULT_WINDOWS_TOGGLE_HOTKEY.to_owned());
                hold = Some(DEFAULT_WINDOWS_HOLD_HOTKEY.to_owned());
            }
        }
    }

    HotkeyBinding::new(toggle, hold)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    static TEST_DIR_COUNTER: AtomicU64 = AtomicU64::new(0);

    fn unique_temp_dir(tag: &str) -> PathBuf {
        let seq = TEST_DIR_COUNTER.fetch_add(1, Ordering::Relaxed);
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "pulsescribe-daemon-cfg-{tag}-{}-{nanos}-{seq}",
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
        env.insert("PULSESCRIBE_SHOW_RTF".into(), " 1 ".into());
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

    #[test]
    fn startup_vs_reload_precedence_preserves_process_only_keys() {
        let dir = unique_temp_dir("precedence");
        let store = PreferencesStore::new(&dir);
        fs::write(
            store.paths().env_file(),
            "PULSESCRIBE_MODE=openai\nPULSESCRIBE_REFINE=true\n",
        )
        .unwrap();

        let mut proc_env = BTreeMap::new();
        proc_env.insert("PULSESCRIBE_MODE".into(), "groq".into());
        proc_env.insert("PULSESCRIBE_OVERLAY".into(), "false".into());

        // Startup: process_env wins over .env.
        let startup = DaemonConfig::from_store_with_env(&store, &proc_env, false);
        assert_eq!(startup.mode, DaemonMode::Groq);
        assert!(startup.refine);
        assert!(!startup.overlay_enabled);

        // Reload: .env overrides matching keys while process-only keys stay intact.
        let reload = DaemonConfig::from_store_with_env(&store, &proc_env, true);
        assert_eq!(reload.mode, DaemonMode::OpenAi);
        assert!(reload.refine);
        assert!(!reload.overlay_enabled);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn platform_hotkey_defaults_apply_only_when_no_slot_is_configured() {
        let empty = BTreeMap::new();
        let mac_default = DaemonConfig::from_env_map_for_platform(&empty, PlatformKind::MacOs);
        assert_eq!(mac_default.hotkeys.toggle, None);
        assert_eq!(
            mac_default.hotkeys.hold.as_deref(),
            Some(DEFAULT_MACOS_HOLD_HOTKEY)
        );

        let win_default = DaemonConfig::from_env_map_for_platform(&empty, PlatformKind::Windows);
        assert_eq!(
            win_default.hotkeys.toggle.as_deref(),
            Some(DEFAULT_WINDOWS_TOGGLE_HOTKEY)
        );
        assert_eq!(
            win_default.hotkeys.hold.as_deref(),
            Some(DEFAULT_WINDOWS_HOLD_HOTKEY)
        );

        // Explicitly setting only one slot must preserve the other slot as unset (`None`).
        let mut toggle_only = BTreeMap::new();
        toggle_only.insert("PULSESCRIBE_TOGGLE_HOTKEY".into(), "f19".into());
        let mac_toggle = DaemonConfig::from_env_map_for_platform(&toggle_only, PlatformKind::MacOs);
        assert_eq!(mac_toggle.hotkeys.toggle.as_deref(), Some("f19"));
        assert_eq!(mac_toggle.hotkeys.hold, None);

        let win_toggle =
            DaemonConfig::from_env_map_for_platform(&toggle_only, PlatformKind::Windows);
        assert_eq!(win_toggle.hotkeys.toggle.as_deref(), Some("f19"));
        assert_eq!(win_toggle.hotkeys.hold, None);

        let mut hold_only = BTreeMap::new();
        hold_only.insert("PULSESCRIBE_HOLD_HOTKEY".into(), "ctrl+shift+space".into());
        let win_hold = DaemonConfig::from_env_map_for_platform(&hold_only, PlatformKind::Windows);
        assert_eq!(win_hold.hotkeys.toggle, None);
        assert_eq!(win_hold.hotkeys.hold.as_deref(), Some("ctrl+shift+space"));
    }

    #[test]
    fn provider_env_captures_credentials_and_redacts_debug_output() {
        let mut env = BTreeMap::new();
        env.insert("OPENAI_API_KEY".into(), " sk-top-secret-123 ".into());
        env.insert("UNRELATED_SHELL_VAR".into(), "ignored".into());

        let cfg = DaemonConfig::from_env_map(&env);
        assert_eq!(
            cfg.provider_env.get("OPENAI_API_KEY"),
            Some("sk-top-secret-123")
        );
        assert_eq!(cfg.provider_env.get("UNRELATED_SHELL_VAR"), None);

        let debug = format!("{cfg:?}");
        assert!(debug.contains("[REDACTED"));
        assert!(!debug.contains("sk-top-secret-123"));

        // `OPENAI_COMPATIBLE_MODEL` takes precedence over stale `PULSESCRIBE_MODEL`.
        let mut compat = BTreeMap::new();
        compat.insert("PULSESCRIBE_MODE".into(), "openai_compatible".into());
        compat.insert("PULSESCRIBE_MODEL".into(), "nova-3".into());
        compat.insert("OPENAI_COMPATIBLE_MODEL".into(), "faster-whisper".into());
        let compat_cfg = DaemonConfig::from_env_map(&compat);
        assert_eq!(compat_cfg.mode, DaemonMode::OpenAiCompatible);
        assert_eq!(compat_cfg.model.as_deref(), Some("faster-whisper"));
    }
}
