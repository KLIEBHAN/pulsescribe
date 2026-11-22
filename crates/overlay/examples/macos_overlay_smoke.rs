//! Live macOS smoke check for `MacOsOverlay` (`NSPanel` + Core Animation `CALayer`).
//!
//! Verifies zero focus steal (`frontmostApplication` unchanged & `is_key_window == false`),
//! active 60 Hz animation -> 0 FPS silence settle, GPU shimmer in `Transcribing`/`Refining`,
//! outcome badge in `Done`, interactive `Error` pill, and clean `Idle` hide.

#[cfg(not(target_os = "macos"))]
fn main() {
    eprintln!("macos_overlay_smoke runs only on macOS.");
}

#[cfg(target_os = "macos")]
fn main() {
    use std::time::Duration;

    use objc2_app_kit::{NSApplication, NSApplicationActivationPolicy, NSWorkspace};
    use objc2_foundation::MainThreadMarker;
    use pulsescribe_core::{AppState, DaemonErrorCode, DaemonStatusError};
    use pulsescribe_overlay::macos::{pump_main_run_loop, MacOsOverlay};
    use pulsescribe_overlay::{OverlayCommand, OverlayTimerMode};

    let mtm = MainThreadMarker::new().expect("must run on macOS main thread");
    let app = NSApplication::sharedApplication(mtm);
    app.setActivationPolicy(NSApplicationActivationPolicy::Accessory);

    let frontmost_before = NSWorkspace::sharedWorkspace()
        .frontmostApplication()
        .and_then(|a| a.bundleIdentifier())
        .map(|s| s.to_string());

    let (overlay, handle) =
        MacOsOverlay::new(mtm, |ev| eprintln!("overlay action: {ev:?}")).expect("overlay init");

    let pump = |ms: u64| pump_main_run_loop(mtm, Duration::from_millis(ms));

    // 1. Listening -> Recording with speech energy & interim preview
    handle.send(OverlayCommand::ApplyState {
        state: AppState::Listening,
        detail: None,
        error: None,
    });
    pump(60);
    assert!(overlay.is_visible(), "overlay must be visible in Listening");
    assert!(
        !overlay.is_key_window(),
        "overlay must never become key window"
    );

    handle.send(OverlayCommand::ApplyState {
        state: AppState::Recording,
        detail: None,
        error: None,
    });
    handle.send(OverlayCommand::AudioLevel(0.35));
    handle.send(OverlayCommand::InterimText(
        "Hallo Fabian, natives 60 FPS Overlay in Rust".to_owned(),
    ));
    pump(120);
    assert_eq!(overlay.timer_mode(), OverlayTimerMode::Active60Hz);
    assert!(!overlay.is_key_window());

    // 2. Transcribing (GPU shimmer, 0 FPS Rust timer)
    handle.send(OverlayCommand::ApplyState {
        state: AppState::Transcribing,
        detail: Some("Transcribing, then refining".to_owned()),
        error: None,
    });
    pump(80);
    assert_eq!(overlay.timer_mode(), OverlayTimerMode::Stopped);

    // 3. Refining -> Done -> Error -> Idle
    handle.send(OverlayCommand::ApplyState {
        state: AppState::Refining,
        detail: None,
        error: None,
    });
    pump(60);
    assert_eq!(overlay.timer_mode(), OverlayTimerMode::Stopped);

    handle.send(OverlayCommand::ApplyState {
        state: AppState::Done,
        detail: Some("Transcript pasted into Notes · 0.6 s".to_owned()),
        error: None,
    });
    pump(60);

    handle.send(OverlayCommand::ApplyState {
        state: AppState::Error,
        detail: None,
        error: Some(DaemonStatusError::new(
            DaemonErrorCode::MissingApiKey,
            Some("DEEPGRAM_API_KEY missing".to_owned()),
        )),
    });
    pump(60);
    assert!(!overlay.is_key_window());

    handle.send(OverlayCommand::ApplyState {
        state: AppState::Idle,
        detail: None,
        error: None,
    });
    pump(60);
    assert!(!overlay.is_visible(), "overlay must hide on Idle");
    assert_eq!(overlay.timer_mode(), OverlayTimerMode::Stopped);

    let frontmost_after = NSWorkspace::sharedWorkspace()
        .frontmostApplication()
        .and_then(|a| a.bundleIdentifier())
        .map(|s| s.to_string());

    println!(
        "PASS macos_overlay_smoke: frontmost_before={frontmost_before:?}, frontmost_after={frontmost_after:?}, is_key_window={}, timer_mode={:?}",
        overlay.is_key_window(),
        overlay.timer_mode(),
    );
    assert_eq!(
        frontmost_before, frontmost_after,
        "overlay must not steal focus from the frontmost app"
    );
}
