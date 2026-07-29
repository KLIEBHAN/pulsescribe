import threading
from unittest.mock import MagicMock, patch

from pulsescribe_daemon import PulseScribeDaemon


class _LocalProvider:
    _backend = "mlx"

    def __init__(self):
        self.keepalive = MagicMock()
        self.preload = MagicMock()

    def _ensure_runtime_config(self):
        return None

    def get_runtime_info(self):
        return {"device": "mps", "compute_type": None}


def _prime_generation(daemon: PulseScribeDaemon):
    signature = daemon._local_provider_memory_signature()
    with daemon._local_warm_lock:
        daemon._local_warm_generation = 1
        daemon._local_preload_signature = signature
    return signature


def test_keepalive_start_is_singleton_for_generation():
    daemon = PulseScribeDaemon(mode="local")
    provider = _LocalProvider()
    signature = _prime_generation(daemon)

    with patch("pulsescribe_daemon.LOCAL_KEEPALIVE_INTERVAL", 60.0):
        assert daemon._start_keepalive_timer(
            provider=provider,
            generation=1,
            signature=signature,
            model="large",
        )
        first_thread = daemon._keepalive_thread
        assert not daemon._start_keepalive_timer(
            provider=provider,
            generation=1,
            signature=signature,
            model="large",
        )
        assert daemon._keepalive_thread is first_thread
        assert daemon._stop_keepalive_timer(wait=True)

    assert daemon._keepalive_thread is None
    provider.keepalive.assert_not_called()


def test_keepalive_stops_after_local_generation_is_cancelled():
    daemon = PulseScribeDaemon(mode="local")
    provider = _LocalProvider()
    signature = _prime_generation(daemon)
    called = threading.Event()
    provider.keepalive.side_effect = lambda _model: called.set()

    with patch("pulsescribe_daemon.LOCAL_KEEPALIVE_INTERVAL", 0.01):
        assert daemon._start_keepalive_timer(
            provider=provider,
            generation=1,
            signature=signature,
            model="large",
        )
        assert called.wait(timeout=1.0)
        calls_before_cancel = provider.keepalive.call_count
        daemon.mode = "openai"
        keepalive_thread = daemon._keepalive_thread
        daemon._cancel_local_warm_lifecycle()
        assert keepalive_thread is not None
        keepalive_thread.join(timeout=1.0)
        assert not keepalive_thread.is_alive()

    assert daemon._keepalive_thread is None
    assert provider.keepalive.call_count == calls_before_cancel


def test_blocked_old_keepalive_starts_pending_generation_after_exit():
    daemon = PulseScribeDaemon(mode="local")
    old_provider = _LocalProvider()
    new_provider = _LocalProvider()
    old_started = threading.Event()
    release_old = threading.Event()
    new_started = threading.Event()
    old_provider.keepalive.side_effect = lambda _model: (
        old_started.set(),
        release_old.wait(timeout=2.0),
    )
    new_provider.keepalive.side_effect = lambda _model: new_started.set()

    old_signature = _prime_generation(daemon)
    with patch("pulsescribe_daemon.LOCAL_KEEPALIVE_INTERVAL", 0.01):
        assert daemon._start_keepalive_timer(
            provider=old_provider,
            generation=1,
            signature=old_signature,
            model="large",
        )
        assert old_started.wait(timeout=1.0)

        daemon.model = "turbo"
        new_signature = daemon._local_provider_memory_signature()
        with daemon._local_warm_lock:
            daemon._local_warm_generation = 2
            daemon._local_preload_signature = new_signature
        assert not daemon._stop_keepalive_timer()
        assert not daemon._start_keepalive_timer(
            provider=new_provider,
            generation=2,
            signature=new_signature,
            model="turbo",
        )

        release_old.set()
        assert new_started.wait(timeout=1.0)
        daemon._cancel_local_warm_lifecycle(wait=True)

    assert old_provider.keepalive.call_count == 1
    assert new_provider.keepalive.call_count >= 1
    assert daemon._keepalive_thread is None


