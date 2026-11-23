//! Native OS system tray / macOS menu bar icon and status menu controller.

use std::sync::Mutex;

use pulsescribe_core::daemon_status_feedback::{
    build_daemon_status_hint, build_daemon_status_label, build_daemon_tray_title, StatusDetail,
};
use pulsescribe_core::transcript_delivery::{
    COPIED, COPY_FAILED, PASTE_FAILED_MACOS, PASTE_FAILED_OTHER, TEST_COMPLETE,
};
use pulsescribe_core::{AppState, DaemonErrorCode, DaemonStatusError, PreferencesStore};
use serde_json::Value;
use tauri::image::Image;
use tauri::menu::{Menu, MenuBuilder, MenuItem, PredefinedMenuItem};
#[cfg(not(target_os = "macos"))]
use tauri::tray::{MouseButton, MouseButtonState, TrayIconEvent};
use tauri::tray::{TrayIcon, TrayIconBuilder};
use tauri::{AppHandle, Manager, Wry};

use crate::commands::open_directory_in_os;
use crate::state::DesktopState;
use crate::window::open_or_focus_window;

const TRAY_ID: &str = "pulsescribe-tray";
const MENU_STATUS_ID: &str = "tray_status";
const MENU_HINT_ID: &str = "tray_hint";
const MENU_TOGGLE_ID: &str = "tray_toggle";
const MENU_COPY_LAST_ID: &str = "tray_copy_last";
const MENU_SETTINGS_ID: &str = "tray_settings";
const MENU_MODELS_ID: &str = "tray_models";
const MENU_HISTORY_ID: &str = "tray_history";
const MENU_ONBOARDING_ID: &str = "tray_onboarding";
const MENU_UPDATES_ID: &str = "tray_updates";
const MENU_OPEN_CONFIG_ID: &str = "tray_open_config";
const MENU_QUIT_ID: &str = "tray_quit";

const TRAY_ICON_PNG: &[u8] = include_bytes!("../../../assets/icon.iconset/icon_32x32.png");

/// Resolved desktop interface language (`en` or `de`).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum UiLanguage {
    #[default]
    En,
    De,
}

impl UiLanguage {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::En => "en",
            Self::De => "de",
        }
    }
}

/// Normalize a persisted or user-selected UI language preference (`"system"`, `"en"`, or `"de"`).
pub fn normalize_ui_language_pref(raw: &str) -> &'static str {
    let lower = raw.trim().to_ascii_lowercase();
    if lower == "de"
        || lower.starts_with("de-")
        || lower.starts_with("de_")
        || lower == "german"
        || lower == "deutsch"
    {
        "de"
    } else if lower == "en"
        || lower.starts_with("en-")
        || lower.starts_with("en_")
        || lower == "english"
    {
        "en"
    } else {
        "system"
    }
}

fn detect_env_ui_language() -> UiLanguage {
    for key in ["LC_ALL", "LC_MESSAGES", "LANG"] {
        if let Ok(val) = std::env::var(key) {
            let trimmed = val.trim().to_ascii_lowercase();
            if trimmed.is_empty() || trimmed == "c" || trimmed == "posix" {
                continue;
            }
            if trimmed.starts_with("de") {
                return UiLanguage::De;
            }
            return UiLanguage::En;
        }
    }
    UiLanguage::En
}

/// Resolve `"system" | "en" | "de"` into a concrete [`UiLanguage`], honoring an optional
/// WebView-detected system language hint (`"de"` or `"en"`) when set to `"system"`.
pub fn resolve_ui_language(pref: &str, resolved_hint: Option<&str>) -> UiLanguage {
    match normalize_ui_language_pref(pref) {
        "de" => UiLanguage::De,
        "en" => UiLanguage::En,
        _ => match resolved_hint.map(normalize_ui_language_pref) {
            Some("de") => UiLanguage::De,
            Some("en") => UiLanguage::En,
            _ => detect_env_ui_language(),
        },
    }
}

