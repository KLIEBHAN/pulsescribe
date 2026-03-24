"""Tests für utils/permissions.py."""

from __future__ import annotations

import ctypes
import ctypes.util

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
