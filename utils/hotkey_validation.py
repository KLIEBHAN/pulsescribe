"""Hotkey validation helpers (UI-facing).

Used by Settings and the onboarding wizard to provide immediate feedback when a
hotkey is invalid, duplicated (toggle vs. hold), or blocked by macOS.
"""

from __future__ import annotations


def _normalize(hotkey_str: str | None) -> str:
    return (hotkey_str or "").strip().lower()


def validate_hotkey_change(kind: str, hotkey_str: str) -> tuple[str, str, str | None]:
    """Validate a hotkey change.

    Returns:
        (normalized_hotkey, level, message)

        - level is one of: "ok", "warning", "error"
        - message is present for warning/error
    """
    from utils.hotkey import hotkeys_conflict, parse_hotkey
    from utils.preferences import get_env_setting
    from utils.permissions import check_input_monitoring_permission

    normalized = _normalize(hotkey_str)
    if not normalized:
        return "", "error", "Hotkey ist leer."

    toggle_current = _normalize(get_env_setting("PULSESCRIBE_TOGGLE_HOTKEY"))
    hold_current = _normalize(get_env_setting("PULSESCRIBE_HOLD_HOTKEY"))

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

    # If unchanged, keep it.
    current = toggle_current if kind == "toggle" else hold_current
    if current and normalized == current:
        return normalized, "ok", None

    input_ok = bool(check_input_monitoring_permission(show_alert=False))

    # Hold always uses Quartz (Input Monitoring required).
    # Toggle only needs Input Monitoring for special keys or when Carbon registration fails.
    is_special = normalized in ("fn", "capslock", "caps_lock")
    if kind == "hold" or is_special:
        if not input_ok:
            return (
                normalized,
                "error",
                "Dieser Hotkey benötigt Eingabemonitoring (Systemeinstellungen → Datenschutz & Sicherheit).",
            )

    # Validate syntax/key names.
    try:
        virtual_key, modifier_mask = parse_hotkey(normalized)
    except ValueError as e:
        return normalized, "error", str(e)

    # For toggle, try registering as a global Carbon hotkey; fall back to Quartz if blocked.
    if kind == "toggle" and not is_special:
        from utils.carbon_hotkey import CarbonHotKeyRegistration

        reg = CarbonHotKeyRegistration(
            virtual_key=virtual_key, modifier_mask=modifier_mask, callback=lambda: None
        )
        ok, err = reg.register()
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

    return normalized, "ok", None


__all__ = ["validate_hotkey_change"]
