//! PulseScribe native Tauri v2 desktop shell wiring `DaemonRuntime`, native 60 FPS overlay,
//! native system tray, lazy Settings/Onboarding WebView, and typed IPC commands.

pub mod commands;
pub mod logging;
pub mod state;
pub mod tray;
pub mod update_install;
pub mod updates;
pub mod window;

use std::path::PathBuf;
use std::sync::Arc;

use pulsescribe_core::paths::AppPaths;
use pulsescribe_core::PreferencesStore;
use pulsescribe_daemon::{
    DaemonConfig, DaemonEvent, DaemonHandle, DaemonPlatform, DaemonRuntime, DaemonRuntimeOptions,
    LiveDictationBackend, LocalPreloadObserver,
};
use pulsescribe_local_asr::{DownloadProgress, LocalAsrManager, ModelDownloader};
use pulsescribe_overlay::{OverlayActionEvent, OverlayCommand, OverlayHandle};
use pulsescribe_platform::{SingleInstanceGuard, SingleInstanceOutcome};
use reqwest::Client;
use tauri::{AppHandle, Manager, RunEvent};

use crate::state::DesktopState;
use crate::tray::{load_ui_language_pref, TrayController};
use crate::window::{
    emit_to_main_if_open, open_or_focus_window, DAEMON_EVENT, MODEL_DOWNLOAD_FINISHED_EVENT,
    MODEL_DOWNLOAD_PROGRESS_EVENT,
};

struct DesktopPreloadObserver {
    daemon: DaemonHandle,
    app: AppHandle,
}

impl LocalPreloadObserver for DesktopPreloadObserver {
    fn on_loading_status(&self, detail: Option<String>) {
        match detail {
            Some(text) => self.daemon.set_loading(Some(text)),
            None => self.daemon.finish_loading(),
        }
    }

    fn on_download_progress(&self, progress: DownloadProgress) {
        emit_to_main_if_open(&self.app, MODEL_DOWNLOAD_PROGRESS_EVENT, &progress);
    }

    fn on_download_finished(&self, model_id: &str, result: Result<(), &str>) {
        let payload = match result {
            Ok(()) => serde_json::json!({
                "model_id": model_id,
                "ok": true,
                "error": serde_json::Value::Null
            }),
            Err("") => serde_json::json!({
                "model_id": model_id,
                "ok": false,
                "error": serde_json::Value::Null
            }),
            Err(err) => serde_json::json!({
                "model_id": model_id,
                "ok": false,
                "error": err
            }),
        };
        emit_to_main_if_open(&self.app, MODEL_DOWNLOAD_FINISHED_EVENT, &payload);
    }
}