/// Read persisted `"ui_language"` (plus cached `"ui_language_resolved"` for `"system"`) from
/// `~/.pulsescribe/preferences.json`.
pub fn load_ui_language_pref(store: &PreferencesStore) -> (&'static str, UiLanguage) {
    let prefs = store.load_preferences();
    let pref = prefs
        .get("ui_language")
        .and_then(Value::as_str)
        .map(normalize_ui_language_pref)
        .unwrap_or("system");
    let hint = prefs.get("ui_language_resolved").and_then(Value::as_str);
    (pref, resolve_ui_language(pref, hint))
}

struct TrayCopy {
    copy_last: &'static str,
    settings: &'static str,
    models: &'static str,
    history: &'static str,
    onboarding: &'static str,
    updates: &'static str,
    open_config: &'static str,
    quit: &'static str,
}

const fn tray_copy(lang: UiLanguage) -> TrayCopy {
    match lang {
        UiLanguage::En => TrayCopy {
            copy_last: "Copy Last Transcript",
            settings: "Settings…",
            models: "Local Models…",
            history: "History",
            onboarding: "Setup Wizard…",
            updates: "Check for Updates…",
            open_config: "Open Config Folder",
            quit: "Quit PulseScribe",
        },
        UiLanguage::De => TrayCopy {
            copy_last: "Letztes Transkript kopieren",
            settings: "Einstellungen…",
            models: "Lokale Modelle…",
            history: "Verlauf",
            onboarding: "Einrichtungsassistent…",
            updates: "Nach Updates suchen…",
            open_config: "Konfigurationsordner öffnen",
            quit: "PulseScribe beenden",
        },
    }
}

struct CachedTrayState {
    lang: UiLanguage,
    state: AppState,
    detail: Option<String>,
    error: Option<DaemonStatusError>,
    status: String,
    hint: String,
    toggle: String,
    tooltip: String,
}

/// Thread-safe controller updating native tray menu labels only when they actually change.
pub struct TrayController {
    tray: TrayIcon<Wry>,
    status_item: MenuItem<Wry>,
    hint_item: MenuItem<Wry>,
    toggle_item: MenuItem<Wry>,
    copy_last_item: MenuItem<Wry>,
    settings_item: MenuItem<Wry>,
    models_item: MenuItem<Wry>,
    history_item: MenuItem<Wry>,
    onboarding_item: MenuItem<Wry>,
    updates_item: MenuItem<Wry>,
    open_config_item: MenuItem<Wry>,
    quit_item: MenuItem<Wry>,
    cached: Mutex<CachedTrayState>,
}