def test_stale_keepalive_start_cannot_overwrite_current_pending_request():
    daemon = PulseScribeDaemon(mode="local")
    active_provider = _LocalProvider()
    stale_provider = _LocalProvider()
    current_provider = _LocalProvider()
    stale_prepare_started = threading.Event()
    release_stale_prepare = threading.Event()

    old_signature = _prime_generation(daemon)
    with patch("pulsescribe_daemon.LOCAL_KEEPALIVE_INTERVAL", 60.0):
        assert daemon._start_keepalive_timer(
            provider=active_provider,
            generation=1,
            signature=old_signature,
            model="large",
        )

        def block_stale_prepare():
            stale_prepare_started.set()
            release_stale_prepare.wait(timeout=2.0)

        stale_provider._ensure_runtime_config = block_stale_prepare
        stale_result = []
        stale_thread = threading.Thread(
            target=lambda: stale_result.append(
                daemon._start_keepalive_timer(
                    provider=stale_provider,
                    generation=1,
                    signature=old_signature,
                    model="large",
                )
            )
        )
        stale_thread.start()
        assert stale_prepare_started.wait(timeout=1.0)

        daemon.model = "turbo"
        current_signature = daemon._local_provider_memory_signature()
        with daemon._local_warm_lock:
            daemon._local_warm_generation = 2
            daemon._local_preload_signature = current_signature
        assert not daemon._start_keepalive_timer(
            provider=current_provider,
            generation=2,
            signature=current_signature,
            model="turbo",
        )

        release_stale_prepare.set()
        stale_thread.join(timeout=1.0)
        assert stale_result == [False]
        assert daemon._pending_keepalive_request is not None
        assert daemon._pending_keepalive_request.provider is current_provider

        assert daemon._stop_keepalive_timer(wait=True)
        assert daemon._keepalive_request is not None
        assert daemon._keepalive_request.provider is current_provider
        daemon._cancel_local_warm_lifecycle(wait=True)


def test_identical_preload_is_not_started_twice():
    daemon = PulseScribeDaemon(mode="local", model="large")
    provider = _LocalProvider()
    started = threading.Event()
    release = threading.Event()

    def preload(_model):
        started.set()
        assert release.wait(timeout=2.0)

    provider.preload.side_effect = preload

    with (
        patch.object(daemon, "_get_provider", return_value=provider),
        patch("pulsescribe_daemon.LOCAL_KEEPALIVE_INTERVAL", 0.0),
        patch("pulsescribe_daemon.get_sound_player"),
    ):
        assert daemon._preload_local_model_async()
        assert started.wait(timeout=1.0)
        assert not daemon._preload_local_model_async()
        release.set()
        preload_thread = daemon._local_preload_thread
        assert preload_thread is not None
        preload_thread.join(timeout=2.0)

    provider.preload.assert_called_once_with("large")
    assert daemon._local_preload_complete.is_set()


def test_stale_preload_cannot_publish_ready_or_start_keepalive():
    daemon = PulseScribeDaemon(mode="local", model="large")
    provider = _LocalProvider()
    started = threading.Event()
    release = threading.Event()

    def preload(_model):
        started.set()
        assert release.wait(timeout=2.0)

    provider.preload.side_effect = preload

    with (
        patch.object(daemon, "_get_provider", return_value=provider),
        patch.object(daemon, "_start_keepalive_timer") as start_keepalive,
        patch("pulsescribe_daemon.get_sound_player") as get_sound_player,
    ):
        assert daemon._preload_local_model_async()
        assert started.wait(timeout=1.0)
        preload_thread = daemon._local_preload_thread
        daemon.mode = "openai"
        daemon._cancel_local_warm_lifecycle()
        release.set()
        assert preload_thread is not None
        preload_thread.join(timeout=2.0)

    assert not daemon._local_preload_complete.is_set()
    start_keepalive.assert_not_called()
    get_sound_player.return_value.play.assert_not_called()


def test_model_cache_release_runs_off_thread_and_preloads_after_completion():
    daemon = PulseScribeDaemon(mode="local", model="large")
    provider = MagicMock()
    release_started = threading.Event()
    allow_release = threading.Event()

    def clear_model_cache():
        release_started.set()
        assert allow_release.wait(timeout=2.0)

    provider.clear_model_cache.side_effect = clear_model_cache
    daemon._provider_cache["local"] = provider
    _prime_generation(daemon)

    with patch.object(daemon, "_preload_local_model_async") as preload:
        reconcile_thread = daemon._release_local_provider_model_cache_async(
            generation=1,
            preload_after=True,
        )
        assert reconcile_thread is not None
        assert release_started.wait(timeout=1.0)
        preload.assert_not_called()
        allow_release.set()
        reconcile_thread.join(timeout=2.0)

    provider.clear_model_cache.assert_called_once_with()
    preload.assert_called_once_with()


