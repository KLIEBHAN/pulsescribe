//! Typed Tauri v2 IPC commands bridging the lazy WebView UI to `pulsescribe-*` crates.

use std::collections::BTreeMap;
use std::fs;
use std::path::Path;
use std::process::Command;
use std::sync::Arc;

use chrono::Local;
use pulsescribe_audio::{list_input_devices, InputDeviceInfo};
use pulsescribe_core::atomic_io::write_text_atomic_restricted;
use pulsescribe_core::daemon_status_feedback::{
    build_daemon_status_hint, build_daemon_status_label, StatusDetail,
};
use pulsescribe_core::env::parse_bool;
use pulsescribe_core::hotkey::{
    normalize_windows_hotkey, validate_macos_hotkey_change, windows_hotkeys_conflict,
};
use pulsescribe_core::onboarding::{OnboardingChoice, OnboardingStep};
use pulsescribe_core::prompts::{
    build_prompt_overrides_from_editor_state, PromptEditorContext, PromptsConfig,
};
use pulsescribe_core::vocabulary::{
    analyze_vocabulary_text, DEEPGRAM_KEYWORD_LIMIT, LOCAL_KEYWORD_LIMIT,
};
use pulsescribe_core::{
    AppState, DaemonStatusError, HotkeyKind, HotkeyValidationContext, HotkeyValidationLevel,
    HotkeyValidationResult, PreferencesStore, TranscriptEntry,
};
use pulsescribe_daemon::DaemonConfig;
use pulsescribe_local_asr::{
    resolve_model_spec, LocalRuntimeCapabilities, ModelSpec, ModelStatus, DEFAULT_LOCAL_MODEL_ID,
};
use pulsescribe_platform::{
    ArboardClipboard, ClipboardBackend, PermissionKind, PermissionSnapshot, PlatformKind,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{AppHandle, State};
use url::Url;

use crate::state::DesktopState;
use crate::updates::{check_latest_release, UpdateCheckResult};
use crate::window::{
    emit_to_main_if_open, MODEL_DOWNLOAD_FINISHED_EVENT, MODEL_DOWNLOAD_PROGRESS_EVENT,
};

const API_KEY_NAMES: &[&str] = &[
    "DEEPGRAM_API_KEY",
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_COMPATIBLE_API_KEY",
];

/// Top-level app status payload returned on window load.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppStatePayload {
    pub version: String,
    pub platform: String,
    pub paste_shortcut: String,
    pub config_dir: String,
    pub daemon_state: AppState,
    pub status_label: String,
    pub status_hint: String,
    pub status_detail: Option<String>,
    pub status_error: Option<DaemonStatusError>,
    pub last_transcript: Option<String>,
    pub last_test_transcript: Option<String>,
    pub last_test_error: Option<String>,
    pub onboarding_complete: bool,
    pub onboarding_step: OnboardingStep,
    pub onboarding_choice: Option<OnboardingChoice>,
    pub show_welcome_on_startup: bool,
    pub initial_route: String,
    pub local_capabilities: LocalRuntimeCapabilities,
    pub toggle_hotkey: Option<String>,
    pub hold_hotkey: Option<String>,
    pub mode: String,
}

/// Settings view payload. Never exposes raw API key secrets to the WebView.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SettingsPayload {
    pub mode: String,
    pub model: String,
    pub local_model: String,
    pub language: String,
    pub refine: bool,
    pub refine_provider: String,
    pub refine_model: String,
    pub context: String,
    pub voice_commands: bool,
    pub streaming: bool,
    pub deepgram_warm_websocket: bool,
    pub overlay_enabled: bool,
    pub sound_enabled: bool,
    pub auto_paste: bool,
    pub show_rtf: bool,
    pub input_device: String,
    pub toggle_hotkey: String,
    pub hold_hotkey: String,
    pub transcribing_timeout_secs: f64,
    pub openai_compatible_base_url: String,
    pub openai_compatible_model: String,
    pub openrouter_provider_order: String,
    pub openrouter_allow_fallbacks: bool,
    pub api_keys_configured: BTreeMap<String, bool>,
    pub api_keys_masked: BTreeMap<String, String>,
}

/// Partial settings save payload accepted from the WebView.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct SaveSettingsInput {
    pub mode: Option<String>,
    pub model: Option<String>,
    pub local_model: Option<String>,
    pub language: Option<String>,
    pub refine: Option<bool>,
    pub refine_provider: Option<String>,
    pub refine_model: Option<String>,
    pub context: Option<String>,
    pub voice_commands: Option<bool>,
    pub streaming: Option<bool>,
    pub deepgram_warm_websocket: Option<bool>,
    pub overlay_enabled: Option<bool>,
    pub sound_enabled: Option<bool>,
    pub auto_paste: Option<bool>,
    pub show_rtf: Option<bool>,
    pub input_device: Option<String>,
    pub toggle_hotkey: Option<String>,
    pub hold_hotkey: Option<String>,
    pub transcribing_timeout_secs: Option<f64>,
    pub openai_compatible_base_url: Option<String>,
    pub openai_compatible_model: Option<String>,
    pub openrouter_provider_order: Option<String>,
    pub openrouter_allow_fallbacks: Option<bool>,
    /// Map of API key env name -> `Some("new-key")` to save, `Some("")` or `None` to remove.
    #[serde(default)]
    pub api_keys: BTreeMap<String, Option<String>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AudioDevicePayload {
    pub id: Option<String>,
    pub name: String,
    pub default_sample_rate: u32,
    pub default_channels: u16,
    pub is_default: bool,
}

