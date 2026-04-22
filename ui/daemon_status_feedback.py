"""Shared daemon status copy for non-overlay UI surfaces.

These helpers are used by surfaces such as the macOS menu bar and the
Windows tray tooltip. They intentionally stay slightly more descriptive than
raw daemon state names so users get clearer progress and recovery guidance
without changing daemon behavior.
"""

from __future__ import annotations

from utils.state import AppState

DEFAULT_DAEMON_STATUS_LABELS = {
    AppState.IDLE: "Ready to dictate",
    AppState.LOADING: "Preparing transcription",
    AppState.LISTENING: "Listening",
    AppState.RECORDING: "Recording — speak now",
    AppState.TRANSCRIBING: "Transcribing",
    AppState.REFINING: "Refining",
    AppState.DONE: "Transcript pasted",
    AppState.NO_SPEECH: "No speech detected",
    AppState.ERROR: "Something went wrong",
}

DEFAULT_DAEMON_HINTS = {
    AppState.IDLE: "Use your hotkey to start dictation.",
    AppState.LOADING: "First launch or provider changes can take a moment.",
    AppState.LISTENING: "Start speaking, or release the hold hotkey to cancel.",
    AppState.RECORDING: "Release the hold hotkey or stop the toggle hotkey to finish.",
    AppState.TRANSCRIBING: "PulseScribe is turning your speech into text.",
    AppState.REFINING: "PulseScribe is polishing punctuation and formatting before paste.",
    AppState.DONE: "Ready for another dictation.",
    AppState.NO_SPEECH: "Try again and speak a short sentence after the listening prompt.",
    AppState.ERROR: (
        "Try again. PulseScribe will return to ready automatically. "
        "Export diagnostics or open Setup if it keeps happening."
    ),
}


def _normalize_state(state: AppState | str | None) -> AppState:
    if isinstance(state, AppState):
        return state

    normalized = str(state or "").strip().lower()
    for candidate in AppState:
        if normalized in {candidate.value, candidate.name.lower()}:
            return candidate
    return AppState.IDLE


def normalize_daemon_status_text(text: str | None) -> str:
    """Collapse whitespace so titles and hints stay readable."""
    return " ".join((text or "").replace("\n", " ").split())


def _truncate_status_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return text[:max_chars]
    return text[: max_chars - 1].rstrip() + "…"


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _contains_provider_config_error(text: str) -> bool:
    return _contains_any(
        text,
        (
            "unknown provider",
            "unbekannter provider",
            "invalid provider",
            "unsupported provider",
            "unknown refine-provider",
            "unknown refine provider",
            "unbekannter refine-provider",
            "unbekannter refine provider",
            "invalid refine provider",
            "unsupported refine provider",
            "unsupported refine-provider",
        ),
    )


def _contains_connection_error(text: str) -> bool:
    return _contains_any(
        text,
        (
            "connection",
            "connect",
            "network",
            "socket",
            "websocket",
            "streaming",
            "could not reach",
            "failed to reach",
            "service unavailable",
            "provider status",
            "provider unavailable",
            "transcription service",
        ),
    )


def _contains_dependency_error(text: str) -> bool:
    return _contains_any(
        text,
        (
            "no module named",
            "modulenotfounderror",
            "importerror",
            "cannot import name",
            "missing dependency",
            "dependency",
            "pip install",
            "install package",
        ),
    )


def _build_loading_label(detail: str, *, prefer_detail: bool) -> str:
    if not detail:
        return DEFAULT_DAEMON_STATUS_LABELS[AppState.LOADING]

    detail_lower = detail.lower()
    if _contains_any(detail_lower, ("starting up", "startup", "initializing", "prewarm")):
        return "Starting up PulseScribe"
    if "warming up" in detail_lower:
        return "Warming up local model"
    if _contains_any(detail_lower, ("input device", "microphone", "audio")):
        return "Preparing microphone"
    if _contains_any(
        detail_lower,
        ("connection", "connect", "network", "socket", "websocket", "service"),
    ):
        return "Connecting to transcription service"
    if prefer_detail and detail:
        return detail.rstrip(".")
    if detail_lower.startswith("loading "):
        return detail.rstrip(".")
    return DEFAULT_DAEMON_STATUS_LABELS[AppState.LOADING]


