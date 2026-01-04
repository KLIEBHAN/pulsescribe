"""State-Management für Hold-to-Record Hotkeys.

Kapselt die Logik für multiple Hold-Quellen (z.B. mehrere Tasten
oder pynput + native Handler gleichzeitig).
"""


class HoldHotkeyState:
    """Manages state for hold-to-record hotkey behavior.

    Handles the case where multiple sources (keyboard keys, event handlers)
    can trigger the same hold action. Recording only stops when ALL sources
    are released.

    Usage:
        hold_state = HoldHotkeyState()

        # On key press:
        if hold_state.should_start(source_id):
            start_recording()
            if recording_actually_started:
                hold_state.mark_started()

        # On key release:
        if hold_state.should_stop(source_id):
            stop_recording()
            hold_state.reset()
    """

    __slots__ = ("active_sources", "started_by_hold")

    def __init__(self):
        self.active_sources: set[str] = set()
        self.started_by_hold: bool = False

    def should_start(self, source_id: str) -> bool:
        """Check if recording should start on key press.

        Returns True if this is a new source (first press).
        Automatically adds source to active set.
        """
        if source_id in self.active_sources:
            return False
        self.active_sources.add(source_id)
        return True

    def should_stop(self, source_id: str) -> bool:
        """Check if recording should stop on key release.

        Returns True if:
        - No active hold sources remain
        - Recording was started by hold

        Automatically removes source from active set.
        """
        self.active_sources.discard(source_id)
        return not self.active_sources and self.started_by_hold

    def is_active(self, source_id: str) -> bool:
        """Check if source is still active (for race condition check)."""
        return source_id in self.active_sources

    def mark_started(self):
        """Mark that recording was started by hold."""
        self.started_by_hold = True

    def reset(self):
        """Reset after recording ends."""
        self.clear()

    def clear(self):
        """Full reset (cleanup)."""
        self.active_sources.clear()
        self.started_by_hold = False