impl TrayController {
    /// Build and register PulseScribe's native tray icon and context menu.
    pub fn build(app: &AppHandle, initial_lang: UiLanguage) -> Result<Self, String> {
        let copy = tray_copy(initial_lang);
        let initial_status = format!(
            "Status: {}",
            localized_status_label(AppState::Idle, None, None, initial_lang)
        );
        let initial_hint = localized_status_hint(AppState::Idle, None, None, initial_lang);
        let initial_toggle = toggle_action_label_for(AppState::Idle, initial_lang).to_owned();
        let initial_tooltip = format!(
            "PulseScribe — {}",
            localized_status_label(AppState::Idle, None, None, initial_lang)
        );

        let status_item =
            MenuItem::with_id(app, MENU_STATUS_ID, &initial_status, false, None::<&str>)
                .map_err(|e| format!("tray status menu item failed: {e}"))?;
        let hint_item = MenuItem::with_id(app, MENU_HINT_ID, &initial_hint, false, None::<&str>)
            .map_err(|e| format!("tray hint menu item failed: {e}"))?;
        let toggle_item =
            MenuItem::with_id(app, MENU_TOGGLE_ID, &initial_toggle, true, None::<&str>)
                .map_err(|e| format!("tray toggle menu item failed: {e}"))?;
        let copy_last_item =
            MenuItem::with_id(app, MENU_COPY_LAST_ID, copy.copy_last, true, None::<&str>)
                .map_err(|e| format!("tray copy menu item failed: {e}"))?;
        let settings_item =
            MenuItem::with_id(app, MENU_SETTINGS_ID, copy.settings, true, None::<&str>)
                .map_err(|e| format!("tray settings menu item failed: {e}"))?;
        let models_item = MenuItem::with_id(app, MENU_MODELS_ID, copy.models, true, None::<&str>)
            .map_err(|e| format!("tray models menu item failed: {e}"))?;
        let history_item =
            MenuItem::with_id(app, MENU_HISTORY_ID, copy.history, true, None::<&str>)
                .map_err(|e| format!("tray history menu item failed: {e}"))?;
        let onboarding_item =
            MenuItem::with_id(app, MENU_ONBOARDING_ID, copy.onboarding, true, None::<&str>)
                .map_err(|e| format!("tray onboarding menu item failed: {e}"))?;
        let updates_item =
            MenuItem::with_id(app, MENU_UPDATES_ID, copy.updates, true, None::<&str>)
                .map_err(|e| format!("tray updates menu item failed: {e}"))?;
        let open_config_item = MenuItem::with_id(
            app,
            MENU_OPEN_CONFIG_ID,
            copy.open_config,
            true,
            None::<&str>,
        )
        .map_err(|e| format!("tray open config menu item failed: {e}"))?;
        let quit_item = MenuItem::with_id(app, MENU_QUIT_ID, copy.quit, true, None::<&str>)
            .map_err(|e| format!("tray quit menu item failed: {e}"))?;

        let sep1 = PredefinedMenuItem::separator(app).map_err(|e| e.to_string())?;
        let sep2 = PredefinedMenuItem::separator(app).map_err(|e| e.to_string())?;
        let sep3 = PredefinedMenuItem::separator(app).map_err(|e| e.to_string())?;
        let sep4 = PredefinedMenuItem::separator(app).map_err(|e| e.to_string())?;

        let menu: Menu<Wry> = MenuBuilder::new(app)
            .items(&[
                &status_item,
                &hint_item,
                &sep1,
                &toggle_item,
                &copy_last_item,
                &sep2,
                &settings_item,
                &models_item,
                &history_item,
                &onboarding_item,
                &sep3,
                &updates_item,
                &open_config_item,
                &sep4,
                &quit_item,
            ])
            .build()
            .map_err(|e| format!("tray menu build failed: {e}"))?;

        let icon = Image::from_bytes(TRAY_ICON_PNG)
            .map_err(|e| format!("failed to decode tray icon PNG: {e}"))?;

        let tray = TrayIconBuilder::with_id(TRAY_ID)
            .icon(icon)
            // Keep colored PNG intact instead of turning the filled rounded square into a solid blob.
            .icon_as_template(false)
            .tooltip(&initial_tooltip)
            .menu(&menu)
            .show_menu_on_left_click(cfg!(target_os = "macos"))
            .on_menu_event(|app, event| handle_tray_menu_event(app, event.id().as_ref()))
            .on_tray_icon_event(|tray, event| {
                #[cfg(not(target_os = "macos"))]
                if let TrayIconEvent::Click {
                    button: MouseButton::Left,
                    button_state: MouseButtonState::Up,
                    ..
                } = event
                {
                    let _ = open_or_focus_window(tray.app_handle(), "general");
                }
                #[cfg(target_os = "macos")]
                let _ = (tray, event);
            })
            .build(app)
            .map_err(|e| format!("TrayIconBuilder::build failed: {e}"))?;

        Ok(Self {
            tray,
            status_item,
            hint_item,
            toggle_item,
            copy_last_item,
            settings_item,
            models_item,
            history_item,
            onboarding_item,
            updates_item,
            open_config_item,
            quit_item,
            cached: Mutex::new(CachedTrayState {
                lang: initial_lang,
                state: AppState::Idle,
                detail: None,
                error: None,
                status: initial_status,
                hint: initial_hint,
                toggle: initial_toggle,
                tooltip: initial_tooltip,
            }),
        })
    }

