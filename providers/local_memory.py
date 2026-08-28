"""Platform-neutrale Helfer für Local-Provider-Signaturen und Modell-Cache-Freigabe.

Der lokale Whisper-Provider hält große Modellgewichte im RAM (~500 MB). Beide
Plattform-Apps (macOS-Daemon, Windows-Tray) müssen beim Settings-Reload
entscheiden, ob das geladene Modell noch zur Konfiguration passt, und es
gegebenenfalls freigeben. Diese Logik ist identisch und lebt hier.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

# Env-Keys, die das geladene lokale Modell und seinen RAM-Footprint bestimmen.
# Die Reihenfolge ist Teil der Memory-Signatur - nicht umsortieren.
LOCAL_MEMORY_ENV_KEYS = (
    "PULSESCRIBE_DEVICE",
    "PULSESCRIBE_FP16",
    "PULSESCRIBE_LOCAL_COMPUTE_TYPE",
    "PULSESCRIBE_LOCAL_CPU_THREADS",
    "PULSESCRIBE_LOCAL_NUM_WORKERS",
    "PULSESCRIBE_LIGHTNING_BATCH_SIZE",
    "PULSESCRIBE_LIGHTNING_QUANT",
)


def normalize_signature_value(value: str | None) -> str | None:
    """Leere/Whitespace-Werte als None behandeln, damit die Signatur stabil bleibt."""
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def build_memory_signature(
    *,
    mode: str | None,
    model: str | None,
    getenv: Callable[[str], str | None] = os.getenv,
) -> tuple[str | None, ...]:
    """Signatur der lokalen Modell-Konfiguration.

    Ändert sich der Tuple zwischen zwei Reloads, muss das Modell freigegeben und
    neu geladen werden. ``model`` wird pro Plattform unterschiedlich ermittelt
    (Daemon: Instanz-Attribut, Windows: env), daher als Parameter.
    """
    return (
        normalize_signature_value(mode),
        normalize_signature_value(getenv("PULSESCRIBE_LOCAL_BACKEND")),
        normalize_signature_value(model),
        *(normalize_signature_value(getenv(key)) for key in LOCAL_MEMORY_ENV_KEYS),
    )


def sync_env_values(env_values: dict[str, str], keys: tuple[str, ...]) -> None:
    """Synchronisiert Env-Keys mit dem Zustand der .env-Datei.

    python-dotenv setzt Variablen, entfernt sie aber nicht, wenn ein Key aus der
    Datei gelöscht wurde. Fehlende Keys werden deshalb aus os.environ entfernt,
    da ihre Abwesenheit in der Settings-UI Defaults bedeutet.
    """
    for key in keys:
        value = env_values.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def release_provider_resources(provider: Any) -> None:
    """Gibt Provider-Ressourcen frei: ``clear_model_cache`` vorziehen.

    Ruft ``clear_model_cache`` wenn verfügbar, sonst ``cleanup`` als Fallback.
    Fehler werden bewusst nicht geschluckt - der Aufrufer loggt sie im
    Plattform-Kontext.
    """
    clear_model_cache = getattr(provider, "clear_model_cache", None)
    if callable(clear_model_cache):
        clear_model_cache()
        return

    cleanup = getattr(provider, "cleanup", None)
    if callable(cleanup):
        cleanup()
