//! Clipboard text access, paste-shortcut boundaries, and race-free restore orchestration.
//!
//! Mirrors `whisper_platform/clipboard.py` and the paste/restore flow in `utils/hotkey.py`,
//! bridging directly into [`pulsescribe_core::TranscriptClipboardBackend`].

use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use pulsescribe_core::TranscriptClipboardBackend;

use crate::{lock_unpoisoned, PlatformError, PlatformKind};

/// Default grace period before restoring the previous clipboard on macOS (`1.0 s`).
pub const MACOS_CLIPBOARD_RESTORE_DELAY: Duration = Duration::from_secs(1);
/// Default grace period before restoring the previous clipboard on Windows (`0.5 s`).
pub const WINDOWS_CLIPBOARD_RESTORE_DELAY: Duration = Duration::from_millis(500);
/// Poll step for confirming clipboard visibility before sending Ctrl+V on Windows (`5 ms`).
pub const WINDOWS_CLIPBOARD_SYNC_POLL: Duration = Duration::from_millis(5);

/// OS clipboard text access boundary.
pub trait ClipboardBackend: Send + Sync {
    /// Writes UTF-8 text into the system clipboard.
    fn write_text(&self, text: &str) -> Result<(), PlatformError>;

    /// Reads UTF-8 text from the system clipboard (`Ok(None)` when empty or non-text).
    fn read_text(&self) -> Result<Option<String>, PlatformError>;

    /// Single-attempt read without internal open-retry sleeps for the Windows paste-sync loop.
    ///
    /// Adapters whose [`Self::read_text`] performs internal retries must override this with a
    /// non-retrying read or return [`PlatformError::Unavailable`].
    fn read_text_once(&self) -> Result<Option<String>, PlatformError> {
        self.read_text()
    }
}

/// OS shortcut injector boundary (`Cmd+V` on macOS, `Ctrl+V` on Windows).
pub trait PasteKeySender: Send + Sync {
    /// Emits the platform paste shortcut to the currently focused window.
    fn send_paste_shortcut(&self) -> Result<(), PlatformError>;
}

/// Cross-platform [`ClipboardBackend`] powered by `arboard`.
///
/// Creates a transient `arboard::Clipboard` per call so the adapter stays `Send + Sync`
/// on every target. macOS and Windows retain clipboard contents after the handle drops;
/// Linux X11/Wayland requires a dedicated persistent clipboard owner backend.
#[derive(Debug, Default, Clone, Copy)]
pub struct ArboardClipboard;

impl ClipboardBackend for ArboardClipboard {
    fn write_text(&self, text: &str) -> Result<(), PlatformError> {
        let mut clipboard =
            arboard::Clipboard::new().map_err(|err| PlatformError::Clipboard(err.to_string()))?;
        clipboard
            .set_text(text.to_owned())
            .map_err(|err| PlatformError::Clipboard(err.to_string()))
    }

    fn read_text(&self) -> Result<Option<String>, PlatformError> {
        let mut clipboard =
            arboard::Clipboard::new().map_err(|err| PlatformError::Clipboard(err.to_string()))?;
        match clipboard.get_text() {
            Ok(text) => Ok(Some(text)),
            Err(arboard::Error::ContentNotAvailable) => Ok(None),
            Err(err) => Err(PlatformError::Clipboard(err.to_string())),
        }
    }

    #[cfg(target_os = "windows")]
    fn read_text_once(&self) -> Result<Option<String>, PlatformError> {
        // `arboard`'s Windows backend retries OpenClipboard with internal sleeps,
        // which would exceed the caller's sync-verify budget.
        Err(PlatformError::Unavailable(
            "single-attempt clipboard read requires the native Windows clipboard adapter",
        ))
    }
}

/// Timing and restoration policy for [`TranscriptPasteAdapter`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ClipboardPastePolicy {
    pub restore_clipboard: bool,
    pub restore_delay: Duration,
    pub paste_sync_timeout: Duration,
    pub paste_sync_poll: Duration,
}

