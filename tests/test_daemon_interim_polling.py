import sys
from unittest.mock import MagicMock, patch

from pulsescribe_daemon import INTERIM_POLL_MAX_CHARS, PulseScribeDaemon
from utils.state import AppState


def test_interim_polling_reads_tail_once_per_file_change():
    daemon = PulseScribeDaemon(mode="deepgram")
    daemon._overlay = MagicMock()
    daemon._current_state = AppState.RECORDING
    daemon._last_interim_mtime = 0.0

    mock_foundation = MagicMock()
    mock_timer_cls = MagicMock()
    mock_foundation.NSTimer = mock_timer_cls

    fake_interim_file = MagicMock()
    fake_interim_file.stat.return_value.st_mtime = 42.0

    with (
        patch.dict(sys.modules, {"Foundation": mock_foundation}),
        patch("pulsescribe_daemon.INTERIM_FILE", fake_interim_file),
        patch(
            "pulsescribe_daemon.read_file_tail_text",
            return_value="  latest words  ",
        ) as read_tail,
    ):
        daemon._start_interim_polling()
        callback = mock_timer_cls.scheduledTimerWithTimeInterval_repeats_block_.call_args[
            0
        ][2]

        callback(None)
        callback(None)

    read_tail.assert_called_once_with(
        fake_interim_file,
        max_chars=INTERIM_POLL_MAX_CHARS,
        errors="replace",
    )
    daemon._overlay.update_state.assert_called_once_with(
        AppState.RECORDING,
        "latest words",
    )
