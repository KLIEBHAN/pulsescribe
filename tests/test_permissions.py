"""Tests für utils/permissions.py."""

from __future__ import annotations

import builtins
import ctypes
import ctypes.util
import sys

import utils.permissions as permissions


def test_get_app_services_returns_none_when_framework_lookup_fails(monkeypatch) -> None:
    monkeypatch.setattr(permissions.sys, "platform", "darwin")
    monkeypatch.setattr(ctypes.util, "find_library", lambda _name: None)
    monkeypatch.setattr(
        ctypes.cdll,
        "LoadLibrary",
        lambda _name: (_ for _ in ()).throw(AssertionError("LoadLibrary should not run")),
    )
    permissions._app_services = None

    try:
        assert permissions._get_app_services() is None
        assert permissions._app_services is None
    finally:
        permissions._app_services = None


def test_get_app_services_does_not_cache_invalid_handle(monkeypatch) -> None:
    monkeypatch.setattr(permissions.sys, "platform", "darwin")
    monkeypatch.setattr(
        ctypes.util,
        "find_library",
        lambda _name: "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices",
    )

    class FakeSymbol:
        pass

    class GoodLibrary:
        def __init__(self) -> None:
            self.AXIsProcessTrusted = FakeSymbol()

    broken_library = object()
    good_library = GoodLibrary()
    load_calls: list[str] = []

    def fake_load_library(_name: str):
        load_calls.append(_name)
        if len(load_calls) == 1:
            return broken_library
        return good_library

    monkeypatch.setattr(ctypes.cdll, "LoadLibrary", fake_load_library)
    permissions._app_services = None

    try:
        assert permissions._get_app_services() is None
        assert permissions._app_services is None

        assert permissions._get_app_services() is good_library
        assert permissions._app_services is good_library
        assert good_library.AXIsProcessTrusted.restype is ctypes.c_bool
        assert len(load_calls) == 2
    finally:
        permissions._app_services = None


def test_has_accessibility_permission_false_when_framework_unavailable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(permissions.sys, "platform", "darwin")
    monkeypatch.setattr(permissions, "_get_app_services", lambda: None)

    assert permissions.has_accessibility_permission() is False


def test_input_monitoring_checks_fail_closed_when_quartz_is_unavailable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(permissions.sys, "platform", "darwin")

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "Quartz":
            raise ImportError("Quartz missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert permissions.has_input_monitoring_permission() is False
    assert permissions.check_input_monitoring_permission(show_alert=False) is False


def test_check_microphone_permission_returns_false_for_unknown_state(monkeypatch) -> None:
    monkeypatch.setattr(permissions.sys, "platform", "darwin")
    monkeypatch.setattr(
        permissions,
        "get_microphone_permission_state",
        lambda: "unknown",
    )

    assert permissions.check_microphone_permission(show_alert=False) is False


def test_get_permission_signature_reuses_recent_snapshot(monkeypatch) -> None:
    calls = {"mic": 0, "access": 0, "input": 0}
    times = iter((100.0, 100.1, 100.4))

    monkeypatch.setattr(permissions.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(
        permissions,
        "get_microphone_permission_state",
        lambda: calls.__setitem__("mic", calls["mic"] + 1) or "authorized",
    )
    monkeypatch.setattr(
        permissions,
        "has_accessibility_permission",
        lambda: calls.__setitem__("access", calls["access"] + 1) or True,
    )
    monkeypatch.setattr(
        permissions,
        "has_input_monitoring_permission",
        lambda: calls.__setitem__("input", calls["input"] + 1) or False,
    )
    permissions.invalidate_permission_signature_cache()

    try:
        assert permissions.get_permission_signature() == ("authorized", True, False)
        assert permissions.get_permission_signature() == ("authorized", True, False)
        assert permissions.get_permission_signature() == ("authorized", True, False)
    finally:
        permissions.invalidate_permission_signature_cache()

    assert calls == {"mic": 2, "access": 2, "input": 2}


def test_check_microphone_permission_request_invalidates_permission_cache(
    monkeypatch,
) -> None:
    monkeypatch.setattr(permissions.sys, "platform", "darwin")
    permissions._permission_signature_cache = (123.0, ("authorized", True, True))
    requested: list[str] = []

    fake_avfoundation = type(
        "_FakeAVFoundation",
        (),
        {
            "AVCaptureDevice": type(
                "_FakeCaptureDevice",
                (),
                {
                    "requestAccessForMediaType_completionHandler_": staticmethod(
                        lambda _media_type, _callback: requested.append("requested")
                    )
                },
            ),
            "AVMediaTypeAudio": "audio",
        },
    )

    monkeypatch.setitem(sys.modules, "AVFoundation", fake_avfoundation)
    monkeypatch.setattr(
        permissions,
        "get_microphone_permission_state",
        lambda: "not_determined",
    )

    try:
        assert (
            permissions.check_microphone_permission(show_alert=False, request=True)
            is True
        )
        assert requested == ["requested"]
        assert permissions._permission_signature_cache is None
    finally:
        permissions.invalidate_permission_signature_cache()
        sys.modules.pop("AVFoundation", None)