/// Platform-native overlay owner kept alive for the lifetime of the desktop event loop.
enum NativeOverlayOwner {
    #[cfg(target_os = "macos")]
    MacOs(#[allow(dead_code)] pulsescribe_overlay::macos::MacOsOverlay),
    #[cfg(target_os = "windows")]
    Windows(#[allow(dead_code)] pulsescribe_overlay::windows::WindowsOverlay),
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    None,
}

fn spawn_native_overlay(app_handle: AppHandle) -> (Option<NativeOverlayOwner>, OverlayHandle) {
    let on_action = move |event: OverlayActionEvent| {
        if matches!(event, OverlayActionEvent::OpenSettings) {
            let handle = app_handle.clone();
            let _ = app_handle.run_on_main_thread(move || {
                let _ = open_or_focus_window(&handle, "general");
            });
        }
    };

    #[cfg(target_os = "macos")]
    {
        match objc2_foundation::MainThreadMarker::new() {
            Some(mtm) => match pulsescribe_overlay::macos::MacOsOverlay::new(mtm, on_action) {
                Ok((overlay, handle)) => (Some(NativeOverlayOwner::MacOs(overlay)), handle),
                Err(err) => {
                    tracing::warn!(error = %err, "macOS overlay unavailable; falling back to noop");
                    (None, OverlayHandle::noop())
                }
            },
            None => {
                tracing::warn!("macOS overlay requested off main thread; falling back to noop");
                (None, OverlayHandle::noop())
            }
        }
    }

    #[cfg(target_os = "windows")]
    {
        match pulsescribe_overlay::windows::WindowsOverlay::spawn(on_action) {
            Ok((overlay, handle)) => (Some(NativeOverlayOwner::Windows(overlay)), handle),
            Err(err) => {
                tracing::warn!(error = %err, "Windows overlay unavailable; falling back to noop");
                (None, OverlayHandle::noop())
            }
        }
    }

    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = on_action;
        (Some(NativeOverlayOwner::None), OverlayHandle::noop())
    }
}

fn resolve_config_dir() -> Result<PathBuf, String> {
    if let Ok(explicit) = std::env::var("PULSESCRIBE_CONFIG_DIR") {
        let trimmed = explicit.trim();
        if !trimmed.is_empty() {
            return Ok(PathBuf::from(trimmed));
        }
    }
    AppPaths::default_user()
        .map(|paths| paths.config_dir().to_path_buf())
        .ok_or_else(|| "Could not determine user home directory for ~/.pulsescribe".to_owned())
}

/// Start the PulseScribe desktop application and return the process exit code.
pub fn run() -> i32 {
    let config_dir = match resolve_config_dir() {
        Ok(dir) => dir,
        Err(err) => {
            eprintln!("PulseScribe startup error: {err}");
            return 1;
        }
    };

    // Acquire the single-instance lock BEFORE opening the rotating log file so a second launch
    // never shares or rotates `pulsescribe.log` concurrently with the running primary instance.
    let mut instance_guard = match SingleInstanceGuard::acquire(&config_dir) {
        Ok(SingleInstanceOutcome::Acquired(guard)) => Some(guard),
        Ok(SingleInstanceOutcome::AlreadyRunning) => {
            eprintln!(
                "PulseScribe is already running for {}; activated existing instance",
                config_dir.display()
            );
            return 0;
        }
        Err(err) => {
            eprintln!("PulseScribe single-instance lock unavailable ({err}); continuing startup");
            None
        }
    };

    logging::init_desktop_logging(&config_dir);

    let tokio_rt = match tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
    {
        Ok(rt) => rt,
        Err(err) => {
            eprintln!("PulseScribe Tokio runtime error: {err}");
            return 1;
        }
    };

    let store = Arc::new(PreferencesStore::new(&config_dir));
    let initial_config = DaemonConfig::from_store_startup(&store);
    tracing::info!(
        version = env!("CARGO_PKG_VERSION"),
        mode = initial_config.mode.as_str(),
        "PulseScribe desktop starting"
    );
    let platform = DaemonPlatform::native(true);
    let permissions = Arc::clone(&platform.permissions);
    let local_asr = LocalAsrManager::from_config_dir(&config_dir);
    let downloader = match ModelDownloader::new() {
        Ok(d) => d,
        Err(err) => {
            eprintln!("PulseScribe model downloader error: {err}");
            return 1;
        }
    };
    let http_client = match Client::builder()
        .user_agent(concat!("pulsescribe-desktop/", env!("CARGO_PKG_VERSION")))
        .build()
    {
        Ok(c) => c,
        Err(err) => {
            eprintln!("PulseScribe HTTP client error: {err}");
            return 1;
        }
    };

    let backend = match LiveDictationBackend::with_local_asr_and_downloader(
        &config_dir,
        Arc::clone(&permissions),
        local_asr.clone(),
        downloader.clone(),
    ) {
        Ok(b) => b,
        Err(err) => {
            eprintln!("PulseScribe dictation backend error: {err}");
            return 1;
        }
    };

    let daemon_runtime = tokio_rt.block_on(async {
        backend.sync_deepgram_warm_state(&initial_config).await;
        DaemonRuntime::spawn(
            Arc::clone(&store),
            initial_config,
            platform,
            Arc::new(backend.clone()),
            DaemonRuntimeOptions::default(),
        )
        .await
    });
    let daemon_handle = daemon_runtime.handle();

    let app = match tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            commands::get_app_state,
            commands::get_settings,
            commands::save_settings,
            commands::validate_hotkey,
            commands::check_permissions,
            commands::request_permission,
            commands::open_privacy_settings,
            commands::list_audio_devices,
            commands::get_history,
            commands::clear_history,
            commands::delete_history_entry,
            commands::copy_text_to_clipboard,
            commands::get_vocabulary,
            commands::save_vocabulary,
            commands::get_prompts,
            commands::save_prompts,
            commands::reset_prompts,
            commands::list_local_models,
            commands::download_local_model,
            commands::cancel_model_download,
            commands::delete_local_model,
            commands::unload_local_model,
            commands::toggle_dictation,
            commands::start_test_dictation,
            commands::cancel_dictation,
            commands::set_test_hotkey_mode,
            commands::copy_last_transcript,
            commands::set_onboarding_state,
            commands::get_theme_preference,
            commands::save_theme_preference,
            commands::save_ui_language,
            commands::open_config_dir,
            commands::open_release_url,
            commands::get_autostart,
            commands::set_autostart,
            commands::export_diagnostics,
            commands::check_for_updates,
        ])
        .build(tauri::generate_context!())
    {
        Ok(app) => app,
        Err(err) => {
            eprintln!("PulseScribe Tauri build error: {err}");
            tokio_rt.block_on(async {
                daemon_runtime.shutdown().await;
                backend.shutdown().await;
            });
            return 1;
        }
    };

    #[cfg(target_os = "macos")]
    {
        // Keep PulseScribe as a clean menu-bar accessory (no idle Dock icon).
        let _ = app
            .handle()
            .set_activation_policy(tauri::ActivationPolicy::Accessory);
    }

    let (mut overlay_owner, overlay_handle) = spawn_native_overlay(app.handle().clone());

    let (_, initial_ui_lang) = load_ui_language_pref(&store);
    let tray = match TrayController::build(app.handle(), initial_ui_lang) {
        Ok(controller) => Arc::new(controller),
        Err(err) => {
            eprintln!("PulseScribe tray initialization error: {err}");
            drop(overlay_owner);
            tokio_rt.block_on(async {
                daemon_runtime.shutdown().await;
                backend.shutdown().await;
            });
            return 1;
        }
    };
    app.manage(Arc::clone(&tray));

    let desktop_state = DesktopState::new(
        config_dir,
        Arc::clone(&store),
        permissions,
        local_asr,
        downloader,
        daemon_handle.clone(),
        backend.clone(),
        overlay_handle.clone(),
        http_client,
    );
    app.manage(desktop_state);

    if let Some(guard) = instance_guard.as_mut() {
        let activate_app = app.handle().clone();
        guard.start_activation_listener(move || {
            let handle = activate_app.clone();
            let _ = activate_app.run_on_main_thread(move || {
                if handle
                    .try_state::<DesktopState>()
                    .is_some_and(|s| s.is_quitting())
                {
                    return;
                }
                let _ = open_or_focus_window(&handle, "general");
            });
        });
    }

    // Subscribe BEFORE syncing initial local auto-download/preload and spawning the event bridge
    // so: (1) startup `AppState::Loading` events are buffered for overlay/tray, and (2) any
    // `ConfigReloaded` event is handled strictly after the initial startup sync completes.
    let bridge_events = daemon_handle.subscribe();

    // Wire loading-overlay & UI progress observer, then trigger initial local auto-download/preload
    // using the runtime's live config in case `.env` reloaded during Tauri/tray initialization.
    backend.set_local_preload_observer(Arc::new(DesktopPreloadObserver {
        daemon: daemon_handle.clone(),
        app: app.handle().clone(),
    }));
    {
        let _guard = tokio_rt.enter();
        let startup_config = daemon_handle.config();
        backend.sync_local_preload_state(&startup_config);
    }

    let bridge_app = app.handle().clone();
    let bridge_daemon = daemon_handle.clone();
    let bridge_backend = backend.clone();
    let bridge_overlay = overlay_handle.clone();
    let bridge_tray = Arc::clone(&tray);
    let mut bridge_task = Some(tokio_rt.spawn(async move {
        run_event_bridge(
            bridge_app,
            bridge_daemon,
            bridge_events,
            bridge_backend,
            bridge_overlay,
            bridge_tray,
        )
        .await;
    }));

    let open_onboarding_on_ready = !store.is_onboarding_complete();
    let mut daemon_runtime = Some(daemon_runtime);

    let exit_code = app.run_return(move |app_handle, event| match event {
        RunEvent::Ready => {
            if open_onboarding_on_ready {
                let _ = open_or_focus_window(app_handle, "onboarding");
            }
        }
        RunEvent::WindowEvent {
            label,
            event: tauri::WindowEvent::Destroyed,
            ..
        } if label == window::MAIN_WINDOW_LABEL => {
            // Ensure closing onboarding/settings never leaves hotkeys stuck in test mode.
            if let Some(state) = app_handle.try_state::<DesktopState>() {
                state.daemon.set_test_hotkey_mode(false);
            }
        }
        RunEvent::ExitRequested { api, code, .. } => {
            let is_quitting = app_handle
                .try_state::<DesktopState>()
                .is_some_and(|s| s.is_quitting());
            // Keep tray + daemon + native overlay alive at 0 WebViews when the last window closes.
            if code.is_none() && !is_quitting {
                api.prevent_exit();
            }
        }
        RunEvent::Exit => {
            if let Some(state) = app_handle.try_state::<DesktopState>() {
                state.set_quitting();
                state.cancel_all_downloads();
            }
            overlay_handle.shutdown();
            drop(overlay_owner.take());
            let task = bridge_task.take();
            let runtime = daemon_runtime.take();
            tokio_rt.block_on(async {
                if let Some(t) = task {
                    t.abort();
                    let _ = t.await;
                }
                if let Some(rt) = runtime {
                    rt.shutdown().await;
                }
                backend.shutdown().await;
            });
            // Keep the single-instance lock until daemon, IPC, and hotkey shutdown completes so a
            // quick relaunch cannot collide with resources still being released.
            drop(instance_guard.take());
        }
        _ => {}
    });

    exit_code
}