def test_only_latest_queued_cache_reconcile_clears_then_preloads():
    daemon = PulseScribeDaemon(mode="local", model="large")
    provider = MagicMock()
    first_clear_started = threading.Event()
    allow_first_clear = threading.Event()
    clear_calls = 0

    def clear_model_cache():
        nonlocal clear_calls
        clear_calls += 1
        if clear_calls == 1:
            first_clear_started.set()
            assert allow_first_clear.wait(timeout=2.0)

    provider.clear_model_cache.side_effect = clear_model_cache
    daemon._provider_cache["local"] = provider
    with daemon._local_warm_lock:
        daemon._local_warm_generation = 1

    with patch.object(daemon, "_preload_local_model_async") as preload:
        first = daemon._release_local_provider_model_cache_async(
            generation=1,
            preload_after=True,
        )
        assert first is not None
        assert first_clear_started.wait(timeout=1.0)

        with daemon._local_warm_lock:
            daemon._local_warm_generation = 2
        second = daemon._release_local_provider_model_cache_async(
            generation=2,
            preload_after=True,
        )
        with daemon._local_warm_lock:
            daemon._local_warm_generation = 3
        third = daemon._release_local_provider_model_cache_async(
            generation=3,
            preload_after=True,
        )
        assert second is not None and third is not None

        allow_first_clear.set()
        for thread in (first, second, third):
            thread.join(timeout=2.0)

    assert provider.clear_model_cache.call_count == 2
    preload.assert_called_once_with()


def _reload_with_runtime_change(daemon, change_runtime):
    with (
        patch("pulsescribe_daemon.load_environment"),
        patch("utils.preferences.read_env_file", return_value={}),
        patch.object(daemon, "_sync_reload_env_values"),
        patch.object(daemon, "_apply_reloaded_hotkey_settings"),
        patch.object(
            daemon,
            "_apply_reloaded_runtime_settings",
            side_effect=lambda _values: change_runtime(),
        ),
        patch.object(daemon, "_resolve_hotkey_bindings", return_value=[]),
        patch.object(daemon, "_invalidate_local_provider_runtime_config"),
        patch.object(
            daemon,
            "_release_local_provider_model_cache_async",
        ) as release_cache_async,
        patch.object(daemon, "_cancel_local_warm_lifecycle") as cancel_warm,
        patch.object(daemon, "_preload_local_model_async") as preload,
    ):
        daemon._reload_settings()
    return release_cache_async, cancel_warm, preload


def test_unrelated_settings_reload_keeps_ready_model():
    daemon = PulseScribeDaemon(mode="local", model="large", language="de")
    signature = daemon._local_provider_memory_signature()
    daemon._local_preload_signature = signature
    daemon._local_preload_complete.set()

    release_cache, cancel_warm, preload = _reload_with_runtime_change(
        daemon,
        lambda: setattr(daemon, "language", "en"),
    )

    release_cache.assert_not_called()
    cancel_warm.assert_not_called()
    preload.assert_not_called()


def test_model_change_restarts_local_warm_generation_once():
    daemon = PulseScribeDaemon(mode="local", model="large")
    daemon._local_preload_signature = daemon._local_provider_memory_signature()
    daemon._local_preload_complete.set()

    release_cache, cancel_warm, preload = _reload_with_runtime_change(
        daemon,
        lambda: setattr(daemon, "model", "turbo"),
    )

    release_cache.assert_called_once_with(generation=0, preload_after=True)
    cancel_warm.assert_called_once_with()
    preload.assert_not_called()


def test_leaving_local_stops_warm_lifecycle_without_preload():
    daemon = PulseScribeDaemon(mode="local", model="large")
    daemon._local_preload_signature = daemon._local_provider_memory_signature()
    daemon._local_preload_complete.set()

    release_cache, cancel_warm, preload = _reload_with_runtime_change(
        daemon,
        lambda: setattr(daemon, "mode", "openai"),
    )

    release_cache.assert_called_once_with(generation=0, preload_after=False)
    cancel_warm.assert_called_once_with()
    preload.assert_not_called()
