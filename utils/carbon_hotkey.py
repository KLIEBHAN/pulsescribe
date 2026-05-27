"""Safe Carbon hotkey registration for macOS.

We use quickmachotkey's Carbon bindings, but add two safeguards:

1) `RegisterEventHotKey` returns an OSStatus. If registration fails, we must not
   keep an invalid reference, otherwise later unregister can crash the process.
2) We should also remove the handler from quickmachotkey's global handler dict.
"""

from __future__ import annotations

from typing import Callable


def _carbon_hotkeys_unsupported_reason() -> str | None:
    try:
        import sys

        if sys.platform != "darwin":
            return "Carbon hotkeys are only supported on macOS"
    except Exception:
        return None
    return None


def _load_registration_dependencies():
    import quickmachotkey
    from quickmachotkey._MinimalHIToolbox import (  # type: ignore[attr-defined]
        GetEventDispatcherTarget,
        RegisterEventHotKey,
    )
    from struct import unpack

    return quickmachotkey, GetEventDispatcherTarget, RegisterEventHotKey, unpack


def _quickmachotkey_signature(quickmachotkey, unpack) -> int:
    qmhk = getattr(quickmachotkey, "_QMHK", None)
    if qmhk is not None:
        return qmhk
    try:
        (qmhk,) = unpack("@I", b"QMHK")
        return qmhk
    except Exception:  # pragma: no cover
        return 0


class CarbonHotKeyRegistration:
    """Register/unregister a single Carbon hotkey via quickmachotkey."""

    def __init__(self, *, virtual_key: int, modifier_mask: int, callback: Callable[[], None]):
        self._virtual_key = int(virtual_key)
        self._modifier_mask = int(modifier_mask)
        self._callback = callback
        self._hotkey_id: int | None = None
        self._ref = None

    @staticmethod
    def _call_on_main_sync(fn, *, timeout_s: float = 2.0):
        """Run `fn` on the main thread and wait for the result (best-effort)."""
        try:
            from Foundation import NSThread  # type: ignore[import-not-found]

            if NSThread.isMainThread():
                return True, fn()
        except Exception:
            return True, fn()

        try:
            from PyObjCTools import AppHelper  # type: ignore[import-not-found]
        except Exception:
            # No dispatcher available – run directly (best-effort).
            return True, fn()

        import threading

        done = threading.Event()
        box: dict[str, object] = {}

        def run() -> None:
            try:
                box["result"] = fn()
            except Exception as e:  # pragma: no cover
                box["error"] = e
            finally:
                done.set()

        AppHelper.callAfter(run)
        if not done.wait(timeout_s):
            return False, TimeoutError("Timed out waiting for main thread")
        if "error" in box:
            raise box["error"]  # type: ignore[misc]
        return True, box.get("result")

    def register(self) -> tuple[bool, str | None]:
        """Registers the hotkey. Returns (ok, error_message)."""
        try:
            ok, out = self._call_on_main_sync(self._register_impl)
        except Exception as e:  # pragma: no cover
            return False, str(e)

        if not ok:
            return False, str(out)
        if isinstance(out, tuple):
            return out  # type: ignore[return-value]
        return False, "Hotkey registration failed"

    def _register_impl(self) -> tuple[bool, str | None]:
        unsupported_reason = _carbon_hotkeys_unsupported_reason()
        if unsupported_reason is not None:
            return False, unsupported_reason

        try:
            quickmachotkey, get_target, register_hotkey, unpack = (
                _load_registration_dependencies()
            )
        except Exception as e:  # pragma: no cover
            return False, f"quickmachotkey unavailable: {e}"

        qmhk = _quickmachotkey_signature(quickmachotkey, unpack)
        return self._register_with_quickmachotkey(
            quickmachotkey,
            get_target,
            register_hotkey,
            qmhk,
        )

    def _register_with_quickmachotkey(
        self,
        quickmachotkey,
        get_target,
        register_hotkey,
        qmhk: int,
    ) -> tuple[bool, str | None]:
        hkid = None
        try:
            quickmachotkey.registrationCounter += 1
            hkid = quickmachotkey.registrationCounter
            quickmachotkey.hotKeyHandlers[hkid] = self._callback
            result, ref = register_hotkey(
                self._virtual_key,
                self._modifier_mask,
                (qmhk, hkid),
                get_target(),
                0,
                None,
            )
        except Exception as e:
            self._remove_quickmachotkey_handler(quickmachotkey, hkid)
            return False, str(e)

        return self._finalize_registration_result(quickmachotkey, hkid, result, ref)

    def _finalize_registration_result(
        self,
        quickmachotkey,
        hkid: int | None,
        result,
        ref,
    ) -> tuple[bool, str | None]:
        if int(result) != 0 or ref is None:
            self._remove_quickmachotkey_handler(quickmachotkey, hkid)
            return False, f"RegisterEventHotKey failed (OSStatus={int(result)})"
        if hkid is None:
            return False, "RegisterEventHotKey failed (missing hotkey id)"

        self._hotkey_id = int(hkid)
        self._ref = ref
        return True, None

    @staticmethod
    def _remove_quickmachotkey_handler(quickmachotkey, hkid: int | None) -> None:
        if hkid is None:
            return
        try:
            quickmachotkey.hotKeyHandlers.pop(hkid, None)
        except Exception:
            pass

    def unregister(self) -> None:
        """Unregisters the hotkey (best-effort)."""
        def _impl() -> None:
            try:
                import quickmachotkey
                from quickmachotkey._MinimalHIToolbox import (  # type: ignore[attr-defined]
                    UnregisterEventHotKey,
                )
            except Exception:
                quickmachotkey = None  # type: ignore[assignment]
                UnregisterEventHotKey = None  # type: ignore[assignment]

            ref = self._ref
            self._ref = None
            hkid = self._hotkey_id
            self._hotkey_id = None

            if quickmachotkey is not None and hkid is not None:
                try:
                    quickmachotkey.hotKeyHandlers.pop(int(hkid), None)
                except Exception:
                    pass

            if ref is not None and UnregisterEventHotKey is not None:
                try:
                    UnregisterEventHotKey(ref)
                except Exception:
                    pass

        try:
            self._call_on_main_sync(_impl)
        except Exception:
            pass


__all__ = ["CarbonHotKeyRegistration"]
