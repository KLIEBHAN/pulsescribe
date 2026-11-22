//! Platform adapter bundle wiring `pulsescribe-platform` traits for `DaemonRuntime`.
//!
//! Keeps `DaemonRuntime` 100% platform-agnostic while offering [`DaemonPlatform::native`]
//! to instantiate the real macOS or Windows adapters behind trait boundaries.

use std::sync::{Arc, Mutex};

use pulsescribe_core::TranscriptClipboardBackend;
use pulsescribe_platform::{
    AppDetector, ClipboardPastePolicy, HotkeyBackend, HotkeyBinding, HotkeyCallback,
    HotkeyRegistration, NoopAppDetector, NoopSoundPlayer, PermissionsChecker, PlatformError,
    PlatformKind, SoundPlayer, TranscriptPasteAdapter,
};

/// No-op [`HotkeyRegistration`] used by [`NoopHotkeyBackend`].
#[derive(Debug, Default)]
pub struct NoopHotkeyRegistration;

impl HotkeyRegistration for NoopHotkeyRegistration {
    fn unregister(&mut self) -> Result<(), PlatformError> {
        Ok(())
    }
}

/// No-op [`HotkeyBackend`] for headless/test/unsupported platforms.
#[derive(Debug, Default, Clone, Copy)]
pub struct NoopHotkeyBackend;

impl HotkeyBackend for NoopHotkeyBackend {
    fn register(
        &self,
        _binding: &HotkeyBinding,
        _on_event: HotkeyCallback,
    ) -> Result<Box<dyn HotkeyRegistration>, PlatformError> {
        Ok(Box::new(NoopHotkeyRegistration))
    }
}

/// No-op [`PasteKeySender`] fallback used on platforms without a native shortcut injector yet.
#[cfg(not(any(target_os = "macos", target_os = "windows")))]
#[derive(Debug, Default, Clone, Copy)]
struct UnavailablePasteKeySender;

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
impl pulsescribe_platform::PasteKeySender for UnavailablePasteKeySender {
    fn send_paste_shortcut(&self) -> Result<(), PlatformError> {
        Err(PlatformError::Unavailable(
            "native paste shortcut injection is not implemented on this platform",
        ))
    }
}

/// Complete set of OS capabilities injected into [`crate::DaemonRuntime`].
pub struct DaemonPlatform {
    pub app_detector: Arc<dyn AppDetector>,
    pub sound: Arc<dyn SoundPlayer>,
    pub hotkeys: Arc<dyn HotkeyBackend>,
    pub permissions: Arc<dyn PermissionsChecker>,
    pub clipboard: Arc<Mutex<dyn TranscriptClipboardBackend + Send>>,
    pub platform_kind: PlatformKind,
}

impl DaemonPlatform {
    /// Instantiate native OS adapters for the compile-time target platform.
    pub fn native(restore_clipboard: bool) -> Self {
        #[cfg(target_os = "macos")]
        {
            use pulsescribe_platform::macos::{
                MacOsAppDetector, MacOsClipboard, MacOsHotkeyBackend, MacOsPermissionsChecker,
                MacOsSoundPlayer,
            };
            use pulsescribe_platform::CachedPermissionsChecker;

            let paste_adapter = TranscriptPasteAdapter::new(
                Arc::new(MacOsClipboard),
                pulsescribe_platform::macos::MacOsPasteKeySender,
                ClipboardPastePolicy::for_platform(PlatformKind::MacOs, restore_clipboard),
            );
            Self {
                app_detector: Arc::new(MacOsAppDetector),
                sound: Arc::new(MacOsSoundPlayer::new()),
                hotkeys: Arc::new(MacOsHotkeyBackend),
                permissions: Arc::new(CachedPermissionsChecker::new(MacOsPermissionsChecker)),
                clipboard: Arc::new(Mutex::new(paste_adapter)),
                platform_kind: PlatformKind::MacOs,
            }
        }

        #[cfg(target_os = "windows")]
        {
            use pulsescribe_platform::windows::{
                WindowsAppDetector, WindowsClipboard, WindowsHotkeyBackend, WindowsPasteKeySender,
                WindowsPermissionsChecker, WindowsSoundPlayer,
            };
            use pulsescribe_platform::CachedPermissionsChecker;

            let paste_adapter = TranscriptPasteAdapter::new(
                Arc::new(WindowsClipboard),
                WindowsPasteKeySender,
                ClipboardPastePolicy::for_platform(PlatformKind::Windows, restore_clipboard),
            );
            Self {
                app_detector: Arc::new(WindowsAppDetector::new()),
                sound: Arc::new(WindowsSoundPlayer::new()),
                hotkeys: Arc::new(WindowsHotkeyBackend),
                permissions: Arc::new(CachedPermissionsChecker::new(
                    WindowsPermissionsChecker::default(),
                )),
                clipboard: Arc::new(Mutex::new(paste_adapter)),
                platform_kind: PlatformKind::Windows,
            }
        }

        #[cfg(not(any(target_os = "macos", target_os = "windows")))]
        {
            use pulsescribe_platform::{ArboardClipboard, DefaultPermissionsChecker};

            let paste_adapter = TranscriptPasteAdapter::new(
                Arc::new(ArboardClipboard),
                UnavailablePasteKeySender,
                ClipboardPastePolicy::for_platform(PlatformKind::current(), restore_clipboard),
            );
            Self {
                app_detector: Arc::new(NoopAppDetector),
                sound: Arc::new(NoopSoundPlayer),
                hotkeys: Arc::new(NoopHotkeyBackend),
                permissions: Arc::new(DefaultPermissionsChecker),
                clipboard: Arc::new(Mutex::new(paste_adapter)),
                platform_kind: PlatformKind::current(),
            }
        }
    }

    /// Lightweight no-op bundle useful in unit tests and headless dry-runs.
    pub fn noop(clipboard: Arc<Mutex<dyn TranscriptClipboardBackend + Send>>) -> Self {
        Self {
            app_detector: Arc::new(NoopAppDetector),
            sound: Arc::new(NoopSoundPlayer),
            hotkeys: Arc::new(NoopHotkeyBackend),
            permissions: Arc::new(pulsescribe_platform::DefaultPermissionsChecker),
            clipboard,
            platform_kind: PlatformKind::current(),
        }
    }
}
