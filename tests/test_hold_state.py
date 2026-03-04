import threading

from utils.hold_state import HoldHotkeyState


def test_hold_state_requires_mark_started_for_stop():
    state = HoldHotkeyState()

    assert state.should_start("src")
    assert not state.should_stop("src")


def test_hold_state_stops_only_after_last_source_released():
    state = HoldHotkeyState()

    assert state.should_start("a")
    assert state.should_start("b")
    state.mark_started()

    assert not state.should_stop("a")
    assert state.should_stop("b")


def test_hold_state_concurrent_access_remains_consistent():
    state = HoldHotkeyState()
    errors: list[Exception] = []

    def worker(source_id: str) -> None:
        try:
            for i in range(500):
                state.should_start(source_id)
                state.mark_started()
                state.should_stop(source_id)
                if i % 25 == 0:
                    state.reset()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(f"src-{i % 3}",))
        for i in range(6)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    state.clear()
    assert state.active_sources == set()
    assert state.started_by_hold is False