def _build_no_speech_label(detail: str) -> str:
    if not detail:
        return DEFAULT_DAEMON_STATUS_LABELS[AppState.NO_SPEECH]

    detail_lower = detail.lower()
    if _contains_any(
        detail_lower,
        (
            "no speech",
            "empty transcript",
            "no transcript",
            "no audio",
            "silent",
            "silence",
            "kein audio",
            "keine sprache",
        ),
    ):
        return DEFAULT_DAEMON_STATUS_LABELS[AppState.NO_SPEECH]
    return detail.rstrip(".")


def _build_error_label(detail: str) -> str:
    if not detail:
        return DEFAULT_DAEMON_STATUS_LABELS[AppState.ERROR]

    detail_lower = detail.lower()
    if _contains_any(
        detail_lower,
        (
            "api key",
            "_api_key",
            "deepgram_api_key",
            "openai_api_key",
            "groq_api_key",
            "openrouter_api_key",
            "gemini_api_key",
        ),
    ):
        return "Missing API key"
    if _contains_any(detail_lower, ("busy", "already recording", "still running")):
        return "PulseScribe is busy"
    if _contains_any(detail_lower, ("timeout", "timed out", "watchdog", "no final response")):
        return "Transcription timed out"
    if _contains_provider_config_error(detail_lower):
        return "Invalid provider setting"
    if _contains_any(detail_lower, ("input monitoring",)):
        return "Input monitoring needed"
    if _contains_any(detail_lower, ("accessibility", "assistive access", "access permission")):
        return "Accessibility permission needed"
    if _contains_any(detail_lower, ("microphone", "mic")) and _contains_any(
        detail_lower,
        ("permission", "denied", "not permitted", "not allowed", "access"),
    ):
        return "Microphone permission needed"
    if _contains_any(
        detail_lower,
        (
            "microphone",
            "mic",
            "input device",
            "audio",
            "sounddevice",
            "wasapi",
        ),
    ):
        return "Microphone unavailable"
    if _contains_connection_error(detail_lower):
        return "Could not reach the transcription service"
    if _contains_dependency_error(detail_lower):
        return "Missing dependency"
    return detail


def build_daemon_status_label(
    state: AppState | str | None,
    text: str | None = None,
    *,
    prefer_detail: bool = True,
    max_chars: int = 80,
) -> str:
    """Return a concise, user-facing status label for non-overlay surfaces."""
    normalized_state = _normalize_state(state)
    detail = normalize_daemon_status_text(text)

    if normalized_state == AppState.RECORDING and prefer_detail and detail:
        return _truncate_status_text(f"Recording: {detail}", max_chars)

    if normalized_state == AppState.LOADING:
        return _truncate_status_text(
            _build_loading_label(detail, prefer_detail=prefer_detail),
            max_chars,
        )

    if normalized_state == AppState.DONE:
        if prefer_detail and detail:
            return _truncate_status_text(detail, max_chars)
        return _truncate_status_text(DEFAULT_DAEMON_STATUS_LABELS[AppState.DONE], max_chars)

    if normalized_state == AppState.NO_SPEECH:
        return _truncate_status_text(_build_no_speech_label(detail), max_chars)

    if normalized_state == AppState.ERROR:
        return _truncate_status_text(_build_error_label(detail), max_chars)

    label = DEFAULT_DAEMON_STATUS_LABELS.get(
        normalized_state,
        DEFAULT_DAEMON_STATUS_LABELS[AppState.IDLE],
    )
    return _truncate_status_text(label, max_chars)