    /// Update native tray menu labels immediately when the user changes the interface language.
    pub fn set_language(&self, lang: UiLanguage) {
        let mut cached = self.cached.lock().unwrap_or_else(|e| e.into_inner());
        if cached.lang == lang {
            return;
        }
        cached.lang = lang;
        let copy = tray_copy(lang);
        let _ = self.copy_last_item.set_text(copy.copy_last);
        let _ = self.settings_item.set_text(copy.settings);
        let _ = self.models_item.set_text(copy.models);
        let _ = self.history_item.set_text(copy.history);
        let _ = self.onboarding_item.set_text(copy.onboarding);
        let _ = self.updates_item.set_text(copy.updates);
        let _ = self.open_config_item.set_text(copy.open_config);
        let _ = self.quit_item.set_text(copy.quit);

        let state = cached.state;
        let detail = cached.detail.clone();
        let error = cached.error.clone();
        self.apply_status_locked(&mut cached, state, detail.as_deref(), error.as_ref());
    }

    /// Update native tray tooltip and menu labels only when they differ from the last applied text.
    pub fn update_status(
        &self,
        state: AppState,
        detail: Option<&str>,
        error: Option<&DaemonStatusError>,
    ) {
        let mut cached = self.cached.lock().unwrap_or_else(|e| e.into_inner());
        cached.state = state;
        cached.detail = detail.map(ToOwned::to_owned);
        cached.error = error.cloned();
        self.apply_status_locked(&mut cached, state, detail, error);
    }

    fn apply_status_locked(
        &self,
        cached: &mut CachedTrayState,
        state: AppState,
        detail: Option<&str>,
        error: Option<&DaemonStatusError>,
    ) {
        let lang = cached.lang;
        let label = localized_status_label(state, detail, error, lang);
        let status_text = format!("Status: {label}");
        let hint_text = localized_status_hint(state, detail, error, lang);
        let toggle_text = toggle_action_label_for(state, lang).to_owned();
        let tooltip_text = match lang {
            UiLanguage::En => {
                let status_detail = match (error, detail) {
                    (Some(err), _) => StatusDetail::Error(err),
                    (None, Some(text)) => StatusDetail::Text(text),
                    (None, None) => StatusDetail::None,
                };
                build_daemon_tray_title(state, status_detail, "PulseScribe", 120)
            }
            UiLanguage::De => format!("PulseScribe — {label}"),
        };

        if cached.status != status_text {
            let _ = self.status_item.set_text(&status_text);
            cached.status = status_text;
        }
        if cached.hint != hint_text {
            let _ = self.hint_item.set_text(&hint_text);
            cached.hint = hint_text;
        }
        if cached.toggle != toggle_text {
            let _ = self.toggle_item.set_text(&toggle_text);
            cached.toggle = toggle_text;
        }
        if cached.tooltip != tooltip_text {
            let _ = self.tray.set_tooltip(Some(&tooltip_text));
            cached.tooltip = tooltip_text;
        }
    }
}

pub fn toggle_action_label(state: AppState) -> &'static str {
    toggle_action_label_for(state, UiLanguage::En)
}

pub fn toggle_action_label_for(state: AppState, lang: UiLanguage) -> &'static str {
    match (lang, state) {
        (UiLanguage::En, AppState::Listening | AppState::Recording) => "Stop Dictation",
        (UiLanguage::En, AppState::Transcribing | AppState::Refining) => "Cancel Dictation",
        (UiLanguage::En, _) => "Start Dictation",
        (UiLanguage::De, AppState::Listening | AppState::Recording) => "Diktat stoppen",
        (UiLanguage::De, AppState::Transcribing | AppState::Refining) => "Diktat abbrechen",
        (UiLanguage::De, _) => "Diktat starten",
    }
}

fn de_delivery_status_label(state: AppState, detail: Option<&str>) -> Option<&'static str> {
    let raw = detail?;
    match state {
        AppState::Done => {
            if raw.starts_with(COPIED) {
                Some("In Zwischenablage kopiert")
            } else if raw.starts_with(TEST_COMPLETE) {
                Some("Test abgeschlossen")
            } else {
                None
            }
        }
        AppState::Error => {
            if raw == PASTE_FAILED_MACOS {
                Some("Kopiert — ⌘V drücken")
            } else if raw == PASTE_FAILED_OTHER {
                Some("Kopiert — Strg+V drücken")
            } else if raw == COPY_FAILED {
                Some("Kopieren fehlgeschlagen — Letztes Transkript kopieren")
            } else {
                None
            }
        }
        _ => None,
    }
}

