"""Microphone choices shared by the native settings windows."""

import logging

MICROPHONE_TOOLTIP = (
    "Used from the next recording after Save & Apply. "
    "If unavailable, PulseScribe uses the system default. "
    "Refresh to detect connected microphones."
)


def get_microphone_options(selected: str = "") -> list[tuple[str, str]]:
    """List capture devices without opening streams; retain an unplugged selection."""
    names: list[str] = []
    try:
        import sounddevice as sd

        names = list(dict.fromkeys(
            device["name"]
            for device in sd.query_devices()
            if device["max_input_channels"] > 0
        ))
    except Exception as exc:
        logging.getLogger("pulsescribe").warning(
            "Mikrofone konnten nicht geladen werden: %s", exc
        )
    options = [("System default", ""), *((name, name) for name in names)]
    if selected and selected not in names:
        options.append((f"{selected} (unavailable)", selected))
    return options
