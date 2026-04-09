"""Hotkey validation helpers (UI-facing).

Used by Settings and the onboarding wizard to provide immediate feedback when a
hotkey is invalid, duplicated (toggle vs. hold), or blocked by macOS.
"""

from __future__ import annotations

ValidationResult = tuple[str, str, str | None]
_SPECIAL_HOTKEYS = {"fn", "capslock", "caps_lock"}


def _normalize(hotkey_str: str | None) -> str:
    return (hotkey_str or "").strip().lower()


def _get_current_hotkeys() -> tuple[str, str]:
    from utils.preferences import get_env_setting

    return (
        _normalize(get_env_setting("PULSESCRIBE_TOGGLE_HOTKEY")),
        _normalize(get_env_setting("PULSESCRIBE_HOLD_HOTKEY")),
    )


def _validate_against_current_hotkeys(
    kind: str,
    normalized: str,
    *,
    toggle_current: str,
    hold_current: str,
) -> ValidationResult | None:
    from utils.hotkey import hotkeys_conflict

    current = toggle_current if kind == "toggle" else hold_current
    other = hold_current if kind == "toggle" else toggle_current

    if other and normalized == other:
        return (
            normalized,
            "error",
            "Toggle und Hold dürfen nicht denselben Hotkey verwenden.",
        )

    if other and hotkeys_conflict(normalized, other):
        return (
            normalized,
            "error",
            "Toggle und Hold dürfen sich nicht überlappen.",
        )

    if current and normalized == current:
        return normalized, "ok", None

    return None


def _requires_input_monitoring(kind: str, normalized: str) -> bool:
    """Hold always needs Quartz; special toggle keys do as well."""
    return kind == "hold" or normalized in _SPECIAL_HOTKEYS


def _parse_hotkey_or_error(
    normalized: str,
) -> tuple[tuple[int, int] | None, ValidationResult | None]:
    from utils.hotkey import parse_hotkey

    try:
        return parse_hotkey(normalized), None
    except ValueError as exc:
        return None, (normalized, "error", str(exc))


def _validate_toggle_registration(
    normalized: str,
    *,
    virtual_key: int,
    modifier_mask: int,
    input_ok: bool,
) -> ValidationResult:
    """Validate that a toggle hotkey can register globally or fall back cleanly."""
    from utils.carbon_hotkey import CarbonHotKeyRegistration

    reg = CarbonHotKeyRegistration(
        virtual_key=virtual_key,
        modifier_mask=modifier_mask,
        callback=lambda: None,
    )
    ok, _err = reg.register()
    if ok:
        reg.unregister()
        return normalized, "ok", None

    if input_ok:
        return (
            normalized,
            "warning",
            "macOS blockiert diese Kombination als globalen Hotkey. PulseScribe nutzt Eingabemonitoring als Fallback.",
        )

    return (
        normalized,
        "error",
        "macOS blockiert diese Kombination als globalen Hotkey. Aktiviere Eingabemonitoring oder wähle einen anderen Hotkey.",
    )


def validate_hotkey_change(kind: str, hotkey_str: str) -> ValidationResult:
    """Validate a hotkey change.

    Returns:
        (normalized_hotkey, level, message)

        - level is one of: "ok", "warning", "error"
        - message is present for warning/error
    """
    from utils.permissions import check_input_monitoring_permission

    normalized = _normalize(hotkey_str)
    if not normalized:
        return "", "error", "Hotkey ist leer."

    toggle_current, hold_current = _get_current_hotkeys()
    existing_result = _validate_against_current_hotkeys(
        kind,
        normalized,
        toggle_current=toggle_current,
        hold_current=hold_current,
    )
    if existing_result is not None:
        return existing_result

    input_ok = bool(check_input_monitoring_permission(show_alert=False))
    if _requires_input_monitoring(kind, normalized) and not input_ok:
        return (
            normalized,
            "error",
            "Dieser Hotkey benötigt Eingabemonitoring (Systemeinstellungen → Datenschutz & Sicherheit).",
        )

    parsed_hotkey, parse_error = _parse_hotkey_or_error(normalized)
    if parse_error is not None:
        return parse_error

    if kind == "toggle" and not _requires_input_monitoring(kind, normalized):
        assert parsed_hotkey is not None
        virtual_key, modifier_mask = parsed_hotkey
        return _validate_toggle_registration(
            normalized,
            virtual_key=virtual_key,
            modifier_mask=modifier_mask,
            input_ok=input_ok,
        )

    return normalized, "ok", None


__all__ = ["validate_hotkey_change"]
