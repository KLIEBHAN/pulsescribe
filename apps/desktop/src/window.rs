//! Lazy WebView window lifecycle manager keeping idle memory at 0 WebViews.

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindowBuilder};

use crate::state::DesktopState;

/// Singleton window label for PulseScribe's on-demand Settings / Onboarding WebView.
pub const MAIN_WINDOW_LABEL: &str = "main";

/// Event name pushed to the lazy WebView when an already-open window should switch views.
pub const NAVIGATE_EVENT: &str = "pulsescribe://navigate";

/// Event name pushed to the lazy WebView for live daemon status/transcript/audio updates.
pub const DAEMON_EVENT: &str = "pulsescribe://daemon-event";

/// Event name pushed to the lazy WebView during local model downloads.
pub const MODEL_DOWNLOAD_PROGRESS_EVENT: &str = "pulsescribe://model-download-progress";

/// Event name pushed to the lazy WebView when a local model download finishes or fails.
pub const MODEL_DOWNLOAD_FINISHED_EVENT: &str = "pulsescribe://model-download-finished";

/// Open the lazy Settings/Onboarding WebView on `route`, or focus & navigate the existing window.
///
/// Closing the window lets Tauri destroy the WebView completely while `RunEvent::ExitRequested`
/// keeps the background daemon, tray icon, and native overlay running with 0 MB WebView overhead.
pub fn open_or_focus_window(app: &AppHandle, route: &str) -> Result<(), String> {
    let normalized = normalize_route(route);
    if let Some(state) = app.try_state::<DesktopState>() {
        state.set_pending_route(normalized);
    }

    if let Some(window) = app.get_webview_window(MAIN_WINDOW_LABEL) {
        let _ = window.emit(NAVIGATE_EVENT, normalized);
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
        return Ok(());
    }

    let window =
        WebviewWindowBuilder::new(app, MAIN_WINDOW_LABEL, WebviewUrl::App("index.html".into()))
            .title("PulseScribe")
            .inner_size(980.0, 680.0)
            .min_inner_size(760.0, 520.0)
            .center()
            .resizable(true)
            .visible(true)
            .build()
            .map_err(|err| format!("Failed to create PulseScribe window: {err}"))?;

    let _ = window.set_focus();
    Ok(())
}

/// Emit an event to the lazy WebView ONLY if it is currently open (zero work when idle).
pub fn emit_to_main_if_open<S: Serialize + Clone>(app: &AppHandle, event: &str, payload: &S) {
    if let Some(window) = app.get_webview_window(MAIN_WINDOW_LABEL) {
        let _ = window.emit(event, payload.clone());
    }
}

/// Normalize any requested route string to one of the known desktop UI sections.
pub fn normalize_route(route: &str) -> &'static str {
    match route.trim().to_ascii_lowercase().as_str() {
        "models" | "local" | "local-models" => "models",
        "prompts" => "prompts",
        "vocabulary" | "vocab" => "vocabulary",
        "history" | "transcripts" => "history",
        "onboarding" | "setup" | "welcome" => "onboarding",
        "diagnostics" | "updates" | "about" => "diagnostics",
        _ => "general",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn route_normalization_maps_aliases_and_defaults_to_general() {
        assert_eq!(normalize_route("models"), "models");
        assert_eq!(normalize_route("LOCAL-MODELS"), "models");
        assert_eq!(normalize_route("setup"), "onboarding");
        assert_eq!(normalize_route("transcripts"), "history");
        assert_eq!(normalize_route("unknown-tab"), "general");
    }
}
