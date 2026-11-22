//! OS permission status types, checker trait, and short-lived UI snapshot cache.
//!
//! Mirrors `utils/permissions.py`.

use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

use crate::{lock_unpoisoned, PlatformError};

/// Shared TTL for UI burst permission queries (250 ms, matching `utils/permissions.py`).
pub const PERMISSION_SNAPSHOT_TTL: Duration = Duration::from_millis(250);

const PERMISSION_MESSAGE_TOKENS: [&str; 6] = [
    "eingabemonitoring",
    "input monitoring",
    "bedienungshilfen",
    "accessibility",
    "mikrofon",
    "microphone",
];

/// System permissions required by PulseScribe (enforced on macOS; granted by default elsewhere).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PermissionKind {
    Microphone,
    Accessibility,
    InputMonitoring,
}

impl PermissionKind {
    /// macOS Privacy & Security System Settings anchor.
    pub const fn macos_privacy_anchor(self) -> &'static str {
        match self {
            Self::Microphone => "Privacy_Microphone",
            Self::Accessibility => "Privacy_Accessibility",
            Self::InputMonitoring => "Privacy_ListenEvent",
        }
    }

    /// Deep-link URL opening the corresponding macOS Privacy & Security pane.
    pub fn macos_settings_url(self) -> String {
        format!(
            "x-apple.systempreferences:com.apple.preference.security?{}",
            self.macos_privacy_anchor()
        )
    }
}

/// Fine-grained microphone permission state matching `AVAuthorizationStatus` and `utils/permissions.py`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MicrophonePermissionStatus {
    Authorized,
    NotDetermined,
    Denied,
    Restricted,
    Unknown,
}

impl MicrophonePermissionStatus {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Authorized => "authorized",
            Self::NotDetermined => "not_determined",
            Self::Denied => "denied",
            Self::Restricted => "restricted",
            Self::Unknown => "unknown",
        }
    }

    /// Returns `true` when recording may proceed (`Authorized` or first-use `NotDetermined`).
    pub const fn is_recording_allowed(self) -> bool {
        matches!(self, Self::Authorized | Self::NotDetermined)
    }
}

/// Point-in-time snapshot of all three PulseScribe permissions.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct PermissionSnapshot {
    pub microphone: MicrophonePermissionStatus,
    pub accessibility: bool,
    pub input_monitoring: bool,
}

impl PermissionSnapshot {
    /// Safe non-macOS default where the OS does not gate these capabilities via TCC.
    pub const fn all_granted_default() -> Self {
        Self {
            microphone: MicrophonePermissionStatus::Authorized,
            accessibility: true,
            input_monitoring: true,
        }
    }

    /// Returns `true` when every required permission is actively granted.
    pub const fn all_ready(self) -> bool {
        matches!(self.microphone, MicrophonePermissionStatus::Authorized)
            && self.accessibility
            && self.input_monitoring
    }
}

/// Boundary for inspecting and requesting OS permissions.
pub trait PermissionsChecker: Send + Sync {
    fn microphone_status(&self) -> MicrophonePermissionStatus;
    fn has_accessibility(&self) -> bool;
    fn has_input_monitoring(&self) -> bool;

    /// Captures all three permission states at once.
    fn snapshot(&self) -> PermissionSnapshot {
        PermissionSnapshot {
            microphone: self.microphone_status(),
            accessibility: self.has_accessibility(),
            input_monitoring: self.has_input_monitoring(),
        }
    }

    /// Prompts the OS to request a specific permission (no-op where unsupported).
    fn request_permission(&self, _kind: PermissionKind) -> Result<(), PlatformError> {
        Ok(())
    }

    /// Opens the OS privacy settings pane for `kind` (no-op where unsupported).
    fn open_privacy_settings(&self, _kind: PermissionKind) -> Result<(), PlatformError> {
        Ok(())
    }
}

/// Non-macOS default implementation returning authorized/granted for all checks.
#[derive(Debug, Default, Clone, Copy)]
pub struct DefaultPermissionsChecker;

impl PermissionsChecker for DefaultPermissionsChecker {
    fn microphone_status(&self) -> MicrophonePermissionStatus {
        MicrophonePermissionStatus::Authorized
    }

    fn has_accessibility(&self) -> bool {
        true
    }

    fn has_input_monitoring(&self) -> bool {
        true
    }
}

#[derive(Debug, Default)]
struct PermissionCacheState {
    epoch: u64,
    entry: Option<(Instant, PermissionSnapshot)>,
}

/// Wraps any [`PermissionsChecker`] with a short-lived snapshot cache to avoid redundant
/// TCC queries during UI refresh bursts (`utils/permissions.py::get_permission_signature`).
pub struct CachedPermissionsChecker<P: PermissionsChecker> {
    inner: P,
    ttl: Duration,
    cache: Mutex<PermissionCacheState>,
}

impl<P: PermissionsChecker> CachedPermissionsChecker<P> {
    pub fn new(inner: P) -> Self {
        Self::with_ttl(inner, PERMISSION_SNAPSHOT_TTL)
    }

    pub fn with_ttl(inner: P, ttl: Duration) -> Self {
        Self {
            inner,
            ttl,
            cache: Mutex::new(PermissionCacheState::default()),
        }
    }

    /// Clears the cached snapshot so the next query hits the underlying OS adapter.
    pub fn invalidate(&self) {
        let mut guard = lock_unpoisoned(&self.cache);
        guard.epoch = guard.epoch.wrapping_add(1);
        guard.entry = None;
    }