fn de_delivery_status_hint(state: AppState, detail: Option<&str>) -> Option<&'static str> {
    if state != AppState::Error {
        return None;
    }
    let raw = detail?;
    if raw == PASTE_FAILED_MACOS || raw == PASTE_FAILED_OTHER {
        Some("Manuell einfügen oder „Letztes Transkript kopieren“ wählen.")
    } else if raw == COPY_FAILED {
        Some("Im Menü „Letztes Transkript kopieren“ wählen, um es erneut zu versuchen.")
    } else {
        None
    }
}

fn localized_status_label(
    state: AppState,
    detail: Option<&str>,
    error: Option<&DaemonStatusError>,
    lang: UiLanguage,
) -> String {
    let status_detail = match (error, detail) {
        (Some(err), _) => StatusDetail::Error(err),
        (None, Some(text)) => StatusDetail::Text(text),
        (None, None) => StatusDetail::None,
    };
    if lang == UiLanguage::En {
        return build_daemon_status_label(state, status_detail, false, 64);
    }

    let effective_detail = detail
        .or_else(|| error.and_then(|err| err.detail.as_deref()))
        .map(str::trim)
        .filter(|s| !s.is_empty());

    if let Some(delivery_label) = de_delivery_status_label(state, effective_detail) {
        return delivery_label.to_owned();
    }

    if let Some(err) = error {
        let mapped = match err.code {
            DaemonErrorCode::MissingApiKey => Some("API-Schlüssel fehlt"),
            DaemonErrorCode::Busy => Some("PulseScribe ist beschäftigt"),
            DaemonErrorCode::Timeout => Some("Zeitüberschreitung bei Transkription"),
            DaemonErrorCode::InvalidProvider => Some("Ungültige Anbieter-Einstellung"),
            DaemonErrorCode::InputMonitoring => Some("Eingabeüberwachung erforderlich"),
            DaemonErrorCode::AccessibilityPermission => Some("Bedienungshilfen erforderlich"),
            DaemonErrorCode::MicrophonePermission => Some("Mikrofonzugriff erforderlich"),
            DaemonErrorCode::MicrophoneUnavailable => Some("Mikrofon nicht verfügbar"),
            DaemonErrorCode::Connection => Some("Transkriptionsdienst nicht erreichbar"),
            DaemonErrorCode::MissingDependency => Some("Fehlende Abhängigkeit"),
            DaemonErrorCode::Unknown => None,
        };
        if let Some(text) = mapped {
            return text.to_owned();
        }
    }

    if let Some(raw) = effective_detail {
        if let Some(rest) = raw.strip_prefix("Downloading ") {
            return format!("Lade {rest}");
        }
        if let Some(rest) = raw.strip_prefix("Loading ") {
            return format!("Lade {rest}");
        }
    }

    match state {
        AppState::Idle => "Bereit zum Diktieren",
        AppState::Loading => "Transkription wird vorbereitet",
        AppState::Listening => "Höre zu",
        AppState::Recording => "Aufnahme läuft — jetzt sprechen",
        AppState::Transcribing => "Transkribiere",
        AppState::Refining => "Bearbeite Text",
        AppState::Done => "Transkript eingefügt",
        AppState::NoSpeech => "Keine Sprache erkannt",
        AppState::Error => "Etwas ist schiefgelaufen",
    }
    .to_owned()
}

