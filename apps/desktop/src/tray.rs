//! Native OS system tray / macOS menu bar icon and status menu controller.

use std::sync::Mutex;

use pulsescribe_core::daemon_status_feedback::{
    build_daemon_status_hint, build_daemon_status_label, build_daemon_tray_title, StatusDetail,
};
use pulsescribe_core::{AppState, DaemonStatusError};
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

#[derive(Default)]
struct CachedTrayLabels {
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
    cached: Mutex<CachedTrayLabels>,
}

impl TrayController {
    /// Build and register PulseScribe's native tray icon and context menu.
    pub fn build(app: &AppHandle) -> Result<Self, String> {
        let initial_status = "Status: Ready to dictate";
        let initial_hint = "Use your hotkey to start dictation.";
        let initial_toggle = "Start Dictation";
        let initial_tooltip = "PulseScribe — Ready to dictate";

        let status_item =
            MenuItem::with_id(app, MENU_STATUS_ID, initial_status, false, None::<&str>)
                .map_err(|e| format!("tray status menu item failed: {e}"))?;
        let hint_item = MenuItem::with_id(app, MENU_HINT_ID, initial_hint, false, None::<&str>)
            .map_err(|e| format!("tray hint menu item failed: {e}"))?;
        let toggle_item =
            MenuItem::with_id(app, MENU_TOGGLE_ID, initial_toggle, true, None::<&str>)
                .map_err(|e| format!("tray toggle menu item failed: {e}"))?;
        let copy_last = MenuItem::with_id(
            app,
            MENU_COPY_LAST_ID,
            "Copy Last Transcript",
            true,
            None::<&str>,
        )
        .map_err(|e| format!("tray copy menu item failed: {e}"))?;
        let settings = MenuItem::with_id(app, MENU_SETTINGS_ID, "Settings…", true, None::<&str>)
            .map_err(|e| format!("tray settings menu item failed: {e}"))?;
        let models = MenuItem::with_id(app, MENU_MODELS_ID, "Local Models…", true, None::<&str>)
            .map_err(|e| format!("tray models menu item failed: {e}"))?;
        let history = MenuItem::with_id(app, MENU_HISTORY_ID, "History", true, None::<&str>)
            .map_err(|e| format!("tray history menu item failed: {e}"))?;
        let onboarding =
            MenuItem::with_id(app, MENU_ONBOARDING_ID, "Setup Wizard…", true, None::<&str>)
                .map_err(|e| format!("tray onboarding menu item failed: {e}"))?;
        let updates = MenuItem::with_id(
            app,
            MENU_UPDATES_ID,
            "Check for Updates…",
            true,
            None::<&str>,
        )
        .map_err(|e| format!("tray updates menu item failed: {e}"))?;
        let open_config = MenuItem::with_id(
            app,
            MENU_OPEN_CONFIG_ID,
            "Open Config Folder",
            true,
            None::<&str>,
        )
        .map_err(|e| format!("tray open config menu item failed: {e}"))?;
        let quit = MenuItem::with_id(app, MENU_QUIT_ID, "Quit PulseScribe", true, None::<&str>)
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
                &copy_last,
                &sep2,
                &settings,
                &models,
                &history,
                &onboarding,
                &sep3,
                &updates,
                &open_config,
                &sep4,
                &quit,
            ])
            .build()
            .map_err(|e| format!("tray menu build failed: {e}"))?;

        let icon = Image::from_bytes(TRAY_ICON_PNG)
            .map_err(|e| format!("failed to decode tray icon PNG: {e}"))?;

        let tray = TrayIconBuilder::with_id(TRAY_ID)
            .icon(icon)
            // Keep colored PNG intact instead of turning the filled rounded square into a solid blob.
            .icon_as_template(false)
            .tooltip(initial_tooltip)
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
            cached: Mutex::new(CachedTrayLabels {
                status: initial_status.to_owned(),
                hint: initial_hint.to_owned(),
                toggle: initial_toggle.to_owned(),
                tooltip: initial_tooltip.to_owned(),
            }),
        })
    }

    /// Update native tray tooltip and menu labels only when they differ from the last applied text.
    pub fn update_status(
        &self,
        state: AppState,
        detail: Option<&str>,
        error: Option<&DaemonStatusError>,
    ) {
        let status_detail = match (error, detail) {
            (Some(err), _) => StatusDetail::Error(err),
            (None, Some(text)) => StatusDetail::Text(text),
            (None, None) => StatusDetail::None,
        };
        let status_text = format!(
            "Status: {}",
            build_daemon_status_label(state, status_detail, false, 64)
        );
        let hint_text = build_daemon_status_hint(state, status_detail, 76);
        let toggle_text = toggle_action_label(state).to_owned();
        let tooltip_text = build_daemon_tray_title(state, status_detail, "PulseScribe", 120);

        let mut cached = self.cached.lock().unwrap_or_else(|e| e.into_inner());
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
    match state {
        AppState::Listening | AppState::Recording => "Stop Dictation",
        AppState::Transcribing | AppState::Refining => "Cancel Dictation",
        _ => "Start Dictation",
    }
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
    }
}