impl ClipboardPastePolicy {
    /// Platform-default policy matching `utils/hotkey.py`.
    pub const fn for_platform(platform: PlatformKind, restore_clipboard: bool) -> Self {
        let restore_delay = match platform {
            PlatformKind::MacOs => MACOS_CLIPBOARD_RESTORE_DELAY,
            PlatformKind::Windows | PlatformKind::Linux | PlatformKind::Other => {
                WINDOWS_CLIPBOARD_RESTORE_DELAY
            }
        };
        Self {
            restore_clipboard,
            restore_delay,
            paste_sync_timeout: Duration::ZERO,
            paste_sync_poll: WINDOWS_CLIPBOARD_SYNC_POLL,
        }
    }
}

impl Default for ClipboardPastePolicy {
    fn default() -> Self {
        Self::for_platform(PlatformKind::current(), false)
    }
}

#[derive(Debug, Default)]
struct RestoreState {
    generation: u64,
    shutdown: bool,
}

/// Orchestrates copy, paste, optional sync verification, and generation-guarded
/// clipboard restoration behind [`pulsescribe_core::TranscriptClipboardBackend`].
pub struct TranscriptPasteAdapter<C: ClipboardBackend + 'static, P: PasteKeySender> {
    clipboard: Arc<C>,
    paste_keys: P,
    policy: ClipboardPastePolicy,
    restore_state: Arc<(Mutex<RestoreState>, Condvar)>,
}

impl<C: ClipboardBackend + 'static, P: PasteKeySender> TranscriptPasteAdapter<C, P> {
    pub fn new(clipboard: Arc<C>, paste_keys: P, policy: ClipboardPastePolicy) -> Self {
        Self {
            clipboard,
            paste_keys,
            policy,
            restore_state: Arc::new((Mutex::new(RestoreState::default()), Condvar::new())),
        }
    }

    pub fn policy(&self) -> ClipboardPastePolicy {
        self.policy
    }

    pub fn set_policy(&mut self, policy: ClipboardPastePolicy) {
        self.policy = policy;
    }

    fn wait_for_clipboard_sync(&self, expected_text: &str) -> bool {
        if self.policy.paste_sync_timeout.is_zero() {
            return true;
        }
        let deadline = Instant::now() + self.policy.paste_sync_timeout;
        let poll = if self.policy.paste_sync_poll.is_zero() {
            WINDOWS_CLIPBOARD_SYNC_POLL
        } else {
            self.policy.paste_sync_poll
        };
        loop {
            match self.clipboard.read_text_once() {
                Ok(Some(ref current)) if current == expected_text => return true,
                Err(PlatformError::Unavailable(_)) => return false,
                _ => {}
            }
            let Some(remaining) = deadline.checked_duration_since(Instant::now()) else {
                tracing::debug!(
                    timeout_ms = self.policy.paste_sync_timeout.as_millis() as u64,
                    "clipboard sync not confirmed before timeout; proceeding with paste"
                );
                return false;
            };
            if remaining.is_zero() {
                return false;
            }
            thread::sleep(poll.min(remaining));
        }
    }

    fn restore_now(&self, generation: u64, expected_current: &str, previous: &str) {
        let guard = lock_unpoisoned(&self.restore_state.0);
        if !guard.shutdown && guard.generation == generation {
            restore_if_unchanged(self.clipboard.as_ref(), expected_current, previous);
        }
    }
}

impl<C: ClipboardBackend + 'static, P: PasteKeySender> Drop for TranscriptPasteAdapter<C, P> {
    fn drop(&mut self) {
        let mut guard = lock_unpoisoned(&self.restore_state.0);
        guard.shutdown = true;
        self.restore_state.1.notify_all();
    }
}

fn restore_if_unchanged<C: ClipboardBackend>(
    clipboard: &C,
    expected_current: &str,
    previous_text: &str,
) -> bool {
    match clipboard.read_text() {
        Ok(Some(current)) if current == expected_current => {
            if let Err(err) = clipboard.write_text(previous_text) {
                tracing::warn!(error = %err, "clipboard restore failed");
                return false;
            }
            true
        }
        Ok(_) => {
            tracing::debug!("clipboard restore skipped: contents changed");
            false
        }
        Err(err) => {
            tracing::warn!(error = %err, "clipboard read before restore failed");
            false
        }
    }
}