fn localized_status_hint(
    state: AppState,
    detail: Option<&str>,
    error: Option<&DaemonStatusError>,
    lang: UiLanguage,
) -> String {
    let status_detail = match (error, detail) {
        (Some(err), _) => StatusDetail::Error(err),
        (None, Some(text)) => StatusDetail::Text(text),
        (None, None) => StatusDetail::None,
    };
    if lang == UiLanguage::En {
        return build_daemon_status_hint(state, status_detail, 76);
    }

    let effective_detail = detail
        .or_else(|| error.and_then(|err| err.detail.as_deref()))
        .map(str::trim)
        .filter(|s| !s.is_empty());

    if let Some(delivery_hint) = de_delivery_status_hint(state, effective_detail) {
        return delivery_hint.to_owned();
    }

    if let Some(err) = error {
        let mapped = match err.code {
            DaemonErrorCode::MissingApiKey => {
                Some("API-Schlüssel in den Einstellungen hinterlegen und erneut versuchen.")
            }
            DaemonErrorCode::Busy => {
                Some("Kurz warten, bis der laufende Vorgang abgeschlossen ist.")
            }
            DaemonErrorCode::Timeout => {
                Some("PulseScribe ist gleich wieder bereit. Bei Wiederholung Diagnose prüfen.")
            }
            DaemonErrorCode::InvalidProvider => {
                Some("Unterstützten Anbieter in den Einstellungen auswählen.")
            }
            DaemonErrorCode::InputMonitoring | DaemonErrorCode::AccessibilityPermission => {
                Some("Fehlende Berechtigung im Einrichtungsassistenten erteilen.")
            }
            DaemonErrorCode::MicrophonePermission => {
                Some("Mikrofonzugriff in den Systemeinstellungen erlauben.")
            }
            DaemonErrorCode::MicrophoneUnavailable => {
                Some("Mikrofonzugriff und gewähltes Eingabegerät prüfen.")
            }
            DaemonErrorCode::Connection => {
                Some("Internetverbindung oder Anbieterstatus prüfen und erneut versuchen.")
            }
            DaemonErrorCode::MissingDependency => Some(
                "Fehlende Komponente installieren oder Anbieter in den Einstellungen wechseln.",
            ),
            DaemonErrorCode::Unknown => None,
        };
        if let Some(text) = mapped {
            return text.to_owned();
        }
    }

    match state {
        AppState::Idle => "Tastenkürzel nutzen, um ein Diktat zu starten.",
        AppState::Loading => "Erster Start oder Anbieterwechsel kann einen Moment dauern.",
        AppState::Listening => "Jetzt sprechen oder Halten-Taste zum Abbrechen loslassen.",
        AppState::Recording => "Taste loslassen oder Umschalten-Kürzel zum Beenden drücken.",
        AppState::Transcribing => "PulseScribe wandelt deine Sprache in Text um.",
        AppState::Refining => "PulseScribe optimiert Zeichensetzung und Formatierung.",
        AppState::Done => "Bereit für das nächste Diktat.",
        AppState::NoSpeech => "Erneut versuchen und nach dem Signal kurz sprechen.",
        AppState::Error => "Erneut versuchen oder Diagnose in den Einstellungen öffnen.",
    }
    .to_owned()
}

