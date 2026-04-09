"""Shared Custom Vocabulary loader.

Providers and CLI use the same vocabulary file (`~/.pulsescribe/vocabulary.json`).
To avoid redundant disk I/O on every transcription, this module caches the
parsed vocabulary and only reloads when the file changes.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from config import VOCABULARY_FILE as _DEFAULT_VOCAB_FILE

logger = logging.getLogger("pulsescribe")

# Cache per path: {Path: (signature, normalized data, validation issues)}
_cache: dict[Path, tuple[tuple[int, int, int], dict[str, Any], list[str]]] = {}


def _file_signature(path: Path) -> tuple[int, int, int]:
    """Erzeugt eine robuste Dateisignatur für Cache-Invalidierung."""
    stat_result = path.stat()
    return (
        int(
            getattr(
                stat_result,
                "st_mtime_ns",
                int(getattr(stat_result, "st_mtime", 0.0) * 1_000_000_000),
            )
        ),
        int(getattr(stat_result, "st_size", 0)),
        int(
            getattr(
                stat_result,
                "st_ctime_ns",
                int(getattr(stat_result, "st_ctime", 0.0) * 1_000_000_000),
            )
        ),
    )


def _normalize_keywords(raw_keywords: list) -> list[str]:
    """Normalisiert Keyword-Liste (nur Strings, trim, dedup in Reihenfolge)."""
    cleaned: list[str] = []
    for item in raw_keywords:
        if isinstance(item, str):
            kw = item.strip()
            if kw:
                cleaned.append(kw)

    seen: set[str] = set()
    result: list[str] = []
    for kw in cleaned:
        dedupe_key = kw.casefold()
        if dedupe_key not in seen:
            seen.add(dedupe_key)
            result.append(kw)
    return result


def _collect_keyword_issues(raw_keywords: Any) -> list[str]:
    """Collect validation issues from raw keyword data without rereading the file."""
    if raw_keywords is None:
        return []
    if not isinstance(raw_keywords, list):
        return ["'keywords' muss eine Liste sein."]

    issues: list[str] = []
    non_strings = [k for k in raw_keywords if not isinstance(k, str)]
    if non_strings:
        issues.append(
            f"{len(non_strings)} Keywords sind keine Strings und werden ignoriert."
        )

    normalized = _normalize_keywords(raw_keywords)
    duplicate_count = len(
        [k for k in raw_keywords if isinstance(k, str) and k.strip()]
    ) - len(normalized)
    if duplicate_count > 0:
        issues.append(f"{duplicate_count} doppelte Keywords gefunden.")

    if len(normalized) > 100:
        issues.append(
            f"{len(normalized)} Keywords: Deepgram nutzt max. 100, Local max. 50."
        )
    elif len(normalized) > 50:
        issues.append(f"{len(normalized)} Keywords: Local nutzt max. 50.")

    return issues


def _parse_vocabulary_text(raw_text: str) -> tuple[dict[str, Any], list[str]]:
    """Parse vocabulary JSON once and return normalized data plus validation issues."""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return {"keywords": []}, ["Vocabulary-Datei ist kein gültiges JSON."]

    if not isinstance(data, dict):
        return {"keywords": []}, ["Vocabulary-Datei muss ein JSON-Objekt sein."]

    parsed = dict(data)
    raw_keywords = parsed.get("keywords")
    issues = _collect_keyword_issues(raw_keywords)
    parsed["keywords"] = (
        _normalize_keywords(raw_keywords) if isinstance(raw_keywords, list) else []
    )
    return parsed, issues


def _read_vocabulary_state(
    vocab_file: Path, signature: tuple[int, int, int]
) -> tuple[dict[str, Any], list[str]]:
    cached = _cache.get(vocab_file)
    if cached and cached[0] == signature:
        return cached[1], list(cached[2])

    try:
        raw_text = vocab_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"Vocabulary-Datei fehlerhaft: {e}")
        data: dict[str, Any] = {"keywords": []}
        issues = [f"Vocabulary-Datei nicht lesbar: {e}"]
    else:
        data, issues = _parse_vocabulary_text(raw_text)

    _cache[vocab_file] = (signature, data, list(issues))
    return data, issues


def load_vocabulary(path: Path | None = None) -> dict:
    """Loads custom vocabulary from JSON.

    Args:
        path: Optional override for tests or custom setups.

    Returns:
        Dict with a guaranteed "keywords" list.
    """
    vocab_file = path or _DEFAULT_VOCAB_FILE

    try:
        signature = _file_signature(vocab_file)
    except FileNotFoundError:
        _cache.pop(vocab_file, None)
        return {"keywords": []}
    except OSError as e:
        logger.warning(f"Vocabulary-Datei nicht lesbar: {e}")
        _cache.pop(vocab_file, None)
        return {"keywords": []}

    data, _issues = _read_vocabulary_state(vocab_file, signature)
    return data


def save_vocabulary(keywords: list[str], path: Path | None = None) -> None:
    """Speichert Custom Vocabulary als JSON.

    Args:
        keywords: Liste der Keywords.
        path: Optionaler Pfad-Override (Tests).
    """
    vocab_file = path or _DEFAULT_VOCAB_FILE
    vocab_file.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {}
    existing_data: dict[str, Any] | None = None
    normalized_keywords = _normalize_keywords(list(keywords))
    if vocab_file.exists():
        try:
            existing = json.loads(vocab_file.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                data = existing
                existing_data = dict(existing)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            data = {}

    data["keywords"] = normalized_keywords

    try:
        current_signature = _file_signature(vocab_file)
    except (FileNotFoundError, OSError):
        current_signature = None

    if existing_data == data and current_signature is not None:
        _cache[vocab_file] = (
            current_signature,
            data,
            _collect_keyword_issues(normalized_keywords),
        )
        return

    if current_signature is not None:
        cached = _cache.get(vocab_file)
        if cached and cached[0] == current_signature and cached[1] == data:
            return

    try:
        vocab_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        # Sichere Permissions: Nur Owner lesen/schreiben
        try:
            vocab_file.chmod(0o600)
        except OSError:
            pass  # Windows unterstützt chmod nicht vollständig
    except OSError as e:
        logger.warning(f"Vocabulary-Datei nicht schreibbar: {e}")
        raise

    issues = _collect_keyword_issues(normalized_keywords)

    # Cache direkt aktualisieren, damit Änderungen sofort wirken.
    try:
        _cache[vocab_file] = (_file_signature(vocab_file), data, issues)
    except OSError:
        _cache.pop(vocab_file, None)


def validate_vocabulary(path: Path | None = None) -> list[str]:
    """Validiert die Vocabulary-Datei und gibt Warnungen zurück."""
    vocab_file = path or _DEFAULT_VOCAB_FILE
    if not vocab_file.exists():
        return []

    try:
        signature = _file_signature(vocab_file)
    except OSError as e:
        return [f"Vocabulary-Datei nicht lesbar: {e}"]

    _data, issues = _read_vocabulary_state(vocab_file, signature)
    return issues


__all__ = ["load_vocabulary", "save_vocabulary", "validate_vocabulary"]