impl<C: ClipboardBackend + 'static, P: PasteKeySender> TranscriptClipboardBackend
    for TranscriptPasteAdapter<C, P>
{
    fn paste_transcript(&mut self, text: &str) -> bool {
        let (previous_text, restore_generation) = {
            let mut guard = lock_unpoisoned(&self.restore_state.0);
            let previous = if self.policy.restore_clipboard {
                self.clipboard.read_text().ok().flatten()
            } else {
                None
            };
            if let Err(err) = self.clipboard.write_text(text) {
                tracing::warn!(error = %err, "clipboard write before paste failed");
                return false;
            }
            guard.generation = guard.generation.wrapping_add(1);
            self.restore_state.1.notify_all();
            (previous, guard.generation)
        };

        self.wait_for_clipboard_sync(text);

        if let Err(err) = self.paste_keys.send_paste_shortcut() {
            tracing::error!(error = %err, "auto-paste shortcut failed");
            if let Some(previous) = previous_text.filter(|prev| prev != text) {
                self.restore_now(restore_generation, text, &previous);
            }
            return false;
        }

        if let Some(previous) = previous_text.filter(|prev| prev != text) {
            if self.policy.restore_delay.is_zero() {
                self.restore_now(restore_generation, text, &previous);
            } else {
                let clipboard = Arc::clone(&self.clipboard);
                let restore_state = Arc::clone(&self.restore_state);
                let expected = text.to_owned();
                let delay = self.policy.restore_delay;
                let spawn_result = thread::Builder::new()
                    .name("pulsescribe-clipboard-restore".to_owned())
                    .spawn(move || {
                        let (lock, cvar) = &*restore_state;
                        let guard = lock_unpoisoned(lock);
                        let (guard, _) = cvar
                            .wait_timeout_while(guard, delay, |state| {
                                !state.shutdown && state.generation == restore_generation
                            })
                            .unwrap_or_else(|err| err.into_inner());
                        if !guard.shutdown && guard.generation == restore_generation {
                            restore_if_unchanged(clipboard.as_ref(), &expected, &previous);
                        }
                    });
                if let Err(err) = spawn_result {
                    tracing::warn!(error = %err, "failed to spawn clipboard restore thread");
                }
            }
        }

        true
    }

    fn copy_transcript(&mut self, text: &str) -> bool {
        let mut guard = lock_unpoisoned(&self.restore_state.0);
        match self.clipboard.write_text(text) {
            Ok(()) => {
                guard.generation = guard.generation.wrapping_add(1);
                self.restore_state.1.notify_all();
                true
            }
            Err(err) => {
                tracing::warn!(error = %err, "clipboard copy failed");
                false
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pulsescribe_core::TranscriptDelivery;
    use std::sync::atomic::{AtomicUsize, Ordering};

    #[derive(Default)]
    struct StubClipboard {
        text: Mutex<Option<String>>,
        history: Mutex<Vec<String>>,
        read_once_misses: Mutex<usize>,
        read_once_unavailable: bool,
        read_once_calls: AtomicUsize,
    }

    impl StubClipboard {
        fn with_text(initial: &str) -> Self {
            Self {
                text: Mutex::new(Some(initial.to_owned())),
                ..Self::default()
            }
        }
    }

    impl ClipboardBackend for StubClipboard {
        fn write_text(&self, text: &str) -> Result<(), PlatformError> {
            *lock_unpoisoned(&self.text) = Some(text.to_owned());
            lock_unpoisoned(&self.history).push(text.to_owned());
            Ok(())
        }

        fn read_text(&self) -> Result<Option<String>, PlatformError> {
            Ok(lock_unpoisoned(&self.text).clone())
        }

        fn read_text_once(&self) -> Result<Option<String>, PlatformError> {
            self.read_once_calls.fetch_add(1, Ordering::SeqCst);
            if self.read_once_unavailable {
                return Err(PlatformError::Unavailable("not supported"));
            }
            let mut misses = lock_unpoisoned(&self.read_once_misses);
            if *misses > 0 {
                *misses -= 1;
                return Ok(None);
            }
            drop(misses);
            self.read_text()
        }
    }

    struct StubPasteKeys(Result<(), &'static str>);

    impl PasteKeySender for StubPasteKeys {
        fn send_paste_shortcut(&self) -> Result<(), PlatformError> {
            self.0.map_err(|msg| PlatformError::Paste(msg.to_owned()))
        }
    }

    #[test]
    fn paste_policy_defaults_match_platform_timings() {
        let mac = ClipboardPastePolicy::for_platform(PlatformKind::MacOs, true);
        assert!(mac.restore_clipboard);
        assert_eq!(mac.restore_delay, Duration::from_secs(1));
        assert_eq!(mac.paste_sync_timeout, Duration::ZERO);

        let win = ClipboardPastePolicy::for_platform(PlatformKind::Windows, false);
        assert!(!win.restore_clipboard);
        assert_eq!(win.restore_delay, Duration::from_millis(500));
        assert_eq!(win.paste_sync_poll, Duration::from_millis(5));
    }

    #[test]
    fn paste_restores_previous_clipboard_and_cancels_on_explicit_copy() {
        let clipboard = Arc::new(StubClipboard::with_text("original"));
        let mut adapter = TranscriptPasteAdapter::new(
            Arc::clone(&clipboard),
            StubPasteKeys(Ok(())),
            ClipboardPastePolicy {
                restore_clipboard: true,
                restore_delay: Duration::ZERO,
                paste_sync_timeout: Duration::ZERO,
                paste_sync_poll: WINDOWS_CLIPBOARD_SYNC_POLL,
            },
        );
        assert!(adapter.paste_transcript("dictated"));
        assert_eq!(
            lock_unpoisoned(&clipboard.text).as_deref(),
            Some("original")
        );

        // Explicit copy of identical text must cancel a pending delayed restore.
        adapter.set_policy(ClipboardPastePolicy {
            restore_clipboard: true,
            restore_delay: Duration::from_millis(50),
            paste_sync_timeout: Duration::ZERO,
            paste_sync_poll: WINDOWS_CLIPBOARD_SYNC_POLL,
        });
        assert!(adapter.paste_transcript("same"));
        assert!(adapter.copy_transcript("same"));
        thread::sleep(Duration::from_millis(80));
        assert_eq!(lock_unpoisoned(&clipboard.text).as_deref(), Some("same"));
    }

    #[test]
    fn failed_paste_falls_back_through_transcript_delivery() {
        let clipboard = Arc::new(StubClipboard::with_text("original"));
        let mut adapter = TranscriptPasteAdapter::new(
            Arc::clone(&clipboard),
            StubPasteKeys(Err("accessibility missing")),
            ClipboardPastePolicy {
                restore_clipboard: true,
                restore_delay: Duration::ZERO,
                paste_sync_timeout: Duration::ZERO,
                paste_sync_poll: WINDOWS_CLIPBOARD_SYNC_POLL,
            },
        );
        let mut delivery = TranscriptDelivery::for_platform(true);
        let outcome = delivery.deliver("Hello world", true, &mut adapter);
        assert!(!outcome.ok);
        assert_eq!(outcome.feedback, "Copied — press ⌘V");
        assert_eq!(
            lock_unpoisoned(&clipboard.text).as_deref(),
            Some("Hello world")
        );
    }

    #[test]
    fn sync_verify_waits_for_visibility_and_aborts_immediately_when_unavailable() {
        let clipboard = Arc::new(StubClipboard {
            read_once_misses: Mutex::new(2),
            ..StubClipboard::default()
        });
        let mut adapter = TranscriptPasteAdapter::new(
            Arc::clone(&clipboard),
            StubPasteKeys(Ok(())),
            ClipboardPastePolicy {
                restore_clipboard: false,
                restore_delay: Duration::ZERO,
                paste_sync_timeout: Duration::from_millis(100),
                paste_sync_poll: Duration::from_millis(1),
            },
        );
        assert!(adapter.paste_transcript("synced"));
        assert_eq!(clipboard.read_once_calls.load(Ordering::SeqCst), 3);

        let unavailable = Arc::new(StubClipboard {
            read_once_unavailable: true,
            ..StubClipboard::default()
        });
        let mut adapter = TranscriptPasteAdapter::new(
            Arc::clone(&unavailable),
            StubPasteKeys(Ok(())),
            ClipboardPastePolicy {
                restore_clipboard: false,
                restore_delay: Duration::ZERO,
                paste_sync_timeout: Duration::from_secs(5),
                paste_sync_poll: Duration::from_millis(1),
            },
        );
        assert!(adapter.paste_transcript("fast"));
        assert_eq!(unavailable.read_once_calls.load(Ordering::SeqCst), 1);
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn arboard_read_text_once_is_explicitly_unavailable_on_windows() {
        assert!(matches!(
            ArboardClipboard.read_text_once(),
            Err(PlatformError::Unavailable(_))
        ));
    }
}