fn handle_tray_menu_event(app: &AppHandle, id: &str) {
    let Some(state) = app.try_state::<DesktopState>() else {
        return;
    };
    match id {
        MENU_TOGGLE_ID => match state.daemon.state() {
            AppState::Transcribing | AppState::Refining => state.daemon.cancel(),
            _ => state.daemon.toggle_recording(),
        },
        MENU_COPY_LAST_ID => {
            let daemon = state.daemon.clone();
            tauri::async_runtime::spawn(async move {
                let _ = daemon.copy_last_transcript().await;
            });
        }
        MENU_SETTINGS_ID => {
            let _ = open_or_focus_window(app, "general");
        }
        MENU_MODELS_ID => {
            let _ = open_or_focus_window(app, "models");
        }
        MENU_HISTORY_ID => {
            let _ = open_or_focus_window(app, "history");
        }
        MENU_ONBOARDING_ID => {
            let _ = open_or_focus_window(app, "onboarding");
        }
        MENU_UPDATES_ID => {
            let _ = open_or_focus_window(app, "diagnostics");
        }
        MENU_OPEN_CONFIG_ID => {
            let _ = open_directory_in_os(&state.config_dir);
        }
        MENU_QUIT_ID => {
            state.set_quitting();
            state.cancel_all_downloads();
            app.exit(0);
        }
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn toggle_label_tracks_active_states() {
        assert_eq!(toggle_action_label(AppState::Idle), "Start Dictation");
        assert_eq!(toggle_action_label(AppState::Listening), "Stop Dictation");
        assert_eq!(toggle_action_label(AppState::Recording), "Stop Dictation");
        assert_eq!(
            toggle_action_label(AppState::Transcribing),
            "Cancel Dictation"
        );
        assert_eq!(toggle_action_label(AppState::Refining), "Cancel Dictation");
        assert_eq!(
            toggle_action_label_for(AppState::Idle, UiLanguage::De),
            "Diktat starten"
        );
        assert_eq!(
            toggle_action_label_for(AppState::Recording, UiLanguage::De),
            "Diktat stoppen"
        );
        assert_eq!(
            toggle_action_label_for(AppState::Transcribing, UiLanguage::De),
            "Diktat abbrechen"
        );
    }

    #[test]
    fn ui_language_normalization_and_resolution_handle_locales_and_fallbacks() {
        assert_eq!(normalize_ui_language_pref("de"), "de");
        assert_eq!(normalize_ui_language_pref("de-DE"), "de");
        assert_eq!(normalize_ui_language_pref("Deutsch"), "de");
        assert_eq!(normalize_ui_language_pref("en-US"), "en");
        assert_eq!(normalize_ui_language_pref("system"), "system");
        assert_eq!(normalize_ui_language_pref("fr"), "system");

        assert_eq!(resolve_ui_language("de", Some("en")), UiLanguage::De);
        assert_eq!(resolve_ui_language("en", Some("de")), UiLanguage::En);
        assert_eq!(resolve_ui_language("system", Some("de")), UiLanguage::De);
        assert_eq!(resolve_ui_language("system", Some("en")), UiLanguage::En);

        assert_eq!(
            localized_status_label(
                AppState::Loading,
                Some("Downloading canary-1b-v2 (1/3, 42%)"),
                None,
                UiLanguage::De
            ),
            "Lade canary-1b-v2 (1/3, 42%)"
        );
    }

    #[test]
    fn german_status_preserves_transcript_delivery_outcomes_and_hints() {
        assert_eq!(
            localized_status_label(
                AppState::Done,
                Some("Copied to clipboard · 1.2 s"),
                None,
                UiLanguage::De
            ),
            "In Zwischenablage kopiert"
        );
        assert_eq!(
            localized_status_label(AppState::Done, Some(TEST_COMPLETE), None, UiLanguage::De),
            "Test abgeschlossen"
        );
        assert_eq!(
            localized_status_label(
                AppState::Done,
                Some("Transcript pasted into Mail"),
                None,
                UiLanguage::De
            ),
            "Transkript eingefügt"
        );
        assert_eq!(
            localized_status_label(
                AppState::Error,
                Some(PASTE_FAILED_MACOS),
                None,
                UiLanguage::De
            ),
            "Kopiert — ⌘V drücken"
        );
        assert_eq!(
            localized_status_hint(
                AppState::Error,
                Some(PASTE_FAILED_MACOS),
                None,
                UiLanguage::De
            ),
            "Manuell einfügen oder „Letztes Transkript kopieren“ wählen."
        );
        assert_eq!(
            localized_status_label(
                AppState::Error,
                Some(PASTE_FAILED_OTHER),
                None,
                UiLanguage::De
            ),
            "Kopiert — Strg+V drücken"
        );
        assert_eq!(
            localized_status_label(AppState::Error, Some(COPY_FAILED), None, UiLanguage::De),
            "Kopieren fehlgeschlagen — Letztes Transkript kopieren"
        );
        assert_eq!(
            localized_status_hint(AppState::Error, Some(COPY_FAILED), None, UiLanguage::De),
            "Im Menü „Letztes Transkript kopieren“ wählen, um es erneut zu versuchen."
        );
        let wrapped_err = DaemonStatusError::new(
            DaemonErrorCode::Unknown,
            Some(PASTE_FAILED_MACOS.to_owned()),
        );
        assert_eq!(
            localized_status_label(AppState::Error, None, Some(&wrapped_err), UiLanguage::De),
            "Kopiert — ⌘V drücken"
        );
        assert_eq!(
            localized_status_hint(AppState::Error, None, Some(&wrapped_err), UiLanguage::De),
            "Manuell einfügen oder „Letztes Transkript kopieren“ wählen."
        );
    }
}