async fn run_event_bridge(
    app: AppHandle,
    daemon: pulsescribe_daemon::DaemonHandle,
    mut events: tokio::sync::broadcast::Receiver<DaemonEvent>,
    backend: LiveDictationBackend,
    overlay: OverlayHandle,
    tray: Arc<TrayController>,
) {
    let mut overlay_enabled = daemon.config().overlay_enabled;

    let schedule_tray_update =
        |app: &AppHandle,
         tray: &Arc<TrayController>,
         state: pulsescribe_core::AppState,
         detail: Option<String>,
         error: Option<pulsescribe_core::DaemonStatusError>| {
            let tray = Arc::clone(tray);
            let _ = app.run_on_main_thread(move || {
                tray.update_status(state, detail.as_deref(), error.as_ref());
            });
        };

    loop {
        match events.recv().await {
            Ok(event) => {
                if overlay_enabled {
                    overlay.apply_daemon_event(&event);
                }
                match &event {
                    DaemonEvent::StateChanged {
                        state,
                        detail,
                        error,
                        ..
                    } => {
                        if let Some(st) = app.try_state::<DesktopState>() {
                            st.update_status(|s| {
                                s.state = *state;
                                s.detail = detail.clone();
                                s.error = error.clone();
                            });
                        }
                        schedule_tray_update(&app, &tray, *state, detail.clone(), error.clone());
                        emit_to_main_if_open(&app, DAEMON_EVENT, &event);
                    }
                    DaemonEvent::TranscriptDelivered { text, .. } => {
                        if let Some(st) = app.try_state::<DesktopState>() {
                            st.update_status(|s| s.last_transcript = Some(text.clone()));
                        }
                        emit_to_main_if_open(&app, DAEMON_EVENT, &event);
                    }
                    DaemonEvent::TestTranscript { text, error, .. } => {
                        if let Some(st) = app.try_state::<DesktopState>() {
                            st.update_status(|s| {
                                s.last_test_transcript = Some(text.clone());
                                s.last_test_error = error.clone();
                            });
                        }
                        emit_to_main_if_open(&app, DAEMON_EVENT, &event);
                    }
                    DaemonEvent::ConfigReloaded => {
                        let reloaded = daemon.config();
                        overlay_enabled = reloaded.overlay_enabled;
                        if !overlay_enabled {
                            overlay.dismiss();
                        }
                        backend.sync_local_preload_state(&reloaded);
                        backend.sync_deepgram_warm_state(&reloaded).await;
                        emit_to_main_if_open(&app, DAEMON_EVENT, &event);
                    }
                    DaemonEvent::OpenSettingsRequested => {
                        let handle = app.clone();
                        let _ = app.run_on_main_thread(move || {
                            let _ = open_or_focus_window(&handle, "general");
                        });
                    }
                    DaemonEvent::AudioLevel { .. } | DaemonEvent::InterimText { .. } => {
                        emit_to_main_if_open(&app, DAEMON_EVENT, &event);
                    }
                }
            }
            Err(tokio::sync::broadcast::error::RecvError::Lagged(skipped)) => {
                tracing::debug!(skipped, "desktop event bridge lagged; resyncing");
                let current_state = daemon.state();
                let current_cfg = daemon.config();
                overlay_enabled = current_cfg.overlay_enabled;
                if overlay_enabled {
                    overlay.send(OverlayCommand::ApplyState {
                        state: current_state,
                        detail: None,
                        error: None,
                    });
                } else {
                    overlay.dismiss();
                }
                if let Some(st) = app.try_state::<DesktopState>() {
                    st.update_status(|s| {
                        s.state = current_state;
                        s.detail = None;
                        s.error = None;
                    });
                }
                backend.sync_local_preload_state(&current_cfg);
                backend.sync_deepgram_warm_state(&current_cfg).await;
                schedule_tray_update(&app, &tray, current_state, None, None);
            }
            Err(tokio::sync::broadcast::error::RecvError::Closed) => break,
        }
    }
}
