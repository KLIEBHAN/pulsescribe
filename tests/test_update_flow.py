import threading
from dataclasses import replace
from unittest.mock import Mock

import pytest

from utils import update_flow
from utils.update_install import PreparedUpdate
from utils.updates import UpdateOffer


@pytest.fixture
def flow(monkeypatch, tmp_path):
    monkeypatch.setattr(update_flow, "get_app_version", lambda: "1.3.0")
    monkeypatch.setattr(update_flow.sys, "platform", "darwin")
    monkeypatch.setattr(update_flow, "can_replace_mac", lambda _target: True)
    monkeypatch.setattr(
        update_flow.tempfile, "mkdtemp", lambda **_kwargs: str(tmp_path / "download")
    )
    (tmp_path / "download").mkdir()
    states = []
    instance = update_flow.UpdateFlow(tmp_path / "PulseScribe.app", states.append)
    yield instance, states
    instance.stop()


def wait_for_worker(flow):
    assert flow._lock.acquire(timeout=2)
    flow._lock.release()


def test_check_is_nonblocking_and_deduplicated(flow, monkeypatch):
    instance, states = flow
    started, release = threading.Event(), threading.Event()
    calls = []

    def check(*_args, **_kwargs):
        calls.append(1)
        started.set()
        assert release.wait(2)
        return None

    monkeypatch.setattr(update_flow, "check_release", check)
    instance.check(manual=True)
    assert started.wait(2)
    instance.check(manual=True)
    release.set()
    wait_for_worker(instance)
    assert calls == [1]
    assert [state.status for state in states] == ["checking", "idle"]
    assert states[-1].manual


def test_automatic_download_ready_survives_further_checks(flow, monkeypatch, tmp_path):
    instance, states = flow
    offer = UpdateOffer("1.4.0", "Notes", "PulseScribe-1.4.0.dmg", 10, "a" * 64)
    check = Mock(return_value=offer)
    monkeypatch.setattr(update_flow, "check_release", check)
    monkeypatch.setattr(
        update_flow,
        "download_update",
        lambda _offer, _path, progress, _cancel: progress(50),
    )
    staged = tmp_path / "staged"
    staged.mkdir()
    prepared = PreparedUpdate(
        staged / "PulseScribe.app", instance.target, staged, "darwin", True
    )
    monkeypatch.setattr(
        update_flow, "prepare_update", lambda *_args, **_kwargs: prepared
    )
    instance.check()
    wait_for_worker(instance)
    assert instance.state.status == "ready"
    assert instance.state.offer == offer
    assert not any(state.manual for state in states)
    instance.check(manual=True)
    instance.download()
    assert check.call_count == 1
    assert instance.prepared is prepared
    instance.stop()
    assert not staged.exists()


def test_unwritable_mac_does_not_download_in_background(flow, monkeypatch):
    instance, _states = flow
    monkeypatch.setattr(update_flow, "can_replace_mac", lambda _target: False)
    monkeypatch.setattr(
        update_flow,
        "check_release",
        lambda *_args, **_kwargs: UpdateOffer("1.4.0", "", "installer", 10, "a" * 64),
    )
    download = Mock()
    monkeypatch.setattr(update_flow, "download_update", download)
    instance.check()
    wait_for_worker(instance)
    assert instance.state.status == "available"
    download.assert_not_called()


def test_background_failure_can_be_retried_explicitly(flow, monkeypatch):
    instance, _states = flow
    offer = UpdateOffer("1.4.0", "", "installer", 10, "a" * 64)
    monkeypatch.setattr(update_flow, "check_release", lambda *_args, **_kwargs: offer)
    monkeypatch.setattr(
        update_flow, "download_update", Mock(side_effect=OSError("offline"))
    )
    instance.check()
    wait_for_worker(instance)
    assert instance.state.status == "error"
    assert instance.state.error_context == "download"
    assert not instance.state.manual
    instance.download()
    wait_for_worker(instance)
    assert instance.state.manual
    assert instance.state.offer == offer


def test_shutdown_cancels_download_and_never_prepares(flow, monkeypatch):
    instance, states = flow
    started = threading.Event()
    monkeypatch.setattr(
        update_flow,
        "check_release",
        lambda *_args, **_kwargs: UpdateOffer("1.4.0", "", "installer", 10, "a" * 64),
    )

    def download(_offer, _path, _progress, cancelled):
        started.set()
        assert instance._stopped.wait(2)
        assert cancelled()

    prepare = Mock()
    monkeypatch.setattr(update_flow, "download_update", download)
    monkeypatch.setattr(update_flow, "prepare_update", prepare)
    instance.check()
    assert started.wait(2)
    instance.stop()
    wait_for_worker(instance)
    assert not instance._worker.is_alive()
    assert instance._work is None
    prepare.assert_not_called()
    assert all(state.status != "ready" for state in states)


def test_install_retains_helper_files_until_process_exit(flow, monkeypatch, tmp_path):
    instance, _states = flow
    work = tmp_path / "ready"
    work.mkdir()
    instance._work = work
    instance.state = replace(instance.state, status="ready")
    prepared = Mock()
    instance.prepared = prepared
    instance.install()
    instance.stop()
    prepared.launch.assert_called_once()
    assert work.exists()


def test_install_is_reserved_once_and_failed_launch_can_retry(flow):
    instance, _states = flow
    instance.state = replace(instance.state, status="ready")
    instance.prepared = Mock(automatic=True)
    instance.prepared.launch.side_effect = [OSError("no helper"), None]
    with pytest.raises(OSError):
        instance.install()
    instance.install()
    with pytest.raises(ValueError, match="already starting"):
        instance.install()
    assert instance.prepared.launch.call_count == 2


def test_manual_installer_does_not_retain_temporary_work(flow, tmp_path):
    instance, _states = flow
    work = tmp_path / "manual"
    work.mkdir()
    instance._work = work
    instance.state = replace(instance.state, status="ready")
    instance.prepared = Mock(automatic=False)
    instance.install()
    instance.stop()
    assert not work.exists()


def test_concurrent_installs_launch_exactly_one_helper(flow):
    instance, _states = flow
    started, release = threading.Event(), threading.Event()
    errors = []
    instance.state = replace(instance.state, status="ready")
    instance.prepared = Mock(automatic=True)

    def launch():
        started.set()
        assert release.wait(2)

    instance.prepared.launch.side_effect = launch

    def install():
        try:
            instance.install()
        except ValueError as error:
            errors.append(str(error))

    first = threading.Thread(target=install)
    second = threading.Thread(target=install)
    first.start()
    assert started.wait(2)
    second.start()
    release.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert not first.is_alive() and not second.is_alive()
    instance.prepared.launch.assert_called_once()
    assert errors == ["Update installation is already starting."]


def test_timer_uses_hourly_events_and_stops(flow, monkeypatch):
    instance, _states = flow
    timers = []

    def timer(seconds, callback):
        result = Mock(seconds=seconds, callback=callback)
        timers.append(result)
        return result

    monkeypatch.setattr(update_flow.threading, "Timer", timer)
    monkeypatch.setattr(instance, "check", Mock())
    instance.start()
    assert timers[-1].seconds == 15
    timers[-1].callback()
    assert timers[-1].seconds == 3600
    instance.stop()
    timers[-1].cancel.assert_called_once()
    instance._schedule(15)
    assert len(timers) == 2


def test_install_requires_verified_ready_state(flow):
    with pytest.raises(ValueError, match="verified"):
        flow[0].install()
