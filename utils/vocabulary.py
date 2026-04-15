"""Shared Custom Vocabulary loader.

Providers and CLI use the same vocabulary file (`~/.pulsescribe/vocabulary.json`).
To avoid redundant disk I/O on every transcription, this module caches the
parsed vocabulary and only reloads when the file changes.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import logging
from pathlib import Path
from typing import Any

from config import VOCABULARY_FILE as _DEFAULT_VOCAB_FILE
from utils.atomic_io import write_text_atomic
from utils.file_signatures import FileSignature, build_file_signature

logger = logging.getLogger("pulsescribe")

# Cache per path: {Path: (signature, normalized data, validation issues)}
_cache: dict[Path, tuple[FileSignature, dict[str, Any], list[str]]] = {}


def _file_signature(path: Path) -> FileSignature:
    """Erzeugt eine robuste Dateisignatur für Cache-Invalidierung."""
    return build_file_signature(path)


def _copy_vocabulary_data(data: dict[str, Any]) -> dict[str, Any]:
    """Return a defensive copy so callers cannot mutate cached state."""
    return deepcopy(data)


def _update_cached_state(
    vocab_file: Path,
    signature: FileSignature,
    data: dict[str, Any],
    issues: list[str],
) -> None:
    """Store normalized vocabulary state in the per-path cache."""
    _cache[vocab_file] = (signature, _copy_vocabulary_data(data), list(issues))


def _get_cached_state(
    vocab_file: Path,
    signature: FileSignature,
) -> tuple[dict[str, Any], list[str]] | None:
    """Return cached vocabulary state when the signature still matches."""
    cached = _cache.get(vocab_file)
    if cached and cached[0] == signature:
        return _copy_vocabulary_data(cached[1]), list(cached[2])
    return None


@dataclass(frozen=True)
class _KeywordAnalysis:
    normalized: list[str]
    issues: list[str]


def _clean_keyword_strings(raw_keywords: list[Any]) -> tuple[list[str], int]:
    """Return trimmed string keywords plus the count of ignored non-strings."""
    cleaned: list[str] = []
    non_string_count = 0
    for item in raw_keywords:
        if not isinstance(item, str):
            non_string_count += 1
            continue
        keyword = item.strip()
        if keyword:
            cleaned.append(keyword)
    return cleaned, non_string_count


def _dedupe_keywords(keywords: list[str]) -> list[str]:
    """Deduplicate keywords case-insensitively while preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for keyword in keywords:
        dedupe_key = keyword.casefold()
        if dedupe_key not in seen:
            seen.add(dedupe_key)
            result.append(keyword)
    return result


def _build_keyword_issues(
    *,
    non_string_count: int,
    duplicate_count: int,
    keyword_count: int,
) -> list[str]:
    """Build stable validation messages from analyzed keyword counts."""
    issues: list[str] = []
    if non_string_count > 0:
        issues.append(
            f"{non_string_count} Keywords sind keine Strings und werden ignoriert."
        )
    if duplicate_count > 0:
        issues.append(f"{duplicate_count} doppelte Keywords gefunden.")
    if keyword_count > 100:
        issues.append(
            f"{keyword_count} Keywords: Deepgram nutzt max. 100, Local max. 50."
        )
    elif keyword_count > 50:
        issues.append(f"{keyword_count} Keywords: Local nutzt max. 50.")
    return issues


def _analyze_keywords(raw_keywords: Any) -> _KeywordAnalysis:
    """Normalize raw keyword data once and derive validation issues from it."""
    if raw_keywords is None:
        return _KeywordAnalysis([], [])
    if not isinstance(raw_keywords, list):
        return _KeywordAnalysis([], ["'keywords' muss eine Liste sein."])

    cleaned, non_string_count = _clean_keyword_strings(raw_keywords)
    normalized = _dedupe_keywords(cleaned)
    issues = _build_keyword_issues(
        non_string_count=non_string_count,
        duplicate_count=len(cleaned) - len(normalized),
        keyword_count=len(normalized),
    )
    return _KeywordAnalysis(normalized, issues)


def _parse_vocabulary_text(raw_text: str) -> tuple[dict[str, Any], list[str]]:
    """Parse vocabulary JSON once and return normalized data plus validation issues."""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return {"keywords": []}, ["Vocabulary-Datei ist kein gültiges JSON."]

    if not isinstance(data, dict):
        return {"keywords": []}, ["Vocabulary-Datei muss ein JSON-Objekt sein."]

    parsed = dict(data)
    analysis = _analyze_keywords(parsed.get("keywords"))
    parsed["keywords"] = analysis.normalized
    return parsed, analysis.issues


def _read_vocabulary_state(
    vocab_file: Path, signature: FileSignature
) -> tuple[dict[str, Any], list[str]]:
    cached = _get_cached_state(vocab_file, signature)
    if cached is not None:
        return cached

    try:
        raw_text = vocab_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"Vocabulary-Datei fehlerhaft: {e}")
        data: dict[str, Any] = {"keywords": []}
        issues = [f"Vocabulary-Datei nicht lesbar: {e}"]
    else:
        data, issues = _parse_vocabulary_text(raw_text)

    _update_cached_state(vocab_file, signature, data, issues)
    return _copy_vocabulary_data(data), list(issues)


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


def _read_existing_vocabulary_data(vocab_file: Path) -> dict[str, Any]:
    """Load the current JSON object if present, otherwise return an empty payload."""
    if not vocab_file.exists():
        return {}
    try:
        existing = json.loads(vocab_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    return dict(existing) if isinstance(existing, dict) else {}


def _get_current_vocabulary_signature(vocab_file: Path) -> FileSignature | None:
    """Return the current file signature when available."""
    try:
        return _file_signature(vocab_file)
    except (FileNotFoundError, OSError):
        return None


def _reuse_current_vocabulary_state(
    vocab_file: Path,
    *,
    current_signature: FileSignature | None,
    data: dict[str, Any],
    issues: list[str],
    file_matches_target: bool,
) -> bool:
    """Refresh or reuse cached state when the on-disk file already matches."""
    if current_signature is None:
        return False

    cached = _cache.get(vocab_file)
    if cached and cached[0] == current_signature and cached[1] == data:
        return True
    if not file_matches_target:
        return False

    _update_cached_state(vocab_file, current_signature, data, issues)
    return True


def save_vocabulary(keywords: list[str], path: Path | None = None) -> None:
    """Speichert Custom Vocabulary als JSON.

    Args:
        keywords: Liste der Keywords.
        path: Optionaler Pfad-Override (Tests).
    """
    vocab_file = path or _DEFAULT_VOCAB_FILE
    vocab_file.parent.mkdir(parents=True, exist_ok=True)

    analysis = _analyze_keywords(list(keywords))
    existing_data = _read_existing_vocabulary_data(vocab_file)
    data = dict(existing_data)
    data["keywords"] = analysis.normalized

    current_signature = _get_current_vocabulary_signature(vocab_file)
    if _reuse_current_vocabulary_state(
        vocab_file,
        current_signature=current_signature,
        data=data,
        issues=analysis.issues,
        file_matches_target=existing_data == data,
    ):
        return

    try:
        write_text_atomic(
            vocab_file,
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

    # Cache direkt aktualisieren, damit Änderungen sofort wirken.
    try:
        _update_cached_state(
            vocab_file,
            _file_signature(vocab_file),
            data,
            analysis.issues,
        )
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
