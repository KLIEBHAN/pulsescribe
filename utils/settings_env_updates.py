"""Shared builders for Settings/Welcome `.env` update dictionaries."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from utils.local_backend import normalize_local_backend, should_remove_local_backend_env

EnvUpdates = dict[str, str | None]


@dataclass
class SettingsEnvUpdateBuilder:
    """Collect normalized `.env` updates without depending on UI widgets."""

    logger: logging.Logger | None = None
    updates: EnvUpdates = field(default_factory=dict)

    def set_present(self, key: str, raw_value: Any) -> None:
        value = _clean_text(raw_value)
        if value:
            self.updates[key] = value

    def set_optional(
        self,
        key: str,
        raw_value: Any,
        *,
        remove_when: set[str] | frozenset[str] = frozenset(),
        lower: bool = False,
    ) -> None:
        value = _clean_text(raw_value)
        if lower:
            value = value.lower()
        self.updates[key] = None if not value or value in remove_when else value

    def set_optional_int(self, key: str, raw_value: Any) -> None:
        value = _clean_text(raw_value)
        if not value:
            self.updates[key] = None
            return
        try:
            int(value)
        except ValueError:
            if self.logger is not None:
                self.logger.warning(f"Invalid {key}={value!r}, not saved")
            return
        self.updates[key] = value

    def set_bool_string(self, key: str, enabled: bool) -> None:
        self.updates[key] = "true" if enabled else "false"

    def set_enabled_default_true(self, key: str, enabled: bool) -> None:
        self.updates[key] = None if enabled else "false"

    def set_enabled_default_false(self, key: str, enabled: bool) -> None:
        self.updates[key] = "true" if enabled else None

    def set_local_backend(self, key: str, raw_value: Any) -> None:
        backend = normalize_local_backend(_clean_text(raw_value))
        self.updates[key] = None if should_remove_local_backend_env(backend) else backend

    def set_lightning_batch(
        self,
        key: str,
        raw_value: Any,
        *,
        default: int = 12,
    ) -> None:
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            if self.logger is not None:
                self.logger.warning(f"Invalid {key}={raw_value!r}, not saved")
            return
        self.updates[key] = None if value == default else str(value)

    def set_lightning_quant_from_index(self, key: str, index: int) -> None:
        if index <= 0:
            self.updates[key] = None
        elif index == 1:
            self.updates[key] = "8bit"
        else:
            self.updates[key] = "4bit"

    def build(self) -> EnvUpdates:
        return dict(self.updates)


def _clean_text(raw_value: Any) -> str:
    return str(raw_value or "").strip()


__all__ = ["EnvUpdates", "SettingsEnvUpdateBuilder"]