def build_daemon_status_hint(
    state: AppState | str | None,
    text: str | None = None,
    *,
    max_chars: int = 120,
) -> str:
    """Return contextual guidance or recovery help for a daemon state."""
    normalized_state = _normalize_state(state)
    detail = normalize_daemon_status_text(text)
    detail_lower = detail.lower()

    if normalized_state == AppState.LOADING:
        if _contains_any(detail_lower, ("starting up", "startup", "initializing", "prewarm")):
            hint = "PulseScribe is starting in the background. Hotkeys and audio are still getting ready."
        elif "warming up" in detail_lower:
            hint = (
                "Offline dictation is warming up after a model or settings change."
            )
        elif detail_lower.startswith("loading "):
            hint = "Offline dictation is loading your selected local model. The first run can take a moment."
        elif _contains_any(
            detail_lower,
            ("connection", "connect", "network", "socket", "websocket", "service"),
        ):
            hint = "PulseScribe is reconnecting to the transcription service. Try again once it returns to ready."
        elif detail:
            hint = "PulseScribe is preparing the current provider or model."
        else:
            hint = DEFAULT_DAEMON_HINTS[AppState.LOADING]
        return _truncate_status_text(hint, max_chars)

    if normalized_state == AppState.NO_SPEECH:
        if _contains_any(
            detail_lower,
            ("no audio", "empty transcript", "silent", "silence", "too short"),
        ):
            hint = "Try again and speak a little earlier, louder, or closer to the microphone."
        else:
            hint = DEFAULT_DAEMON_HINTS[AppState.NO_SPEECH]
        return _truncate_status_text(hint, max_chars)

    if normalized_state == AppState.ERROR:
        if _contains_any(
            detail_lower,
            (
                "api key",
                "_api_key",
                "deepgram_api_key",
                "openai_api_key",
                "groq_api_key",
                "openrouter_api_key",
                "gemini_api_key",
            ),
        ):
            hint = "Add the required key in Setup & Settings, then try again."
        elif _contains_any(detail_lower, ("busy", "already recording", "still running")):
            hint = "Wait a moment for the current task to finish, then try again."
        elif _contains_any(detail_lower, ("timeout", "timed out", "watchdog", "no final response")):
            hint = (
                "PulseScribe will return to ready automatically. Export diagnostics "
                "or open Setup if it repeats."
            )
        elif _contains_provider_config_error(detail_lower):
            hint = "Open Setup & Settings, choose a supported provider, then try again."
        elif _contains_any(detail_lower, ("input monitoring", "accessibility", "assistive access", "access permission")):
            hint = "Open Setup & Settings, grant the missing permission, then try again."
        elif _contains_any(detail_lower, ("microphone", "mic")) and _contains_any(
            detail_lower,
            ("permission", "denied", "not permitted", "not allowed", "access"),
        ):
            hint = "Open Setup & Settings, allow microphone access, then try again."
        elif _contains_any(
            detail_lower,
            (
                "microphone",
                "mic",
                "input device",
                "audio",
                "sounddevice",
                "wasapi",
            ),
        ):
            hint = "Check microphone access and the selected input device, then try again."
        elif _contains_connection_error(detail_lower):
            hint = "Check your internet connection or provider status, then try again."
        elif _contains_dependency_error(detail_lower):
            hint = "Install the missing dependency or switch providers in Setup & Settings."
        else:
            hint = DEFAULT_DAEMON_HINTS[AppState.ERROR]
        return _truncate_status_text(hint, max_chars)

    hint = DEFAULT_DAEMON_HINTS.get(
        normalized_state,
        DEFAULT_DAEMON_HINTS[AppState.IDLE],
    )
    return _truncate_status_text(hint, max_chars)


def build_daemon_tray_title(
    state: AppState | str | None,
    text: str | None = None,
    *,
    app_name: str = "PulseScribe",
    max_chars: int = 128,
) -> str:
    """Return one accessible tray/tooltip title with status + recovery guidance."""
    normalized_state = _normalize_state(state)
    label = build_daemon_status_label(
        normalized_state,
        text,
        prefer_detail=False,
        max_chars=max_chars,
    )

    if normalized_state in (AppState.LOADING, AppState.NO_SPEECH, AppState.ERROR):
        hint = build_daemon_status_hint(normalized_state, text, max_chars=max_chars)
        summary = f"{label} — {hint}"
    else:
        summary = label

    prefix = f"{app_name} — "
    budget = max(8, max_chars - len(prefix))
    return prefix + _truncate_status_text(summary, budget)


__all__ = [
    "build_daemon_status_hint",
    "build_daemon_status_label",
    "build_daemon_tray_title",
    "normalize_daemon_status_text",
]