    /// Returns a cached snapshot if younger than `ttl`, refreshing from `inner` otherwise.
    pub fn cached_snapshot(&self) -> PermissionSnapshot {
        if self.ttl.is_zero() {
            return self.inner.snapshot();
        }
        let now = Instant::now();
        let epoch_before = {
            let guard = lock_unpoisoned(&self.cache);
            if let Some((captured_at, snapshot)) = guard.entry {
                if now.saturating_duration_since(captured_at) < self.ttl {
                    return snapshot;
                }
            }
            guard.epoch
        };
        let snapshot = self.inner.snapshot();
        let mut guard = lock_unpoisoned(&self.cache);
        if guard.epoch == epoch_before {
            guard.entry = Some((Instant::now(), snapshot));
        }
        snapshot
    }
}

impl<P: PermissionsChecker> PermissionsChecker for CachedPermissionsChecker<P> {
    fn microphone_status(&self) -> MicrophonePermissionStatus {
        self.cached_snapshot().microphone
    }

    fn has_accessibility(&self) -> bool {
        self.cached_snapshot().accessibility
    }

    fn has_input_monitoring(&self) -> bool {
        self.cached_snapshot().input_monitoring
    }

    fn snapshot(&self) -> PermissionSnapshot {
        self.cached_snapshot()
    }

    fn request_permission(&self, kind: PermissionKind) -> Result<(), PlatformError> {
        self.invalidate();
        self.inner.request_permission(kind)
    }

    fn open_privacy_settings(&self, kind: PermissionKind) -> Result<(), PlatformError> {
        self.invalidate();
        self.inner.open_privacy_settings(kind)
    }
}

/// Best-effort check whether a user-facing error message relates to missing OS permissions.
///
/// Mirrors `utils/permissions.py::is_permission_related_message`.
pub fn is_permission_related_message(message: &str) -> bool {
    let lower = message.to_lowercase();
    PERMISSION_MESSAGE_TOKENS
        .iter()
        .any(|token| lower.contains(token))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct StubPermissions {
        snapshot: Mutex<PermissionSnapshot>,
        calls: AtomicUsize,
    }

    impl StubPermissions {
        fn new(snapshot: PermissionSnapshot) -> Self {
            Self {
                snapshot: Mutex::new(snapshot),
                calls: AtomicUsize::new(0),
            }
        }
    }

    impl PermissionsChecker for StubPermissions {
        fn microphone_status(&self) -> MicrophonePermissionStatus {
            self.snapshot().microphone
        }
        fn has_accessibility(&self) -> bool {
            self.snapshot().accessibility
        }
        fn has_input_monitoring(&self) -> bool {
            self.snapshot().input_monitoring
        }
        fn snapshot(&self) -> PermissionSnapshot {
            self.calls.fetch_add(1, Ordering::SeqCst);
            *lock_unpoisoned(&self.snapshot)
        }
    }

    #[test]
    fn permission_kinds_and_message_detection_match_python() {
        assert_eq!(
            PermissionKind::Microphone.macos_settings_url(),
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
        );
        assert_eq!(
            PermissionKind::Accessibility.macos_settings_url(),
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
        );
        assert_eq!(
            PermissionKind::InputMonitoring.macos_settings_url(),
            "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
        );

        assert!(is_permission_related_message("Mikrofon-Zugriff verweigert"));
        assert!(is_permission_related_message("Missing Accessibility trust"));
        assert!(is_permission_related_message("Eingabemonitoring fehlt"));
        assert!(!is_permission_related_message("Network timeout"));
    }

    #[test]
    fn microphone_status_recording_allowance_matches_python() {
        assert!(MicrophonePermissionStatus::Authorized.is_recording_allowed());
        assert!(MicrophonePermissionStatus::NotDetermined.is_recording_allowed());
        assert!(!MicrophonePermissionStatus::Denied.is_recording_allowed());
        assert!(!MicrophonePermissionStatus::Restricted.is_recording_allowed());
        assert!(!MicrophonePermissionStatus::Unknown.is_recording_allowed());
    }

    #[test]
    fn cached_permissions_checker_reuses_snapshot_and_invalidates_on_request() {
        let stub = StubPermissions::new(PermissionSnapshot {
            microphone: MicrophonePermissionStatus::NotDetermined,
            accessibility: false,
            input_monitoring: true,
        });
        let cached = CachedPermissionsChecker::with_ttl(stub, Duration::from_secs(10));

        assert!(!cached.snapshot().all_ready());
        assert!(!cached.has_accessibility());
        assert!(cached.has_input_monitoring());
        assert_eq!(cached.inner.calls.load(Ordering::SeqCst), 1);

        *lock_unpoisoned(&cached.inner.snapshot) = PermissionSnapshot::all_granted_default();
        assert!(!cached.has_accessibility());

        cached
            .request_permission(PermissionKind::Accessibility)
            .unwrap();
        assert!(cached.has_accessibility());
        assert!(cached.snapshot().all_ready());
        assert_eq!(cached.inner.calls.load(Ordering::SeqCst), 2);

        // Zero TTL always bypasses caching.
        let uncached = CachedPermissionsChecker::with_ttl(
            StubPermissions::new(PermissionSnapshot::all_granted_default()),
            Duration::ZERO,
        );
        let _ = uncached.snapshot();
        let _ = uncached.snapshot();
        assert_eq!(uncached.inner.calls.load(Ordering::SeqCst), 2);
    }
}
