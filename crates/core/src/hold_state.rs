//! Thread-safe state management for multi-source hold-to-record hotkeys.
//!
//! Mirrors `utils/hold_state.py`: tracks active hold sources so recording only
//! stops once the last active source has been released and only if recording was
//! actually started by a hold event.

use std::collections::HashSet;
use std::sync::Mutex;

#[derive(Debug, Default)]
struct HoldStateInner {
    active_sources: HashSet<String>,
    started_by_hold: bool,
}

/// Manages active hold-to-record sources across concurrent keyboard handlers.
#[derive(Debug, Default)]
pub struct HoldHotkeyState {
    inner: Mutex<HoldStateInner>,
}

impl HoldHotkeyState {
    pub fn new() -> Self {
        Self::default()
    }

    /// Register a pressed hold source.
    ///
    /// Returns `true` when `source_id` was not already active (first press).
    pub fn should_start(&self, source_id: &str) -> bool {
        let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        guard.active_sources.insert(source_id.to_string())
    }

    /// Release a hold source and report whether recording should stop.
    ///
    /// Returns `true` only when no active sources remain AND `mark_started()` was called.
    pub fn should_stop(&self, source_id: &str) -> bool {
        let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        guard.active_sources.remove(source_id);
        guard.active_sources.is_empty() && guard.started_by_hold
    }

    /// Check whether `source_id` is currently active (guards against press/release races).
    pub fn is_active(&self, source_id: &str) -> bool {
        let guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        guard.active_sources.contains(source_id)
    }

    /// Mark that recording was actually started by hold.
    pub fn mark_started(&self) {
        let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        guard.started_by_hold = true;
    }

    /// Return whether recording is currently marked as started by hold.
    pub fn started_by_hold(&self) -> bool {
        let guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        guard.started_by_hold
    }

    /// Return the number of currently active hold sources.
    pub fn active_source_count(&self) -> usize {
        let guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        guard.active_sources.len()
    }

    /// Reset all state after recording ends (alias for `clear`).
    pub fn reset(&self) {
        self.clear();
    }

    /// Clear all active sources and reset `started_by_hold`.
    pub fn clear(&self) {
        let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        guard.active_sources.clear();
        guard.started_by_hold = false;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    use std::thread;

    #[test]
    fn requires_mark_started_and_waits_for_last_source_release() {
        let state = HoldHotkeyState::new();

        assert!(state.should_start("quartz"));
        assert!(!state.should_start("quartz"));
        assert!(state.is_active("quartz"));
        // Released before mark_started -> does not trigger stop.
        assert!(!state.should_stop("quartz"));
        assert!(!state.is_active("quartz"));

        assert!(state.should_start("quartz"));
        state.mark_started();
        assert!(state.started_by_hold());
        assert!(state.should_start("pynput"));
        assert_eq!(state.active_source_count(), 2);

        assert!(!state.should_stop("quartz"));
        assert!(state.should_stop("pynput"));
        state.reset();
        assert!(!state.started_by_hold());
        assert_eq!(state.active_source_count(), 0);
        assert!(!state.should_stop("pynput"));
    }

    #[test]
    fn concurrent_press_and_release_stays_consistent() {
        let state = Arc::new(HoldHotkeyState::new());
        state.mark_started();

        let handles: Vec<_> = (0..8)
            .map(|id| {
                let s = Arc::clone(&state);
                thread::spawn(move || {
                    let src = format!("src-{id}");
                    for _ in 0..50 {
                        let _ = s.should_start(&src);
                        let _ = s.is_active(&src);
                        let _ = s.should_stop(&src);
                    }
                })
            })
            .collect();

        for handle in handles {
            handle.join().unwrap();
        }
        assert_eq!(state.active_source_count(), 0);
    }
}