impl From<InputDeviceInfo> for AudioDevicePayload {
    fn from(info: InputDeviceInfo) -> Self {
        Self {
            id: info.id,
            name: info.name,
            default_sample_rate: info.default_sample_rate,
            default_channels: info.default_channels,
            is_default: info.is_default,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HistoryEntryPayload {
    pub timestamp: String,
    pub text: String,
    pub mode: Option<String>,
    pub language: Option<String>,
    pub app: Option<String>,
    pub refined: bool,
}

impl From<TranscriptEntry> for HistoryEntryPayload {
    fn from(entry: TranscriptEntry) -> Self {
        Self {
            timestamp: entry.timestamp,
            text: entry.text,
            mode: entry.mode,
            language: entry.language,
            app: entry.app,
            refined: entry.refined,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VocabularyPayload {
    pub keywords: Vec<String>,
    pub issues: Vec<String>,
    pub local_limit: usize,
    pub deepgram_limit: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PromptsPayload {
    pub contexts: Vec<PromptContextInfo>,
    pub drafts: BTreeMap<String, String>,
    pub defaults: BTreeMap<String, String>,
    pub has_custom_overrides: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PromptContextInfo {
    pub key: String,
    pub label: String,
    pub description: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalModelsPayload {
    pub models: Vec<ModelStatus>,
    pub active_downloads: Vec<String>,
    pub loaded_model_id: Option<String>,
    pub capabilities: LocalRuntimeCapabilities,
    pub default_model_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ThemePreferencePayload {
    pub theme: String,
    pub accent: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DiagnosticsExportPayload {
    pub path: String,
    pub preview: String,
}

/// Mask an API key for display (keeps only short non-sensitive prefix/suffix hint).
pub fn mask_secret(secret: &str) -> String {
    let trimmed = secret.trim();
    let chars: Vec<char> = trimmed.chars().collect();
    if chars.len() <= 8 {
        return "••••".to_owned();
    }
    let prefix: String = chars[..3].iter().collect();
    let suffix: String = chars[chars.len() - 4..].iter().collect();
    format!("{prefix}…{suffix}")
}

/// Strip credentials, query parameters, and fragments from a URL before placing it in diagnostics.
pub fn redact_url_for_diagnostics(raw: &str) -> String {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return String::new();
    }
    let Ok(mut url) = Url::parse(trimmed) else {
        return "[REDACTED_URL]".to_owned();
    };
    if !url.username().is_empty() || url.password().is_some() {
        let _ = url.set_username("REDACTED");
        let _ = url.set_password(None);
    }
    let had_query = url.query().is_some();
    url.set_query(None);
    url.set_fragment(None);
    if had_query {
        format!("{url}?[REDACTED]")
    } else {
        url.into()
    }
}

/// Redact common secret tokens from diagnostic log tails before writing or returning them.
pub fn redact_log_text(raw: &str) -> String {
    raw.lines()
        .map(|line| {
            let lower = line.to_ascii_lowercase();
            if lower.contains("api_key=")
                || lower.contains("bearer ")
                || lower.contains("authorization:")
                || lower.contains("token=")
                || line.contains("sk-")
                || line.contains("gsk_")
            {
                "[REDACTED_LOG_LINE]"
            } else {
                line
            }
        })
        .collect::<Vec<_>>()
        .join("\n")
}

/// Resolve a non-empty explicit model identifier against the local ASR catalog.
pub fn resolve_explicit_model_spec(model_id: &str) -> Result<&'static ModelSpec, String> {
    let trimmed = model_id.trim();
    if trimmed.is_empty() {
        return Err("Model ID must not be empty".to_owned());
    }
    resolve_model_spec(Some(trimmed)).ok_or_else(|| format!("Unknown local model '{trimmed}'"))
}

/// Build [`SettingsPayload`] from the resolved [`DaemonConfig`] (which already applies dotenv
/// `${VAR}` expansion and `process_env` fallbacks) plus non-secret `.env` model preferences.
pub fn build_settings_payload(store: &PreferencesStore, cfg: &DaemonConfig) -> SettingsPayload {
    let env = store.read_env_file();
    let mut api_keys_configured = BTreeMap::new();
    let mut api_keys_masked = BTreeMap::new();
    for &key in API_KEY_NAMES {
        let val = cfg
            .provider_env
            .get(key)
            .map(str::trim)
            .filter(|v| !v.is_empty());
        api_keys_configured.insert(key.to_owned(), val.is_some());
        if let Some(secret) = val {
            api_keys_masked.insert(key.to_owned(), mask_secret(secret));
        }
    }

    let voice_commands =
        parse_bool(cfg.provider_env.get("PULSESCRIBE_VOICE_COMMANDS")).unwrap_or(true);
    let openrouter_allow_fallbacks =
        parse_bool(cfg.provider_env.get("OPENROUTER_ALLOW_FALLBACKS")).unwrap_or(true);

    SettingsPayload {
        mode: cfg.mode.as_str().to_owned(),
        model: env
            .get("PULSESCRIBE_MODEL")
            .cloned()
            .or_else(|| {
                (!matches!(
                    cfg.mode,
                    pulsescribe_daemon::DaemonMode::Local
                        | pulsescribe_daemon::DaemonMode::OpenAiCompatible
                ))
                .then(|| cfg.model.clone())
                .flatten()
            })
            .unwrap_or_default(),
        local_model: env
            .get("PULSESCRIBE_LOCAL_MODEL")
            .cloned()
            .or_else(|| {
                (cfg.mode == pulsescribe_daemon::DaemonMode::Local)
                    .then(|| cfg.model.clone())
                    .flatten()
            })
            .unwrap_or_else(|| DEFAULT_LOCAL_MODEL_ID.to_owned()),
        language: cfg.language.clone().unwrap_or_else(|| "auto".to_owned()),
        refine: cfg.refine,
        refine_provider: cfg.refine_provider.clone().unwrap_or_default(),
        refine_model: cfg.refine_model.clone().unwrap_or_default(),
        context: cfg.context.clone().unwrap_or_default(),
        voice_commands,
        streaming: cfg.streaming,
        deepgram_warm_websocket: cfg.deepgram_warm_websocket,
        overlay_enabled: cfg.overlay_enabled,
        sound_enabled: cfg.sound_enabled,
        auto_paste: cfg.auto_paste,
        show_rtf: cfg.show_rtf,
        input_device: cfg.input_device.clone().unwrap_or_default(),
        toggle_hotkey: cfg.hotkeys.toggle.clone().unwrap_or_default(),
        hold_hotkey: cfg.hotkeys.hold.clone().unwrap_or_default(),
        transcribing_timeout_secs: cfg.timings.transcribing_timeout.as_secs_f64(),
        openai_compatible_base_url: cfg
            .provider_env
            .get("OPENAI_COMPATIBLE_BASE_URL")
            .unwrap_or_default()
            .to_owned(),
        openai_compatible_model: cfg
            .provider_env
            .get("OPENAI_COMPATIBLE_MODEL")
            .unwrap_or_default()
            .to_owned(),
        openrouter_provider_order: cfg
            .provider_env
            .get("OPENROUTER_PROVIDER_ORDER")
            .unwrap_or_default()
            .to_owned(),
        openrouter_allow_fallbacks,
        api_keys_configured,
        api_keys_masked,
    }
}

/// Convert [`SaveSettingsInput`] into atomic `.env` key/value updates (rejecting unknown API keys).
pub fn build_env_updates(
    patch: &SaveSettingsInput,
) -> Result<Vec<(String, Option<String>)>, String> {
    let mut updates: Vec<(String, Option<String>)> = Vec::new();
    let push_opt = |updates: &mut Vec<(String, Option<String>)>, key: &str, raw: &str| {
        let trimmed = raw.trim();
        if trimmed.is_empty() {
            updates.push((key.to_owned(), None));
        } else {
            updates.push((key.to_owned(), Some(trimmed.to_owned())));
        }
    };
    let push_bool = |updates: &mut Vec<(String, Option<String>)>, key: &str, val: bool| {
        updates.push((
            key.to_owned(),
            Some(if val { "true" } else { "false" }.to_owned()),
        ));
    };

    if let Some(ref mode) = patch.mode {
        push_opt(&mut updates, "PULSESCRIBE_MODE", mode);
    }
    if let Some(ref model) = patch.model {
        push_opt(&mut updates, "PULSESCRIBE_MODEL", model);
    }
    if let Some(ref local_model) = patch.local_model {
        push_opt(&mut updates, "PULSESCRIBE_LOCAL_MODEL", local_model);
    }
    if let Some(ref language) = patch.language {
        let trimmed = language.trim();
        if trimmed.is_empty() || trimmed.eq_ignore_ascii_case("auto") {
            updates.push(("PULSESCRIBE_LANGUAGE".to_owned(), None));
        } else {
            updates.push(("PULSESCRIBE_LANGUAGE".to_owned(), Some(trimmed.to_owned())));
        }
    }
    if let Some(val) = patch.refine {
        push_bool(&mut updates, "PULSESCRIBE_REFINE", val);
    }
    if let Some(ref val) = patch.refine_provider {
        push_opt(&mut updates, "PULSESCRIBE_REFINE_PROVIDER", val);
    }
    if let Some(ref val) = patch.refine_model {
        push_opt(&mut updates, "PULSESCRIBE_REFINE_MODEL", val);
    }
    if let Some(ref val) = patch.context {
        push_opt(&mut updates, "PULSESCRIBE_CONTEXT", val);
    }
    if let Some(val) = patch.voice_commands {
        push_bool(&mut updates, "PULSESCRIBE_VOICE_COMMANDS", val);
    }
    if let Some(val) = patch.streaming {
        push_bool(&mut updates, "PULSESCRIBE_STREAMING", val);
    }
    if let Some(val) = patch.deepgram_warm_websocket {
        push_bool(&mut updates, "PULSESCRIBE_DEEPGRAM_WARM_WEBSOCKET", val);
    }
    if let Some(val) = patch.overlay_enabled {
        push_bool(&mut updates, "PULSESCRIBE_OVERLAY", val);
    }
    if let Some(val) = patch.sound_enabled {
        push_bool(&mut updates, "PULSESCRIBE_SOUND", val);
    }
    if let Some(val) = patch.auto_paste {
        push_bool(&mut updates, "PULSESCRIBE_AUTO_PASTE", val);
    }
    if let Some(val) = patch.show_rtf {
        push_bool(&mut updates, "PULSESCRIBE_SHOW_RTF", val);
    }
    if let Some(ref val) = patch.input_device {
        push_opt(&mut updates, "PULSESCRIBE_INPUT_DEVICE", val);
    }
    if let Some(ref val) = patch.toggle_hotkey {
        let trimmed = val.trim().to_ascii_lowercase();
        updates.push((
            "PULSESCRIBE_TOGGLE_HOTKEY".to_owned(),
            (!trimmed.is_empty()).then_some(trimmed),
        ));
        updates.push(("PULSESCRIBE_HOTKEY".to_owned(), None));
        updates.push(("PULSESCRIBE_HOTKEY_MODE".to_owned(), None));
    }
    if let Some(ref val) = patch.hold_hotkey {
        let trimmed = val.trim().to_ascii_lowercase();
        updates.push((
            "PULSESCRIBE_HOLD_HOTKEY".to_owned(),
            (!trimmed.is_empty()).then_some(trimmed),
        ));
        updates.push(("PULSESCRIBE_HOTKEY".to_owned(), None));
        updates.push(("PULSESCRIBE_HOTKEY_MODE".to_owned(), None));
    }
    if let Some(secs) = patch.transcribing_timeout_secs {
        if secs.is_finite() {
            let clamped = secs.clamp(5.0, 300.0);
            updates.push((
                "PULSESCRIBE_TRANSCRIBING_TIMEOUT".to_owned(),
                Some(format!("{clamped:.0}")),
            ));
        }
    }
    if let Some(ref val) = patch.openai_compatible_base_url {
        push_opt(&mut updates, "OPENAI_COMPATIBLE_BASE_URL", val);
    }
    if let Some(ref val) = patch.openai_compatible_model {
        push_opt(&mut updates, "OPENAI_COMPATIBLE_MODEL", val);
    }
    if let Some(ref val) = patch.openrouter_provider_order {
        push_opt(&mut updates, "OPENROUTER_PROVIDER_ORDER", val);
    }
    if let Some(val) = patch.openrouter_allow_fallbacks {
        push_bool(&mut updates, "OPENROUTER_ALLOW_FALLBACKS", val);
    }

    for (key, maybe_val) in &patch.api_keys {
        if !API_KEY_NAMES.contains(&key.as_str()) {
            return Err(format!("Unsupported API key setting: {key}"));
        }
        let cleaned = maybe_val
            .as_deref()
            .map(str::trim)
            .filter(|v| !v.is_empty())
            .map(ToOwned::to_owned);
        updates.push((key.clone(), cleaned));
    }

    Ok(updates)
}

/// Validate a proposed hotkey change for `platform` using the supplied macOS Carbon probe.
#[allow(clippy::too_many_arguments)]
pub fn validate_hotkey_for_platform<F>(
    platform: PlatformKind,
    kind: HotkeyKind,
    raw_value: &str,
    toggle_current: &str,
    hold_current: &str,
    replace_other: bool,
    input_monitoring_allowed: bool,
    carbon_probe: F,
) -> HotkeyValidationResult
where
    F: FnOnce(pulsescribe_core::CarbonHotkey) -> bool,
{
    if platform.is_macos() {
        let ctx = HotkeyValidationContext {
            toggle_current: toggle_current.to_owned(),
            hold_current: hold_current.to_owned(),
            replace_other,
            input_monitoring_allowed,
        };
        return validate_macos_hotkey_change(kind, Some(raw_value), &ctx, carbon_probe);
    }

    match normalize_windows_hotkey(Some(raw_value)) {
        Err(msg) => HotkeyValidationResult {
            normalized: raw_value.trim().to_ascii_lowercase(),
            level: HotkeyValidationLevel::Error,
            message: Some(msg),
        },
        Ok(normalized) => {
            if normalized.is_empty() {
                return HotkeyValidationResult {
                    normalized,
                    level: HotkeyValidationLevel::Ok,
                    message: None,
                };
            }
            let other = match kind {
                HotkeyKind::Toggle => hold_current,
                HotkeyKind::Hold => toggle_current,
            };
            if !replace_other && windows_hotkeys_conflict(Some(&normalized), Some(other)) {
                return HotkeyValidationResult {
                    normalized,
                    level: HotkeyValidationLevel::Error,
                    message: Some(format!("Conflicts with the other hotkey ({other}).")),
                };
            }
            HotkeyValidationResult {
                normalized,
                level: HotkeyValidationLevel::Ok,
                message: None,
            }
        }
    }
}

/// Open a local directory in Finder (macOS) or Explorer (Windows), reaping the launcher child.
pub fn open_directory_in_os(dir: &Path) -> Result<(), String> {
    if !dir.exists() {
        fs::create_dir_all(dir)
            .map_err(|err| format!("Failed to create config directory: {err}"))?;
    }
    if !dir.is_dir() {
        return Err(format!("Config path is not a directory: {}", dir.display()));
    }
    #[cfg(target_os = "macos")]
    {
        let mut child = Command::new("open")
            .arg(dir)
            .spawn()
            .map_err(|err| format!("Failed to open Finder: {err}"))?;
        std::thread::spawn(move || {
            let _ = child.wait();
        });
        Ok(())
    }
    #[cfg(target_os = "windows")]
    {
        let mut child = Command::new("explorer")
            .arg(dir)
            .spawn()
            .map_err(|err| format!("Failed to open Explorer: {err}"))?;
        std::thread::spawn(move || {
            let _ = child.wait();
        });
        Ok(())
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = dir;
        Err("Opening config folder is unsupported on this OS".to_owned())
    }
}

fn parse_permission_kind(raw: &str) -> Result<PermissionKind, String> {
    match raw.trim().to_ascii_lowercase().as_str() {
        "microphone" | "mic" => Ok(PermissionKind::Microphone),
        "accessibility" => Ok(PermissionKind::Accessibility),
        "input_monitoring" | "inputmonitoring" => Ok(PermissionKind::InputMonitoring),
        other => Err(format!("Unknown permission kind: {other}")),
    }
}

fn build_prompts_payload(config: &PromptsConfig) -> PromptsPayload {
    let defaults_cfg = PromptsConfig::defaults();
    let mut drafts = BTreeMap::new();
    let mut defaults = BTreeMap::new();
    let mut contexts = Vec::with_capacity(PromptEditorContext::ALL.len());
    for ctx in PromptEditorContext::ALL {
        let key = ctx.as_str().to_owned();
        drafts.insert(key.clone(), config.editor_text(ctx));
        defaults.insert(key.clone(), defaults_cfg.editor_text(ctx));
        contexts.push(PromptContextInfo {
            key,
            label: ctx.label().to_owned(),
            description: ctx.description().to_owned(),
        });
    }
    let has_custom_overrides = !config.filter_overrides(&defaults_cfg).is_empty();
    PromptsPayload {
        contexts,
        drafts,
        defaults,
        has_custom_overrides,
    }
}

#[tauri::command]
pub fn get_app_state(state: State<'_, DesktopState>) -> AppStatePayload {
    let cached = state.cached_status();
    let cfg = state.daemon.config();
    let platform = PlatformKind::current();
    let detail = match (cached.error.as_ref(), cached.detail.as_deref()) {
        (Some(err), _) => StatusDetail::Error(err),
        (None, Some(text)) => StatusDetail::Text(text),
        (None, None) => StatusDetail::None,
    };

    AppStatePayload {
        version: env!("CARGO_PKG_VERSION").to_owned(),
        platform: match platform {
            PlatformKind::MacOs => "macos",
            PlatformKind::Windows => "windows",
            PlatformKind::Linux => "linux",
            PlatformKind::Other => "other",
        }
        .to_owned(),
        paste_shortcut: platform.paste_shortcut_hint().to_owned(),
        config_dir: state.config_dir.display().to_string(),
        daemon_state: cached.state,
        status_label: build_daemon_status_label(cached.state, detail, false, 80),
        status_hint: build_daemon_status_hint(cached.state, detail, 140),
        status_detail: cached.detail,
        status_error: cached.error,
        last_transcript: cached.last_transcript,
        last_test_transcript: cached.last_test_transcript,
        last_test_error: cached.last_test_error,
        onboarding_complete: state.preferences.is_onboarding_complete(),
        onboarding_step: state.preferences.get_onboarding_step(),
        onboarding_choice: state.preferences.get_onboarding_choice(),
        show_welcome_on_startup: state.preferences.get_show_welcome_on_startup(),
        initial_route: state.pending_route(),
        local_capabilities: LocalRuntimeCapabilities::current(),
        toggle_hotkey: cfg.hotkeys.toggle,
        hold_hotkey: cfg.hotkeys.hold,
        mode: cfg.mode.as_str().to_owned(),
    }
}

#[tauri::command]
pub fn get_settings(state: State<'_, DesktopState>) -> SettingsPayload {
    let cfg = state.daemon.config();
    build_settings_payload(&state.preferences, &cfg)
}

#[tauri::command]
pub async fn save_settings(
    state: State<'_, DesktopState>,
    patch: SaveSettingsInput,
) -> Result<SettingsPayload, String> {
    let updates = build_env_updates(&patch)?;
    let prefs = Arc::clone(&state.preferences);
    let daemon = state.daemon.clone();
    tokio::task::spawn_blocking(move || {
        prefs
            .update_env_settings(&updates)
            .map_err(|err| format!("Failed to save settings: {err}"))?;
        daemon.reload_config();
        let fresh_cfg = DaemonConfig::from_store_reload(&prefs);
        Ok(build_settings_payload(&prefs, &fresh_cfg))
    })
    .await
    .map_err(|err| format!("Settings save task failed: {err}"))?
}

#[tauri::command]
pub fn validate_hotkey(
    state: State<'_, DesktopState>,
    kind: String,
    value: String,
    toggle_current: String,
    hold_current: String,
    replace_other: bool,
) -> HotkeyValidationResult {
    let kind = HotkeyKind::from_str_or_toggle(&kind);
    let input_monitoring = state.permissions.has_input_monitoring();
    #[cfg(target_os = "macos")]
    let probe = pulsescribe_platform::macos::probe_carbon_hotkey;
    #[cfg(not(target_os = "macos"))]
    let probe = |_: pulsescribe_core::CarbonHotkey| true;
    validate_hotkey_for_platform(
        PlatformKind::current(),
        kind,
        &value,
        &toggle_current,
        &hold_current,
        replace_other,
        input_monitoring,
        probe,
    )
}

#[tauri::command]
pub fn check_permissions(state: State<'_, DesktopState>) -> PermissionSnapshot {
    state.permissions.snapshot()
}

#[tauri::command]
pub fn request_permission(state: State<'_, DesktopState>, kind: String) -> Result<(), String> {
    let parsed = parse_permission_kind(&kind)?;
    state
        .permissions
        .request_permission(parsed)
        .map_err(|err| err.to_string())
}

#[tauri::command]
pub fn open_privacy_settings(state: State<'_, DesktopState>, kind: String) -> Result<(), String> {
    let parsed = parse_permission_kind(&kind)?;
    state
        .permissions
        .open_privacy_settings(parsed)
        .map_err(|err| err.to_string())
}

#[tauri::command]
pub async fn list_audio_devices() -> Result<Vec<AudioDevicePayload>, String> {
    tokio::task::spawn_blocking(|| {
        list_input_devices()
            .map(|devices| devices.into_iter().map(AudioDevicePayload::from).collect())
            .map_err(|err| err.to_string())
    })
    .await
    .map_err(|err| format!("Audio device query failed: {err}"))?
}

#[tauri::command]
pub async fn get_history(
    state: State<'_, DesktopState>,
    limit: Option<usize>,
) -> Result<Vec<HistoryEntryPayload>, String> {
    let count = limit.unwrap_or(100).clamp(1, 500);
    let history = Arc::clone(&state.history);
    tokio::task::spawn_blocking(move || {
        history
            .get_recent_transcripts(count)
            .into_iter()
            .map(HistoryEntryPayload::from)
            .collect()
    })
    .await
    .map_err(|err| format!("Failed to read history: {err}"))
}

#[tauri::command]
pub async fn clear_history(state: State<'_, DesktopState>) -> Result<(), String> {
    let history = Arc::clone(&state.history);
    tokio::task::spawn_blocking(move || history.clear().map_err(|err| err.to_string()))
        .await
        .map_err(|err| format!("Failed to clear history: {err}"))?
}

#[tauri::command]
pub async fn delete_history_entry(
    state: State<'_, DesktopState>,
    timestamp: String,
    text: String,
) -> Result<bool, String> {
    let history = Arc::clone(&state.history);
    tokio::task::spawn_blocking(move || {
        history
            .delete_entry(&timestamp, &text)
            .map_err(|err| err.to_string())
    })
    .await
    .map_err(|err| format!("Failed to delete history entry: {err}"))?
}

#[tauri::command]
pub async fn copy_text_to_clipboard(text: String) -> Result<(), String> {
    tokio::task::spawn_blocking(move || {
        ArboardClipboard
            .write_text(&text)
            .map_err(|err| err.to_string())
    })
    .await
    .map_err(|err| format!("Clipboard task failed: {err}"))?
}

#[tauri::command]
pub fn get_vocabulary(state: State<'_, DesktopState>) -> VocabularyPayload {
    let loaded = state.vocabulary.load_state();
    VocabularyPayload {
        keywords: loaded.keywords(),
        issues: loaded.issues,
        local_limit: LOCAL_KEYWORD_LIMIT,
        deepgram_limit: DEEPGRAM_KEYWORD_LIMIT,
    }
}

#[tauri::command]
pub async fn save_vocabulary(
    state: State<'_, DesktopState>,
    raw_text: String,
) -> Result<VocabularyPayload, String> {
    let vocab = Arc::clone(&state.vocabulary);
    tokio::task::spawn_blocking(move || {
        let analysis = analyze_vocabulary_text(Some(&raw_text));
        let saved = vocab
            .save_state(&analysis.keywords)
            .map_err(|err| err.to_string())?;
        Ok(VocabularyPayload {
            keywords: saved.keywords(),
            issues: saved.issues,
            local_limit: LOCAL_KEYWORD_LIMIT,
            deepgram_limit: DEEPGRAM_KEYWORD_LIMIT,
        })
    })
    .await
    .map_err(|err| format!("Vocabulary save task failed: {err}"))?
}

#[tauri::command]
pub fn get_prompts(state: State<'_, DesktopState>) -> PromptsPayload {
    let cfg = state.prompts.load();
    build_prompts_payload(&cfg)
}

#[tauri::command]
pub async fn save_prompts(
    state: State<'_, DesktopState>,
    drafts: BTreeMap<String, String>,
) -> Result<PromptsPayload, String> {
    let prompts = Arc::clone(&state.prompts);
    tokio::task::spawn_blocking(move || {
        let existing = prompts.load();
        let overrides = build_prompt_overrides_from_editor_state(&existing, &drafts, None, None);
        let saved = prompts
            .save_overrides(&overrides)
            .map_err(|err| err.to_string())?;
        Ok(build_prompts_payload(&saved))
    })
    .await
    .map_err(|err| format!("Prompts save task failed: {err}"))?
}

#[tauri::command]
pub async fn reset_prompts(state: State<'_, DesktopState>) -> Result<PromptsPayload, String> {
    let prompts = Arc::clone(&state.prompts);
    tokio::task::spawn_blocking(move || {
        prompts.reset_to_defaults().map_err(|err| err.to_string())?;
        Ok(build_prompts_payload(&PromptsConfig::defaults()))
    })
    .await
    .map_err(|err| format!("Prompts reset task failed: {err}"))?
}

#[tauri::command]
pub async fn list_local_models(
    state: State<'_, DesktopState>,
) -> Result<LocalModelsPayload, String> {
    let cache = state.local_asr.cache().clone();
    let models = tokio::task::spawn_blocking(move || cache.list_statuses())
        .await
        .map_err(|err| format!("Failed to scan model cache: {err}"))?;
    Ok(LocalModelsPayload {
        models,
        active_downloads: state.active_download_ids(),
        loaded_model_id: state.local_asr.loaded_model_id(),
        capabilities: LocalRuntimeCapabilities::current(),
        default_model_id: DEFAULT_LOCAL_MODEL_ID.to_owned(),
    })
}

#[tauri::command]
pub async fn download_local_model(
    app: AppHandle,
    state: State<'_, DesktopState>,
    model_id: String,
) -> Result<LocalModelsPayload, String> {
    let spec = resolve_explicit_model_spec(&model_id)?;
    let guard = state.begin_download(spec.id)?;
    let cancel_flag = guard
        .cancel_flag()
        .expect("download guard always carries a cancel flag");

    let cache = state.local_asr.cache().clone();
    let downloader = state.downloader.clone();
    let app_clone = app.clone();
    let download_res = downloader
        .ensure_downloaded(&cache, spec, cancel_flag, |progress| {
            emit_to_main_if_open(&app_clone, MODEL_DOWNLOAD_PROGRESS_EVENT, &progress);
        })
        .await;

    drop(guard);
    match &download_res {
        Ok(_) => emit_to_main_if_open(
            &app,
            MODEL_DOWNLOAD_FINISHED_EVENT,
            &serde_json::json!({ "model_id": spec.id, "ok": true, "error": serde_json::Value::Null }),
        ),
        Err(err) => emit_to_main_if_open(
            &app,
            MODEL_DOWNLOAD_FINISHED_EVENT,
            &serde_json::json!({ "model_id": spec.id, "ok": false, "error": err.to_string() }),
        ),
    }
    download_res.map_err(|err| err.to_string())?;
    list_local_models(state).await
}

#[tauri::command]
pub fn cancel_model_download(state: State<'_, DesktopState>, model_id: String) -> bool {
    let Ok(spec) = resolve_explicit_model_spec(&model_id) else {
        return false;
    };
    state.cancel_download(spec.id)
}

#[tauri::command]
pub async fn delete_local_model(
    state: State<'_, DesktopState>,
    model_id: String,
) -> Result<LocalModelsPayload, String> {
    let spec = resolve_explicit_model_spec(&model_id)?;
    let _guard = state.begin_delete(spec.id)?;
    if state.local_asr.loaded_model_id().as_deref() == Some(spec.id) {
        state.local_asr.unload();
    }
    let cache = state.local_asr.cache().clone();
    tokio::task::spawn_blocking(move || cache.remove_managed(spec))
        .await
        .map_err(|err| format!("Failed to delete model: {err}"))?
        .map_err(|err| err.to_string())?;
    drop(_guard);
    list_local_models(state).await
}

#[tauri::command]
pub fn unload_local_model(state: State<'_, DesktopState>) {
    state.local_asr.unload();
}

#[tauri::command]
pub fn toggle_dictation(state: State<'_, DesktopState>) {
    state.daemon.toggle_recording();
}

#[tauri::command]
pub fn start_test_dictation(state: State<'_, DesktopState>) {
    state.update_status(|s| {
        s.last_test_transcript = None;
        s.last_test_error = None;
    });
    state.daemon.start_test();
}

#[tauri::command]
pub fn cancel_dictation(state: State<'_, DesktopState>) {
    state.daemon.cancel();
}

#[tauri::command]
pub fn set_test_hotkey_mode(state: State<'_, DesktopState>, enabled: bool) {
    state.daemon.set_test_hotkey_mode(enabled);
}

#[tauri::command]
pub async fn copy_last_transcript(
    state: State<'_, DesktopState>,
) -> Result<Option<String>, String> {
    Ok(state
        .daemon
        .copy_last_transcript()
        .await
        .map(ToOwned::to_owned))
}

#[tauri::command]
pub async fn set_onboarding_state(
    state: State<'_, DesktopState>,
    step: Option<String>,
    choice: Option<String>,
    show_welcome_on_startup: Option<bool>,
) -> Result<(), String> {
    let parsed_step = match step {
        Some(raw) => Some(
            OnboardingStep::parse(&raw).ok_or_else(|| format!("Unknown onboarding step: {raw}"))?,
        ),
        None => None,
    };
    let parsed_choice = match choice {
        Some(raw) if raw.trim().is_empty() => Some(None),
        Some(raw) => {
            Some(Some(OnboardingChoice::parse(&raw).ok_or_else(|| {
                format!("Unknown onboarding choice: {raw}")
            })?))
        }
        None => None,
    };
    let prefs = Arc::clone(&state.preferences);
    tokio::task::spawn_blocking(move || {
        if let Some(step) = parsed_step {
            prefs
                .set_onboarding_step(step)
                .map_err(|err| err.to_string())?;
        }
        if let Some(choice) = parsed_choice {
            prefs
                .set_onboarding_choice(choice)
                .map_err(|err| err.to_string())?;
        }
        if let Some(show) = show_welcome_on_startup {
            prefs
                .set_show_welcome_on_startup(show)
                .map_err(|err| err.to_string())?;
        }
        Ok(())
    })
    .await
    .map_err(|err| format!("Onboarding state save failed: {err}"))?
}

#[tauri::command]
pub fn get_theme_preference(state: State<'_, DesktopState>) -> ThemePreferencePayload {
    let prefs = state.preferences.load_preferences();
    let theme = prefs
        .get("ui_theme")
        .and_then(Value::as_str)
        .filter(|s| matches!(*s, "system" | "light" | "dark"))
        .unwrap_or("system")
        .to_owned();
    let accent = prefs
        .get("ui_accent")
        .and_then(Value::as_str)
        .filter(|s| matches!(*s, "mono" | "blue"))
        .unwrap_or("blue")
        .to_owned();
    ThemePreferencePayload { theme, accent }
}

#[tauri::command]
pub async fn save_theme_preference(
    state: State<'_, DesktopState>,
    theme: String,
    accent: String,
) -> Result<ThemePreferencePayload, String> {
    let norm_theme = match theme.trim().to_ascii_lowercase().as_str() {
        "light" => "light",
        "dark" => "dark",
        _ => "system",
    };
    let norm_accent = match accent.trim().to_ascii_lowercase().as_str() {
        "mono" => "mono",
        _ => "blue",
    };
    let prefs_store = Arc::clone(&state.preferences);
    tokio::task::spawn_blocking(move || {
        let mut prefs = prefs_store.load_preferences();
        prefs.insert("ui_theme".to_owned(), Value::String(norm_theme.to_owned()));
        prefs.insert(
            "ui_accent".to_owned(),
            Value::String(norm_accent.to_owned()),
        );
        prefs_store
            .save_preferences(&prefs)
            .map_err(|err| err.to_string())?;
        Ok(ThemePreferencePayload {
            theme: norm_theme.to_owned(),
            accent: norm_accent.to_owned(),
        })
    })
    .await
    .map_err(|err| format!("Theme preference save failed: {err}"))?
}

#[tauri::command]
pub async fn open_config_dir(state: State<'_, DesktopState>) -> Result<(), String> {
    let dir = state.config_dir.clone();
    tokio::task::spawn_blocking(move || open_directory_in_os(&dir))
        .await
        .map_err(|err| format!("Failed to open config folder: {err}"))?
}

#[tauri::command]
pub async fn export_diagnostics(
    state: State<'_, DesktopState>,
) -> Result<DiagnosticsExportPayload, String> {
    let cfg = state.daemon.config();
    let mut settings = build_settings_payload(&state.preferences, &cfg);
    settings.openai_compatible_base_url =
        redact_url_for_diagnostics(&settings.openai_compatible_base_url);
    let perms = state.permissions.snapshot();
    let cache = state.local_asr.cache().clone();
    let log_path = state.paths.log_file();
    let config_dir = state.config_dir.clone();

    tokio::task::spawn_blocking(move || {
        let models = cache.list_statuses();
        let devices = list_input_devices().unwrap_or_default();
        let log_tail = fs::read(&log_path)
            .ok()
            .map(|bytes| {
                let raw = String::from_utf8_lossy(&bytes);
                let lines: Vec<&str> = raw.lines().collect();
                let start = lines.len().saturating_sub(120);
                redact_log_text(&lines[start..].join("\n"))
            })
            .unwrap_or_default();

        let stamp = Local::now().format("%Y%m%d-%H%M%S").to_string();
        let report = serde_json::json!({
            "generated_at": Local::now().to_rfc3339(),
            "version": env!("CARGO_PKG_VERSION"),
            "os": std::env::consts::OS,
            "arch": std::env::consts::ARCH,
            "local_capabilities": LocalRuntimeCapabilities::current(),
            "permissions": perms,
            "settings_redacted": settings,
            "audio_devices": devices.into_iter().map(AudioDevicePayload::from).collect::<Vec<_>>(),
            "local_models": models,
            "recent_log_tail": log_tail,
        });
        let pretty = serde_json::to_string_pretty(&report)
            .map_err(|err| format!("Failed to serialize diagnostics: {err}"))?;
        let out_path = config_dir.join(format!("diagnostics-{stamp}.json"));
        write_text_atomic_restricted(&out_path, &pretty)
            .map_err(|err| format!("Failed to write diagnostics bundle: {err}"))?;

        let preview = if pretty.chars().count() > 2500 {
            let prefix: String = pretty.chars().take(2500).collect();
            format!("{prefix}\n… (truncated; see full file)")
        } else {
            pretty
        };
        Ok(DiagnosticsExportPayload {
            path: out_path.display().to_string(),
            preview,
        })
    })
    .await
    .map_err(|err| format!("Diagnostics export failed: {err}"))?
}

#[tauri::command]
pub async fn check_for_updates(
    state: State<'_, DesktopState>,
) -> Result<UpdateCheckResult, String> {
    Ok(check_latest_release(&state.http_client, env!("CARGO_PKG_VERSION")).await)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_dir(tag: &str) -> std::path::PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "pulsescribe-desktop-{tag}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn settings_payload_masks_api_keys_and_ignores_unset_templates() {
        let dir = temp_dir("settings");
        let store = PreferencesStore::new(&dir);
        store
            .save_api_key("DEEPGRAM_API_KEY", "dg_live_secret_12345678")
            .unwrap();
        store
            .save_env_setting("OPENAI_API_KEY", "${UNSET_VAR_PLACEHOLDER}")
            .unwrap();
        store.save_env_setting("PULSESCRIBE_MODE", "local").unwrap();

        let cfg = DaemonConfig::from_env_map(&BTreeMap::from([
            ("PULSESCRIBE_MODE".to_owned(), "local".to_owned()),
            (
                "DEEPGRAM_API_KEY".to_owned(),
                "dg_live_secret_12345678".to_owned(),
            ),
        ]));
        let payload = build_settings_payload(&store, &cfg);
        assert_eq!(payload.mode, "local");
        assert_eq!(
            payload.api_keys_configured.get("DEEPGRAM_API_KEY"),
            Some(&true)
        );
        assert_eq!(
            payload.api_keys_configured.get("OPENAI_API_KEY"),
            Some(&false)
        );
        assert_eq!(
            payload
                .api_keys_masked
                .get("DEEPGRAM_API_KEY")
                .map(String::as_str),
            Some("dg_…5678")
        );
        assert!(!payload
            .api_keys_masked
            .values()
            .any(|v| v.contains("secret")));

        let mut bad_patch = SaveSettingsInput::default();
        bad_patch
            .api_keys
            .insert("PATH".to_owned(), Some("/evil".to_owned()));
        assert!(build_env_updates(&bad_patch).is_err());

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn explicit_model_resolution_rejects_empty_and_unknown_ids() {
        assert!(resolve_explicit_model_spec("").is_err());
        assert!(resolve_explicit_model_spec("   ").is_err());
        assert!(resolve_explicit_model_spec("nonexistent-model").is_err());
        assert_eq!(resolve_explicit_model_spec("small").unwrap().id, "small");
    }

    #[test]
    fn diagnostics_redaction_strips_url_credentials_and_log_tokens() {
        assert_eq!(
            redact_url_for_diagnostics(
                "https://user:pass@proxy.example.com/v1?api_key=secret#frag"
            ),
            "https://REDACTED@proxy.example.com/v1?[REDACTED]"
        );
        assert_eq!(redact_url_for_diagnostics(""), "");
        assert_eq!(
            redact_log_text("INFO ok\nWARN Authorization: Bearer sk-123456\nINFO done"),
            "INFO ok\n[REDACTED_LOG_LINE]\nINFO done"
        );
    }

    #[test]
    fn history_store_delete_entry_handles_lossy_utf8_atomically() {
        let dir = temp_dir("history");
        let store = pulsescribe_core::HistoryStore::new(&dir);
        let e1 = TranscriptEntry::with_timestamp(
            "2026-11-22T10:00:00",
            "First",
            None,
            None,
            false,
            None,
        );
        let e2 = TranscriptEntry::with_timestamp(
            "2026-11-22T10:01:00",
            "Second",
            None,
            None,
            false,
            None,
        );
        store.save_entry(&e1).unwrap();
        store.save_entry(&e2).unwrap();

        assert!(store.delete_entry("2026-11-22T10:00:00", "First").unwrap());
        let remaining = store.get_recent_transcripts(10);
        assert_eq!(remaining.len(), 1);
        assert_eq!(remaining[0].text, "Second");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn hotkey_validation_detects_windows_conflicts_and_uses_macos_probe() {
        let conflict = validate_hotkey_for_platform(
            PlatformKind::Windows,
            HotkeyKind::Toggle,
            "ctrl+win+r",
            "ctrl+alt+r",
            "ctrl+win",
            false,
            true,
            |_| true,
        );
        assert_eq!(conflict.level, HotkeyValidationLevel::Error);

        let ok = validate_hotkey_for_platform(
            PlatformKind::Windows,
            HotkeyKind::Toggle,
            "ctrl+shift+space",
            "ctrl+alt+r",
            "ctrl+win",
            false,
            true,
            |_| true,
        );
        assert_eq!(ok.level, HotkeyValidationLevel::Ok);
        assert_eq!(ok.normalized, "ctrl+shift+space");

        let mac_reserved = validate_hotkey_for_platform(
            PlatformKind::MacOs,
            HotkeyKind::Toggle,
            "cmd+space",
            "f19",
            "fn",
            false,
            false,
            |_| false,
        );
        assert_eq!(mac_reserved.level, HotkeyValidationLevel::Error);
    }
}
