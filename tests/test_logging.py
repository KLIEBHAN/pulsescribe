from __future__ import annotations

import logging

import config
import utils.logging as logging_mod


def _reset_logging_state() -> None:
    for handler in list(logging_mod.logger.handlers):
        logging_mod.logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    logging_mod.logger.setLevel(logging.NOTSET)
    logging_mod._session_id = ""
    logging_mod._fallback_stderr_handler = None
    logging_mod._debug_stderr_handler = None



def test_setup_logging_reconfigures_debug_stderr_handler(tmp_path, monkeypatch) -> None:
    _reset_logging_state()
    monkeypatch.setattr(config, "LOG_FILE", tmp_path / "pulsescribe.log")

    try:
        logging_mod.setup_logging(debug=False)
        assert logging_mod.logger.level == logging.INFO
        assert logging_mod._debug_stderr_handler is None
        assert len(logging_mod.logger.handlers) == 1

        logging_mod.setup_logging(debug=True)
        assert logging_mod.logger.level == logging.DEBUG
        assert logging_mod._debug_stderr_handler is not None
        assert len(logging_mod.logger.handlers) == 2

        logging_mod.setup_logging(debug=False)
        assert logging_mod.logger.level == logging.INFO
        assert logging_mod._debug_stderr_handler is None
        assert len(logging_mod.logger.handlers) == 1
    finally:
        _reset_logging_state()



def test_setup_logging_does_not_duplicate_stderr_when_file_handler_is_unavailable(
    tmp_path, monkeypatch
) -> None:
    _reset_logging_state()
    monkeypatch.setattr(config, "LOG_FILE", tmp_path / "pulsescribe.log")
    monkeypatch.setattr(
        logging_mod,
        "RotatingFileHandler",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk blocked")),
    )

    try:
        logging_mod.setup_logging(debug=True)

        assert logging_mod.logger.level == logging.DEBUG
        assert logging_mod._fallback_stderr_handler is not None
        assert logging_mod._debug_stderr_handler is None
        assert len(logging_mod.logger.handlers) == 1
        assert logging_mod._fallback_stderr_handler.level == logging.DEBUG
    finally:
        _reset_logging_state()
