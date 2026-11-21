"""Export language-neutral golden fixtures from the Python implementation.

Both pytest (`tests/test_golden_fixtures.py`) and Rust (`crates/core/tests/`)
validate against the exported JSON files so behavior cannot drift silently
during the Rust migration. Only contracts that already have a Rust counterpart
are exported here; later Phase 1 PRs extend this file alongside their Rust code.

Usage:
    python tests/golden/export_fixtures.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from refine.prompts import (  # noqa: E402
    CONTEXT_PROMPTS,
    DEFAULT_APP_CONTEXTS,
    VOICE_COMMANDS_INSTRUCTION,
)
from ui.animation import (  # noqa: E402
    BAR_MAX_HEIGHT,
    BAR_MIN_HEIGHT,
    LISTENING_BAR_HEIGHTS,
    AnimationLogic,
    listening_bar_heights,
    listening_level,
)
from ui.daemon_status_feedback import (  # noqa: E402
    build_daemon_status_hint,
    build_daemon_status_label,
    build_daemon_tray_title,
    infer_daemon_status_error,
    normalize_daemon_status_text,
)
from ui.overlay_feedback import (  # noqa: E402
    describe_overlay_content,
    transcribing_status,
)
from ui.overlay_style import (  # noqa: E402
    BADGE_TONES,
    BAR_AREA_WIDTH,
    BAR_COLORS,
    OVERLAY_WIDTH,
    PHASE_STEP_COLORS,
    PHASE_STEP_WIDTH,
    compute_overlay_layout,
    hex_to_rgb,
    shimmer_track,
)
from ui.overlay_text import (  # noqa: E402
    InterimLines,
    format_recording_duration,
    format_recording_interim_text,
    split_recording_interim_text,
    wrap_interim_text,
)
from utils.custom_prompts import (  # noqa: E402
    KNOWN_CONTEXTS,
    PROMPT_EDITOR_CONTEXT_OPTIONS,
    _merge_user_with_defaults,
    _serialize_prompt_sections,
    build_prompt_overrides_from_editor_state,
    filter_overrides_for_storage,
    format_app_mappings,
    get_defaults as get_prompt_defaults,
    get_prompt_editor_context_description,
    get_prompt_editor_context_label,
    get_prompt_editor_placeholder,
    normalize_prompt_editor_context,
    parse_app_mappings,
)
from utils.env import _read_dotenv_values, parse_bool, parse_env_line  # noqa: E402
from utils.history import (  # noqa: E402
    DISPLAY_TRANSCRIPTS_EMPTY_MESSAGE,
    WELCOME_TRANSCRIPTS_EMPTY_MESSAGE,
    _select_recent_lines_within_bytes,
    format_transcripts_for_display,
    format_transcripts_for_welcome,
    merge_recent_transcript_entries,
)
from utils.hold_state import HoldHotkeyState  # noqa: E402
from utils.hotkey import (  # noqa: E402
    KEY_CODE_MAP,
    MODIFIER_MAP,
    hotkeys_conflict as macos_hotkeys_conflict,
    parse_hotkey as parse_macos_hotkey,
)
from utils.hotkey_validation import validate_hotkey_change  # noqa: E402
from utils.hotkey_windows import (  # noqa: E402
    hotkeys_conflict as windows_hotkeys_conflict,
    normalize_windows_hotkey,
)
from utils.onboarding import (  # noqa: E402
    ONBOARDING_ORDER,
    OnboardingChoice,
    next_step,
    prev_step,
    step_index,
    total_steps,
)
from utils.preferences import (  # noqa: E402
    _apply_env_updates_to_lines,
    _format_env_assignment,
)
from utils.state import AppState, DaemonErrorCode, MessageType  # noqa: E402
from utils.text_preview import normalized_text_prefix  # noqa: E402
from utils.transcript_delivery import (  # noqa: E402
    COPIED,
    COPY_FAILED,
    DELIVERY_HINTS,
    PASTE_FAILED,
    PASTED,
    TEST_COMPLETE,
    TranscriptDelivery,
    done_status,
    transcription_time_enabled,
)
from utils.vocabulary import (  # noqa: E402
    _parse_vocabulary_text,
    analyze_vocabulary_text,
    split_vocabulary_text,
)

GOLDEN_FILE = Path(__file__).resolve().parent / "core_contracts.json"

_PARSE_BOOL_INPUTS: tuple[str | None, ...] = (
    None,
    "",
    "   ",
    "1",
    "true",
    "TRUE",
    " yes ",
    "On",
    "0",
    "false",
    "FALSE",
    " no ",
    "Off",
    "maybe",
    "2",
)

_PARSE_ENV_LINE_INPUTS: tuple[str, ...] = (
    "",
    "   # comment",
    "NO_EQUALS",
    "=missing_key",
    "KEY=value",
    "KEY=",
    "  export MODE = local  ",
    'export MODE = "local" # keep',
    'export MODE = "" # keep',
    "MIC='Bob\\'s #1 \"USB\" \\\\Mic' # inline",
    "HASH_NO_SPACE=abc#def",
    "HASH_WITH_SPACE=abc # trailing comment",
    "QUOTED_HASH=\"abc # kept\" # trailing",
)

_FORMAT_ENV_INPUTS: tuple[tuple[str, str], ...] = (
    ("PULSESCRIBE_MODE", "local"),
    ("PULSESCRIBE_MODE", ""),
    ("PULSESCRIBE_INPUT_DEVICE", "MacBook Pro Microphone"),
    ("PULSESCRIBE_INPUT_DEVICE", "Bob's #1 \"USB\" \\Mic"),
)

_ENV_UPDATE_CASES: tuple[tuple[str, list[str], dict[str, str | None], bool], ...] = (
    (
        "single-unchanged-preserves-format-and-dup",
        ['export MODE = "local" # keep', "MODE=legacy"],
        {"MODE": "local"},
        True,
    ),
    (
        "batch-unchanged-canonicalizes-and-dedupes",
        ['export MODE = "local" # keep', "MODE=legacy"],
        {"MODE": "local"},
        False,
    ),
    (
        "single-changed-updates-first-keeps-dup",
        ['export MODE = "old"', "# keep", "MODE=legacy"],
        {"MODE": "local"},
        True,
    ),
    (
        "batch-changed-collapses-dup",
        ['export MODE = "old"', "# keep", "MODE=legacy"],
        {"MODE": "local"},
        False,
    ),
    (
        "single-empty-value-not-deletion",
        ["MODE=old", "MODE=legacy"],
        {"MODE": ""},
        True,
    ),
    (
        "batch-deletion-removes-all-matching",
        ["# keep", "MODE=old", "MODE=legacy", "OTHER=first", "OTHER=second"],
        {"MODE": None},
        False,
    ),
    (
        "batch-absent-deletion-is-noop",
        ["# keep", "OTHER=first"],
        {"MODE": None},
        False,
    ),
    (
        "batch-append-empty-value",
        ["# keep", "OTHER=value"],
        {"MODE": ""},
        False,
    ),
    (
        "batch-multi-key-order",
        ["PULSESCRIBE_HOTKEY=f18", "PULSESCRIBE_HOLD_HOTKEY=fn"],
        {
            "PULSESCRIBE_TOGGLE_HOTKEY": "f19",
            "PULSESCRIBE_HOTKEY": None,
            "PULSESCRIBE_HOTKEY_MODE": None,
        },
        False,
    ),
    (
        "quoted-key-deletion",
        ["'OPENAI_API_KEY'=secret", "OTHER=1"],
        {"OPENAI_API_KEY": None},
        False,
    ),
    (
        "quoted-key-replacement",
        ["'OPENAI_API_KEY'=old"],
        {"OPENAI_API_KEY": "new"},
        False,
    ),
    (
        "quoted-key-single-unchanged",
        ["'OPENAI_API_KEY'=same"],
        {"OPENAI_API_KEY": "same"},
        True,
    ),
    (
        "export-tab-key-replacement",
        ["export\tMODE=old"],
        {"MODE": "local"},
        False,
    ),
)

_DOTENV_RESOLVE_PROCESS_ENV: dict[str, str] = {
    "PULSESCRIBE_GOLDEN_PROC_VAR": "from-proc",
}

_DOTENV_RESOLVE_CONTENT = "\n".join(
    [
        "BASE=actual-key",
        "GROQ_API_KEY=${BASE}",
        "CHAINED=${GROQ_API_KEY}-suffix",
        "FROM_PROC=${PULSESCRIBE_GOLDEN_PROC_VAR}",
        "WITH_DEFAULT=${PULSESCRIBE_GOLDEN_UNSET:-fallback}",
        "UNSET_PLAIN=${PULSESCRIBE_GOLDEN_UNSET}",
        "SINGLE_EXP='${BASE}'",
        'DOUBLE_EXP="${BASE}"',
        "BARE_DOLLAR=$BASE",
        "'QUOTED_KEY'=quoted-val",
        'PULSESCRIBE_APP_CONTEXTS={"Slack": "chat"}',
        'JSON_WITH_COMMENT={"a": "b"} # comment',
        "UNQUOTED_APOSTROPHE=Bob's Mic # comment",
        "PULSESCRIBE_INPUT_DEVICE=USB ${PULSESCRIBE_GOLDEN_PROC_VAR}",
    ]
) + "\n"


def _export_env_update_case(
    name: str,
    lines: list[str],
    updates: dict[str, str | None],
    preserve_following_duplicates: bool,
) -> dict[str, Any]:
    out_lines, changed = _apply_env_updates_to_lines(
        lines,
        updates,
        preserve_following_duplicates=preserve_following_duplicates,
    )
    return {
        "name": name,
        "lines": lines,
        "updates": [[k, v] for k, v in updates.items()],
        "preserve_following_duplicates": preserve_following_duplicates,
        "expected_lines": out_lines,
        "expected_changed": changed,
    }


def _export_dotenv_resolve() -> dict[str, Any]:
    sentinel_unset = "PULSESCRIBE_GOLDEN_UNSET"
    saved = {
        key: os.environ.get(key)
        for key in (*_DOTENV_RESOLVE_PROCESS_ENV.keys(), sentinel_unset)
    }
    try:
        for key, value in _DOTENV_RESOLVE_PROCESS_ENV.items():
            os.environ[key] = value
        os.environ.pop(sentinel_unset, None)
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text(_DOTENV_RESOLVE_CONTENT, encoding="utf-8")
            resolved = _read_dotenv_values(env_path)
    finally:
        for key, previous in saved.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous

    return {
        "process_env": dict(_DOTENV_RESOLVE_PROCESS_ENV),
        "content": _DOTENV_RESOLVE_CONTENT,
        "expected": dict(sorted(resolved.items())),
    }


def _export_vocabulary_contracts() -> dict[str, Any]:
    split_inputs: tuple[str | None, ...] = (
        None,
        "",
        "   , , \n ",
        "PulseScribe, Whisper\r\n  Deepgram , , \rGroq",
    )
    analyze_inputs: tuple[tuple[str, str | None], ...] = (
        ("none", None),
        ("dedupe-and-casefold", "Whisper, whisper, Straße, STRASSE, ſage, sage, ﬁle, FILE, Groq"),
        (
            "over-local-limit",
            ", ".join(f"local_term_{i}" for i in range(51)),
        ),
        (
            "over-deepgram-limit",
            "\n".join(f"dg_term_{i}" for i in range(101)),
        ),
    )
    parse_inputs: tuple[tuple[str, str], ...] = (
        (
            "valid-with-extra-nonstrings-and-dups",
            json.dumps(
                {
                    "keywords": ["  PulseScribe  ", "pulsescribe", "", "   ", 42, True, None, "Deepgram"],
                    "custom_meta": {"version": 1},
                }
            ),
        ),
        ("missing-keywords-key", '{"custom_meta": "kept"}'),
        ("null-keywords", '{"keywords": null}'),
        ("non-list-keywords", '{"keywords": "not-a-list"}'),
        ("non-object-json", '["PulseScribe"]'),
        ("invalid-json", "{broken json"),
    )

    analyze_cases = []
    for name, raw in analyze_inputs:
        res = analyze_vocabulary_text(raw)
        analyze_cases.append(
            {
                "name": name,
                "input": raw,
                "keywords": res.keywords,
                "issues": res.issues,
                "input_count": res.input_count,
                "duplicate_count": res.duplicate_count,
                "exceeds_local_limit": res.exceeds_local_limit,
                "exceeds_deepgram_limit": res.exceeds_deepgram_limit,
            }
        )

    parse_cases = []
    for name, raw in parse_inputs:
        data, issues = _parse_vocabulary_text(raw)
        parse_cases.append(
            {
                "name": name,
                "input": raw,
                "expected_data": data,
                "expected_issues": issues,
            }
        )

    return {
        "split_text": [
            {"input": raw, "expected": split_vocabulary_text(raw)}
            for raw in split_inputs
        ],
        "analyze_text": analyze_cases,
        "parse_text": parse_cases,
    }


def _export_prompts_contracts() -> dict[str, Any]:
    defaults = get_prompt_defaults()
    normalize_inputs: tuple[str | None, ...] = (
        None,
        "",
        "default",
        "EMAIL",
        '  "── Voice Commands"  ',
        "Voice Commands",
        "voice_commands",
        "App Mappings",
        "── app mappings",
        "unknown-context",
    )

    sample_mappings_for_format = {
        "Zed": "code",
        "Mail": "email",
        "Slack": "chat",
    }
    sample_mappings_text = "\n".join(
        [
            "# Comment line",
            "",
            '  "VS Code" = CODE # inline comment',
            "NoEqualsLine",
            "GhostApp = nonexistent_context",
            "Mail = 'email'",
            'App.With.Dots = "chat"',
            "  = code",
        ]
    )

    custom_merged = {
        "voice_commands": {"instruction": "Custom voice instructions"},
        "prompts": {
            "default": {"prompt": CONTEXT_PROMPTS["default"]},
            "email": {"prompt": "Custom email rewrite"},
            "chat": {"prompt": ""},
            "code": {"prompt": CONTEXT_PROMPTS["code"]},
        },
        "app_contexts": {
            **DEFAULT_APP_CONTEXTS,
            "Mail": "email",  # unchanged default -> filtered out
            "Slack": "email",  # overridden default -> kept
            "  Linear  ": "CODE",  # normalized -> kept
            "InvalidApp": "mystery",  # invalid -> ignored
        },
    }
    filtered_overrides = filter_overrides_for_storage(custom_merged, defaults=defaults)

    editor_existing = {
        "voice_commands": {"instruction": VOICE_COMMANDS_INSTRUCTION},
        "prompts": {
            **{ctx: {"prompt": text} for ctx, text in CONTEXT_PROMPTS.items()},
            "code": {"prompt": "Existing code override"},
        },
        "app_contexts": dict(DEFAULT_APP_CONTEXTS),
    }
    editor_drafts = {
        "email": "Draft email override",
        "chat": "Untouched draft that is excluded by contexts filter",
        "App Mappings": "Mail = email\nLinear = code\n",
    }
    editor_contexts = ["email", "App Mappings"]
    editor_overrides = build_prompt_overrides_from_editor_state(
        existing=editor_existing,
        drafts=editor_drafts,
        contexts=editor_contexts,
        defaults=defaults,
    )

    toml_overrides = {
        "voice_commands": {
            "instruction": 'Say """stop""" and keep C:\\Users\\Test\\file.txt',
        },
        "prompts": {
            "email": {"prompt": 'Formal tone with """quotes""" and \\backslash\\'},
        },
        "app_contexts": {
            "App.With.Dots": "code",
            'Quoted "App" \\Name': "chat",
        },
    }
    serialized_toml = "\n".join(_serialize_prompt_sections(toml_overrides))
    merged_from_toml = _merge_user_with_defaults(toml_overrides)

    refine_cases = []
    for ctx in ("default", "email", "chat", "code", "unknown"):
        for vc in (False, True):
            effective = ctx if ctx in KNOWN_CONTEXTS else "default"
            prompt_text = CONTEXT_PROMPTS[effective]
            expected = f"{VOICE_COMMANDS_INSTRUCTION}\n{prompt_text}" if vc else prompt_text
            refine_cases.append(
                {
                    "context": ctx,
                    "voice_commands": vc,
                    "expected": expected,
                }
            )

    return {
        "known_contexts": list(KNOWN_CONTEXTS),
        "defaults": {
            "voice_commands_instruction": defaults["voice_commands"]["instruction"],
            "prompts": {
                ctx: cfg["prompt"] for ctx, cfg in sorted(defaults["prompts"].items())
            },
            "app_contexts": dict(sorted(defaults["app_contexts"].items())),
        },
        "editor_contexts": [
            {
                "key": key,
                "label": get_prompt_editor_context_label(key),
                "description": get_prompt_editor_context_description(key),
                "placeholder": get_prompt_editor_placeholder(key),
            }
            for key, _label in PROMPT_EDITOR_CONTEXT_OPTIONS
        ],
        "normalize_editor_context": [
            {"input": raw, "expected": normalize_prompt_editor_context(raw)}
            for raw in normalize_inputs
        ],
        "format_app_mappings": {
            "input": sample_mappings_for_format,
            "expected": format_app_mappings(sample_mappings_for_format),
        },
        "parse_app_mappings": {
            "input": sample_mappings_text,
            "expected": dict(sorted(parse_app_mappings(sample_mappings_text).items())),
        },
        "filter_overrides": {
            "voice_commands_instruction": custom_merged["voice_commands"]["instruction"],
            "prompts": {
                ctx: cfg["prompt"] for ctx, cfg in custom_merged["prompts"].items()
            },
            "app_contexts": custom_merged["app_contexts"],
            "expected_voice_commands_instruction": filtered_overrides.get(
                "voice_commands", {}
            ).get("instruction"),
            "expected_prompts": {
                ctx: cfg["prompt"]
                for ctx, cfg in sorted(filtered_overrides.get("prompts", {}).items())
            },
            "expected_app_contexts": dict(
                sorted(filtered_overrides.get("app_contexts", {}).items())
            ),
        },
        "editor_build_overrides": {
            "existing_code_prompt": editor_existing["prompts"]["code"]["prompt"],
            "drafts": editor_drafts,
            "contexts": editor_contexts,
            "expected_voice_commands_instruction": editor_overrides.get(
                "voice_commands", {}
            ).get("instruction"),
            "expected_prompts": {
                ctx: cfg["prompt"]
                for ctx, cfg in sorted(editor_overrides.get("prompts", {}).items())
            },
            "expected_app_contexts": dict(
                sorted(editor_overrides.get("app_contexts", {}).items())
            ),
        },
        "toml_roundtrip": {
            "overrides_voice_commands": toml_overrides["voice_commands"]["instruction"],
            "overrides_prompts": {
                ctx: cfg["prompt"] for ctx, cfg in toml_overrides["prompts"].items()
            },
            "overrides_app_contexts": dict(
                sorted(toml_overrides["app_contexts"].items())
            ),
            "serialized_toml": serialized_toml,
            "expected_merged_voice_commands": merged_from_toml["voice_commands"][
                "instruction"
            ],
            "expected_merged_prompts": {
                ctx: cfg["prompt"]
                for ctx, cfg in sorted(merged_from_toml["prompts"].items())
            },
            "expected_merged_app_contexts": dict(
                sorted(merged_from_toml["app_contexts"].items())
            ),
        },
        "refine_prompt_cases": refine_cases,
    }


def _export_history_contracts() -> dict[str, Any]:
    select_cases = [
        ("empty", [], 100),
        ("fits-all", ["line1", "line2"], 100),
        ("utf8-budget-trim", ["äää", "bbb", "ccc"], 9),
        ("oversized-newest-kept", ["old", "x" * 40], 10),
    ]

    sample_entries: list[dict[str, Any]] = [
        {
            "timestamp": "2026-11-21T10:15:30.123456",
            "text": "\n\nFirst line  \r\n\r\n  Indented paragraph\rLast line  \n\n",
            "mode": "local",
            "language": "de",
            "refined": True,
        },
        {
            "timestamp": "2026-11-21T10:10:00.000000",
            "text": "  Simple entry  ",
            "mode": " deepgram ",
            "language": " en ",
        },
        {
            "timestamp": "2026-11-21T10:05:00",
            "text": "   ",
            "mode": "groq",
        },
    ]

    prev_entries = [
        {"timestamp": "2026-11-21T09:00:00", "text": "one"},
        {"timestamp": "2026-11-21T09:01:00", "text": "two"},
        {"timestamp": "2026-11-21T09:02:00", "text": "three"},
    ]
    appended_entries = [
        {"timestamp": "2026-11-21T09:03:00", "text": "four"},
    ]

    merge_cases = [
        ("zero-max", prev_entries, appended_entries, 0),
        ("window-trim", prev_entries, appended_entries, 2),
        ("trim-prev-only", prev_entries, [], 2),
    ]

    return {
        "display_empty_message": DISPLAY_TRANSCRIPTS_EMPTY_MESSAGE,
        "welcome_empty_message": WELCOME_TRANSCRIPTS_EMPTY_MESSAGE,
        "select_recent_lines": [
            {
                "name": name,
                "lines": lines,
                "max_size_bytes": max_bytes,
                "expected": _select_recent_lines_within_bytes(lines, max_bytes),
            }
            for name, lines, max_bytes in select_cases
        ],
        "merge_recent_entries": [
            {
                "name": name,
                "previous": prev,
                "appended": app,
                "max_entries": max_entries,
                "expected_texts": [
                    entry["text"]
                    for entry in merge_recent_transcript_entries(
                        prev, app, max_entries=max_entries
                    )
                ],
            }
            for name, prev, app, max_entries in merge_cases
        ],
        "format_entries": sample_entries,
        "format_display_empty": format_transcripts_for_display([], newest_first=True),
        "format_display_newest_first": format_transcripts_for_display(
            sample_entries, newest_first=True
        ),
        "format_display_oldest_first": format_transcripts_for_display(
            sample_entries, newest_first=False
        ),
        "format_welcome_empty": format_transcripts_for_welcome([], newest_first=True),
        "format_welcome_newest_first": format_transcripts_for_welcome(
            sample_entries, newest_first=True
        ),
        "format_welcome_oldest_first": format_transcripts_for_welcome(
            sample_entries, newest_first=False
        ),
    }


def _export_hotkey_contracts() -> dict[str, Any]:
    macos_parse_inputs: tuple[str, ...] = (
        "f19",
        "F1",
        "fn",
        "capslock",
        "caps_lock",
        "space",
        "enter",
        "return",
        "esc",
        "escape",
        "delete",
        "backspace",
        "forwarddelete",
        "a",
        "0",
        ".",
        "\\",
        "=",
        "cmd+r",
        "command+shift+r",
        "ctrl+shift+space",
        "control+option+f19",
        "alt+.",
        "  CMD + Shift + Space  ",
        "",
        "   ",
        "f21",
        "unknown",
        "cmd+",
        "+r",
        "super+r",
        "cmd+bogus",
    )
    macos_parse_cases = []
    for raw in macos_parse_inputs:
        try:
            vk, mask = parse_macos_hotkey(raw)
            macos_parse_cases.append(
                {"input": raw, "virtual_key": vk, "modifier_mask": mask, "error": None}
            )
        except ValueError as exc:
            macos_parse_cases.append(
                {"input": raw, "virtual_key": None, "modifier_mask": None, "error": str(exc)}
            )

    macos_conflict_pairs: tuple[tuple[str | None, str | None], ...] = (
        ("space", "cmd+space"),
        ("cmd+space", "space"),
        ("cmd+r", "cmd+shift+r"),
        ("command+r", "cmd+r"),
        ("cmd+r", "alt+r"),
        ("f18", "f19"),
        ("", "cmd+r"),
        (None, "cmd+r"),
        ("invalid", "cmd+r"),
    )

    windows_normalize_inputs: tuple[str | None, ...] = (
        None,
        "",
        "   ",
        "Option + Control + Shift + Command + R",
        "ctrl+ctrl+alt+r",
        "return",
        "escape",
        "caps_lock",
        "f01",
        "f24",
        "ctrl+win",
        "ctrl+ä",
        "ctrl++r",
        "ctrl+",
        "+r",
        "f0",
        "f25",
        "ctrl+bogus",
        "ctrl+a+b",
    )
    windows_normalize_cases = []
    for raw in windows_normalize_inputs:
        normalized, error = normalize_windows_hotkey(raw)
        windows_normalize_cases.append(
            {"input": raw, "normalized": normalized, "error": error}
        )

    windows_conflict_pairs: tuple[tuple[str | None, str | None], ...] = (
        ("ctrl+win", "ctrl+win+r"),
        ("ctrl+win+r", "ctrl+win"),
        ("command+option+r", "win+alt+r"),
        ("ctrl+r", "alt+r"),
        ("ctrl+a+b", "ctrl+a"),
        (None, "ctrl+r"),
    )

    validation_scenarios: tuple[dict[str, Any], ...] = (
        {
            "name": "empty",
            "kind": "toggle",
            "hotkey": "   ",
            "toggle_current": "f19",
            "hold_current": "fn",
            "replace_other": False,
            "input_monitoring_allowed": True,
            "carbon_ok": True,
        },
        {
            "name": "same-as-other",
            "kind": "toggle",
            "hotkey": "fn",
            "toggle_current": "f19",
            "hold_current": "fn",
            "replace_other": False,
            "input_monitoring_allowed": True,
            "carbon_ok": True,
        },
        {
            "name": "overlap-other",
            "kind": "toggle",
            "hotkey": "cmd+r",
            "toggle_current": "f19",
            "hold_current": "cmd+shift+r",
            "replace_other": False,
            "input_monitoring_allowed": True,
            "carbon_ok": True,
        },
        {
            "name": "replace-other-skips-conflict",
            "kind": "toggle",
            "hotkey": "cmd+r",
            "toggle_current": "",
            "hold_current": "cmd+shift+r",
            "replace_other": True,
            "input_monitoring_allowed": False,
            "carbon_ok": True,
        },
        {
            "name": "unchanged-current-short-circuits",
            "kind": "toggle",
            "hotkey": " F19 ",
            "toggle_current": "f19",
            "hold_current": "fn",
            "replace_other": False,
            "input_monitoring_allowed": False,
            "carbon_ok": False,
        },
        {
            "name": "hold-requires-input-monitoring",
            "kind": "hold",
            "hotkey": "f18",
            "toggle_current": "f19",
            "hold_current": "",
            "replace_other": False,
            "input_monitoring_allowed": False,
            "carbon_ok": True,
        },
        {
            "name": "special-toggle-requires-input-monitoring",
            "kind": "toggle",
            "hotkey": "capslock",
            "toggle_current": "f19",
            "hold_current": "",
            "replace_other": False,
            "input_monitoring_allowed": False,
            "carbon_ok": True,
        },
        {
            "name": "special-toggle-allowed-with-input-monitoring",
            "kind": "toggle",
            "hotkey": "fn",
            "toggle_current": "f19",
            "hold_current": "",
            "replace_other": False,
            "input_monitoring_allowed": True,
            "carbon_ok": False,
        },
        {
            "name": "invalid-hotkey-error",
            "kind": "toggle",
            "hotkey": "super+r",
            "toggle_current": "f19",
            "hold_current": "",
            "replace_other": False,
            "input_monitoring_allowed": True,
            "carbon_ok": True,
        },
        {
            "name": "carbon-blocked-with-input-monitoring-warns",
            "kind": "toggle",
            "hotkey": "cmd+space",
            "toggle_current": "f19",
            "hold_current": "",
            "replace_other": False,
            "input_monitoring_allowed": True,
            "carbon_ok": False,
        },
        {
            "name": "carbon-blocked-without-input-monitoring-errors",
            "kind": "toggle",
            "hotkey": "cmd+space",
            "toggle_current": "f19",
            "hold_current": "",
            "replace_other": False,
            "input_monitoring_allowed": False,
            "carbon_ok": False,
        },
        {
            "name": "carbon-accepted-ok",
            "kind": "toggle",
            "hotkey": "cmd+shift+r",
            "toggle_current": "f19",
            "hold_current": "",
            "replace_other": False,
            "input_monitoring_allowed": False,
            "carbon_ok": True,
        },
    )

    validation_cases = []
    for scenario in validation_scenarios:
        carbon_ok = scenario["carbon_ok"]

        class _FakeCarbonReg:
            def __init__(self, virtual_key: int, modifier_mask: int, callback: Any) -> None:
                self.virtual_key = virtual_key
                self.modifier_mask = modifier_mask

            def register(self) -> tuple[bool, str | None]:
                return (carbon_ok, None if carbon_ok else "blocked")

            def unregister(self) -> None:
                return None

        env_values = {
            "PULSESCRIBE_TOGGLE_HOTKEY": scenario["toggle_current"],
            "PULSESCRIBE_HOLD_HOTKEY": scenario["hold_current"],
        }
        with (
            patch(
                "utils.preferences.get_env_setting",
                side_effect=lambda k: env_values.get(k),
            ),
            patch(
                "utils.permissions.check_input_monitoring_permission",
                return_value=scenario["input_monitoring_allowed"],
            ),
            patch(
                "utils.carbon_hotkey.CarbonHotKeyRegistration",
                _FakeCarbonReg,
            ),
        ):
            normalized, level, message = validate_hotkey_change(
                scenario["kind"],
                scenario["hotkey"],
                replace_other=scenario["replace_other"],
            )
        validation_cases.append(
            {
                **scenario,
                "expected_normalized": normalized,
                "expected_level": level,
                "expected_message": message,
            }
        )

    return {
        "macos_key_code_map": dict(sorted(KEY_CODE_MAP.items())),
        "macos_modifier_map": dict(sorted(MODIFIER_MAP.items())),
        "macos_parse": macos_parse_cases,
        "macos_conflict": [
            {"a": a, "b": b, "expected": macos_hotkeys_conflict(a, b)}
            for a, b in macos_conflict_pairs
        ],
        "windows_normalize": windows_normalize_cases,
        "windows_conflict": [
            {"a": a, "b": b, "expected": windows_hotkeys_conflict(a, b)}
            for a, b in windows_conflict_pairs
        ],
        "macos_validation": validation_cases,
    }


def _export_hold_state_contracts() -> list[dict[str, Any]]:
    steps: list[tuple[str, str | None]] = [
        ("should_start", "quartz"),
        ("should_start", "quartz"),
        ("is_active", "quartz"),
        ("should_stop", "quartz"),
        ("is_active", "quartz"),
        ("should_start", "quartz"),
        ("mark_started", None),
        ("should_start", "pynput"),
        ("should_stop", "quartz"),
        ("is_active", "pynput"),
        ("should_stop", "pynput"),
        ("reset", None),
        ("should_stop", "pynput"),
    ]
    state = HoldHotkeyState()
    trace: list[dict[str, Any]] = []
    for op, source in steps:
        result: bool | None = None
        if op == "should_start":
            assert source is not None
            result = state.should_start(source)
        elif op == "should_stop":
            assert source is not None
            result = state.should_stop(source)
        elif op == "is_active":
            assert source is not None
            result = state.is_active(source)
        elif op == "mark_started":
            state.mark_started()
        elif op == "reset":
            state.reset()
        trace.append({"op": op, "source": source, "expected": result})
    return trace


def _export_transcript_delivery_contracts() -> dict[str, Any]:
    expected_platform_paste_failed = (
        "Copied — press ⌘V" if sys.platform == "darwin" else "Copied — press Ctrl+V"
    )
    assert PASTE_FAILED == expected_platform_paste_failed

    done_status_inputs: tuple[tuple[str, str | None, float | None], ...] = (
        (PASTED, "Mail", 1.24),
        (PASTED, None, 0.0),
        (PASTED, "", 2.35),
        (COPIED, "Mail", 1.25),
        (TEST_COMPLETE, "Mail", -0.5),
        (COPY_FAILED, "Slack", None),
    )

    show_rtf_inputs: tuple[str | None, ...] = (
        None,
        "",
        "true",
        "TRUE",
        "1",
        "yes",
        "on",
        " true ",
        "false",
        "0",
    )
    show_rtf_cases = []
    for raw in show_rtf_inputs:
        with patch("utils.preferences.get_env_setting", return_value=raw):
            show_rtf_cases.append(
                {"input": raw, "expected": transcription_time_enabled()}
            )

    delivery_matrix: tuple[tuple[str, bool, bool, bool], ...] = (
        ("auto-paste-ok", True, True, True),
        ("auto-paste-fallback-copy", True, False, True),
        ("auto-paste-all-fail", True, False, False),
        ("copy-only-ok", False, False, True),
        ("copy-only-fail", False, False, False),
    )
    delivery_cases = []
    for name, auto_paste, paste_ok, copy_ok in delivery_matrix:
        delivery = TranscriptDelivery()
        with (
            patch("utils.hotkey.paste_transcript", return_value=paste_ok),
            patch("utils.hotkey.copy_transcript", return_value=copy_ok),
        ):
            ok, feedback = delivery.deliver("Hello", auto_paste=auto_paste)
        canonical_feedback = "<paste_failed>" if feedback == PASTE_FAILED else feedback
        delivery_cases.append(
            {
                "name": name,
                "auto_paste": auto_paste,
                "paste_ok": paste_ok,
                "copy_ok": copy_ok,
                "expected_ok": ok,
                "expected_feedback": canonical_feedback,
            }
        )

    copy_last_empty = TranscriptDelivery().copy_last()
    d_retry = TranscriptDelivery()
    with (
        patch("utils.hotkey.paste_transcript", return_value=True),
        patch("utils.hotkey.copy_transcript", return_value=True),
    ):
        d_retry.deliver("Saved text", auto_paste=True)
        copy_last_ok = d_retry.copy_last()
    with patch("utils.hotkey.copy_transcript", return_value=False):
        copy_last_fail = d_retry.copy_last()

    return {
        "pasted": PASTED,
        "copied": COPIED,
        "test_complete": TEST_COMPLETE,
        "paste_failed_macos": "Copied — press ⌘V",
        "paste_failed_other": "Copied — press Ctrl+V",
        "copy_failed": COPY_FAILED,
        "paste_failed_hint": DELIVERY_HINTS[PASTE_FAILED],
        "copy_failed_hint": DELIVERY_HINTS[COPY_FAILED],
        "copy_last_empty": copy_last_empty,
        "copy_last_ok": copy_last_ok,
        "copy_last_fail": copy_last_fail,
        "done_status": [
            {
                "feedback": feedback,
                "app": app,
                "seconds": seconds,
                "expected": done_status(feedback, app=app, seconds=seconds),
            }
            for feedback, app, seconds in done_status_inputs
        ],
        "transcription_time_enabled": show_rtf_cases,
        "delivery_outcomes": delivery_cases,
    }


def _export_animation_contracts() -> dict[str, Any]:
    anim = AnimationLogic()
    level_inputs = [0.004, 0.08, 0.35, 0.60, 0.25, 0.05]
    for sample in level_inputs:
        anim.update_level(sample)
        anim.update_agc()

    t = 1.37
    return {
        "height_factors": AnimationLogic.get_height_factors(),
        "listening_bar_rest_heights": list(LISTENING_BAR_HEIGHTS),
        "listening_level": [
            {"rms": rms, "expected": listening_level(rms)}
            for rms in (-0.1, 0.0, 0.0015, 0.005, 0.010, 0.5)
        ],
        "listening_bar_heights": [
            {"level": lvl, "expected": list(listening_bar_heights(lvl))}
            for lvl in (0.0, 0.5, 1.0, 1.5)
        ],
        "trace": {
            "level_inputs": level_inputs,
            "t": t,
            "min_height": float(BAR_MIN_HEIGHT),
            "max_height": float(BAR_MAX_HEIGHT),
            "listening_rms_probe": 0.006,
            "expected_listening_settled": anim.listening_is_settled(0.006),
            "expected_listening_frame": list(anim.listening_frame_heights()),
            "expected_recording_normalized": list(
                anim.calculate_frame_normalized(t, "RECORDING")
            ),
            "expected_listening_normalized": list(
                anim.calculate_frame_normalized(t, "LISTENING")
            ),
            "expected_recording_heights": list(
                anim.calculate_frame_heights(
                    t,
                    "RECORDING",
                    min_height=BAR_MIN_HEIGHT,
                    max_height=BAR_MAX_HEIGHT,
                )
            ),
        },
    }


def _export_overlay_style_contracts() -> dict[str, Any]:
    layout_inputs = [
        ("fixed-basic", 120.0, 0.0, 0.0, 0.0, False, float(OVERLAY_WIDTH), float(OVERLAY_WIDTH)),
        ("with-meta-and-dismiss", 200.0, 48.0, 14.0, 0.0, False, float(OVERLAY_WIDTH), float(OVERLAY_WIDTH)),
        ("grow-within-cap", 360.0, 48.0, 14.0, 0.0, True, 700.0, float(OVERLAY_WIDTH)),
        ("grow-capped", 900.0, 48.0, 14.0, 0.0, True, 600.0, float(OVERLAY_WIDTH)),
        ("max-smaller-than-base", 500.0, 0.0, 0.0, 0.0, True, 300.0, float(OVERLAY_WIDTH)),
        ("wide-badge-pushes-text", 120.0, 0.0, 0.0, 96.0, False, float(OVERLAY_WIDTH), float(OVERLAY_WIDTH)),
    ]
    layout_cases = []
    for name, tw, mw, dw, sw, grow, max_w, base_w in layout_inputs:
        layout = compute_overlay_layout(
            text_width=tw,
            meta_width=mw,
            dismiss_width=dw,
            status_width=sw,
            grow=grow,
            max_width=max_w,
            base_width=base_w,
        )
        layout_cases.append(
            {
                "name": name,
                "text_width": tw,
                "meta_width": mw,
                "dismiss_width": dw,
                "status_width": sw,
                "grow": grow,
                "max_width": max_w,
                "base_width": base_w,
                "expected": {
                    "width": layout.width,
                    "status_x": layout.status_x,
                    "text_x": layout.text_x,
                    "text_width": layout.text_width,
                    "meta_x": layout.meta_x,
                    "dismiss_x": layout.dismiss_x,
                },
            }
        )

    shimmer_modes = ("none", "level", "low", "line", "phase1", "phase2")
    return {
        "bar_area_width": float(BAR_AREA_WIDTH),
        "phase_step_width": float(PHASE_STEP_WIDTH),
        "bar_colors": dict(sorted(BAR_COLORS.items())),
        "phase_step_colors": {
            k: list(v) for k, v in sorted(PHASE_STEP_COLORS.items())
        },
        "badge_tones": {k: list(v) for k, v in sorted(BADGE_TONES.items())},
        "hex_to_rgb": [
            {"hex": hx, "expected": list(hex_to_rgb(hx))}
            for hx in ("#1b1b1b", "#6fd39b", "#f08a8a", "ececec")
        ],
        "shimmer_track": [
            {
                "mode": mode,
                "expected": list(track) if (track := shimmer_track(mode)) is not None else None,
            }
            for mode in shimmer_modes
        ],
        "layout_cases": layout_cases,
    }


def _export_overlay_text_contracts() -> dict[str, Any]:
    prefix_inputs: tuple[tuple[str, int], ...] = (
        ("  hello   world\n\tagain ", 11),
        ("  Über   Straße\n\tgehen ", 8),
        ("hello", 0),
        ("   ", 5),
    )
    duration_inputs = (-5.0, 0.0, 5.9, 65.2, 3661.0)
    interim_inputs: tuple[tuple[str, int, bool], ...] = (
        ("", 45, True),
        ("   ", 45, True),
        ("  Hallo   Welt\n  heute ", 45, True),
        ("eins zwei drei vier fünf sechs sieben", 18, True),
        ("eins zwei drei vier fünf sechs sieben", 18, False),
        ("abcdef", 3, True),
        ("Übermäßig lange Grüße aus München", 20, True),
    )
    split_inputs: tuple[tuple[str, str, int, bool], ...] = (
        ("Hallo Welt", "", 45, True),
        ("Hallo Welt heute", "Hallo Welt", 45, True),
        ("Hallo Welt", "Hallo Welt", 45, True),
        ("Andere Worte hier", "Hallo Welt", 45, True),
        ("eins zwei drei vier fünf sechs sieben", "eins zwei drei vier", 20, True),
        ("eins zwei drei vier fünf sechs sieben", "eins zwei drei vier", 20, False),
    )

    wrap_steps = [
        ("Hallo Welt", 14, 2),
        ("Hallo Welt heute morgen", 14, 2),
        ("Hallo Welt heute morgen scheint die Sonne", 14, 2),
        ("Hallo Welt heute morgen regnet es stark", 14, 2),
        ("Hallo Welt heute morgen regnet es stark.", 14, 2),
        ("   ", 14, 2),
        ("日本語テスト文字列確認用データ", 6, 2),
        ("日本語テスト文字列確認用データ追加", 6, 2),
    ]
    wrap_trace = []
    prev: InterimLines | None = None
    for text, limit, max_lines in wrap_steps:
        if text == "日本語テスト文字列確認用データ":
            prev = None
        res = wrap_interim_text(
            text,
            prev,
            lambda s, lim=limit: len(s) <= lim,
            max_lines=max_lines,
        )
        wrap_trace.append(
            {
                "text": text,
                "reset_previous": text == "日本語テスト文字列確認用データ",
                "char_limit": limit,
                "max_lines": max_lines,
                "expected_lines": [list(pair) for pair in res.lines],
                "expected_source": res.source,
                "expected_start": res.start,
            }
        )
        prev = res

    return {
        "normalized_text_prefix": [
            {"text": text, "max_chars": max_chars, "expected": normalized_text_prefix(text, max_chars=max_chars)}
            for text, max_chars in prefix_inputs
        ],
        "format_recording_duration": [
            {"seconds": sec, "expected": format_recording_duration(sec)}
            for sec in duration_inputs
        ],
        "format_recording_interim_text": [
            {
                "text": text,
                "max_chars": max_chars,
                "ellipsis": ellipsis,
                "expected": format_recording_interim_text(text, max_chars, ellipsis=ellipsis),
            }
            for text, max_chars, ellipsis in interim_inputs
        ],
        "split_recording_interim_text": [
            {
                "text": text,
                "previous_text": prev_text,
                "max_chars": max_chars,
                "ellipsis": ellipsis,
                "expected": list(
                    split_recording_interim_text(
                        text, prev_text, max_chars, ellipsis=ellipsis
                    )
                ),
            }
            for text, prev_text, max_chars, ellipsis in split_inputs
        ],
        "wrap_interim_text_trace": wrap_trace,
    }


def _export_daemon_status_feedback_contracts() -> dict[str, Any]:
    infer_inputs: tuple[str | None, ...] = (
        None,
        "   ",
        "DEEPGRAM_API_KEY not configured",
        "Already recording",
        "Transcription timed out after 30s",
        "Unbekannter Refine-Provider 'bar'",
        "Input Monitoring permission missing",
        "Accessibility access permission required",
        "Microphone permission denied",
        "WASAPI audio input device disconnected",
        "WebSocket connection failed",
        "No module named 'sounddevice'",
        "module 'x' has no attribute 'y'",
    )
    infer_cases = []
    for raw in infer_inputs:
        inferred = infer_daemon_status_error(raw)
        infer_cases.append(
            {
                "input": raw,
                "expected_code": inferred.code.value if inferred is not None else None,
                "expected_detail": inferred.detail if inferred is not None else None,
            }
        )

    label_inputs: tuple[tuple[str, str | None, bool, int], ...] = (
        ("idle", None, True, 80),
        ("loading", None, True, 80),
        ("loading", "Starting up daemon", True, 80),
        ("loading", "Warming up whisper", True, 80),
        ("loading", "Checking microphone input", True, 80),
        ("loading", "Opening websocket connection", True, 80),
        ("loading", "Loading whisper-large-v3.", False, 80),
        ("loading", "Custom step.", True, 80),
        ("loading", "Custom step.", False, 80),
        ("recording", "Hallo   Welt", True, 16),
        ("recording", "Hallo   Welt", False, 80),
        ("done", "Transcript pasted into Mail · 1.2 s", True, 80),
        ("done", "Transcript pasted into Mail · 1.2 s", False, 80),
        ("done", "Copied to clipboard · 0.4 s", False, 80),
        ("done", "Test complete", False, 80),
        ("no_speech", None, True, 80),
        ("no_speech", "empty transcript", True, 80),
        ("no_speech", "Too quiet.", True, 80),
        ("error", None, True, 80),
        ("error", "OPENAI_API_KEY not set", True, 80),
        ("error", "module 'x' has no attribute 'y'", True, 80),
    )

    hint_inputs: tuple[tuple[str, str | None, int], ...] = (
        ("idle", None, 120),
        ("loading", None, 120),
        ("loading", "Starting up", 120),
        ("loading", "Warming up model", 120),
        ("loading", "Loading whisper-small", 120),
        ("loading", "Reconnecting websocket", 120),
        ("loading", "Downloading tokenizer", 120),
        ("no_speech", None, 120),
        ("no_speech", "empty transcript", 120),
        ("error", "DEEPGRAM_API_KEY missing", 120),
        ("error", "Microphone permission denied", 120),
        ("error", COPY_FAILED, 120),
        ("error", "<paste_failed>", 120),
        ("error", "unexpected custom error", 120),
    )
    hint_cases = []
    for state, raw_text, max_chars in hint_inputs:
        effective_text = PASTE_FAILED if raw_text == "<paste_failed>" else raw_text
        hint_cases.append(
            {
                "state": state,
                "text": raw_text,
                "max_chars": max_chars,
                "expected": build_daemon_status_hint(
                    state, effective_text, max_chars=max_chars
                ),
            }
        )

    tray_inputs: tuple[tuple[str, str | None, str, int], ...] = (
        ("idle", None, "PulseScribe", 128),
        ("loading", "Warming up local model", "PulseScribe", 128),
        ("no_speech", "empty transcript", "PulseScribe", 128),
        ("error", "OPENAI_API_KEY not set", "PulseScribe", 128),
        ("error", "OPENAI_API_KEY not set", "PulseScribe", 40),
    )

    return {
        "normalize_text": [
            {"input": raw, "expected": normalize_daemon_status_text(raw)}
            for raw in (None, "", "  Hello \n\t  world  ")
        ],
        "infer_error": infer_cases,
        "status_labels": [
            {
                "state": state,
                "text": text,
                "prefer_detail": prefer_detail,
                "max_chars": max_chars,
                "expected": build_daemon_status_label(
                    state, text, prefer_detail=prefer_detail, max_chars=max_chars
                ),
            }
            for state, text, prefer_detail, max_chars in label_inputs
        ],
        "status_hints": hint_cases,
        "tray_titles": [
            {
                "state": state,
                "text": text,
                "app_name": app_name,
                "max_chars": max_chars,
                "expected": build_daemon_tray_title(
                    state, text, app_name=app_name, max_chars=max_chars
                ),
            }
            for state, text, app_name, max_chars in tray_inputs
        ],
    }


def _export_overlay_feedback_contracts() -> dict[str, Any]:
    transcribing_inputs: tuple[tuple[str | None, bool], ...] = (
        (None, False),
        ("Deepgram", False),
        (None, True),
        ("", True),
        ("Deepgram…", True),
        ("Whisper...", True),
    )

    describe_inputs: tuple[tuple[str | None, str | None, int], ...] = (
        ("IDLE", None, 72),
        ("LISTENING", None, 72),
        ("RECORDING", "ignored interim", 72),
        ("LOADING", None, 72),
        ("LOADING", "Warming up local model", 72),
        ("LOADING", "Loading a very long local model name that exceeds thirty characters", 30),
        ("TRANSCRIBING", "Deepgram", 72),
        ("TRANSCRIBING", "Deepgram, then refining", 72),
        ("REFINING", None, 72),
        ("DONE", TEST_COMPLETE, 72),
        ("DONE", PASTED, 72),
        ("DONE", "Transcript pasted into Mail · 1.2 s", 72),
        ("DONE", "Copied to clipboard · 0.4 s", 72),
        ("DONE", "unrecognized done text", 72),
        ("NO_SPEECH", None, 72),
        ("ERROR", None, 72),
        ("ERROR", "DEEPGRAM_API_KEY missing", 72),
        ("ERROR", "WASAPI audio input device unavailable", 72),
        ("ERROR", "Error: custom failure", 72),
        ("ERROR", "ERROR —   boom", 72),
        ("ERROR", COPY_FAILED, 72),
        ("ERROR", "<paste_failed>", 72),
    )

    describe_cases = []
    for state, raw_text, max_chars in describe_inputs:
        effective_text = PASTE_FAILED if raw_text == "<paste_failed>" else raw_text
        content = describe_overlay_content(state, effective_text, max_chars=max_chars)
        canonical_text = "<paste_failed>" if content.text == PASTE_FAILED else content.text
        describe_cases.append(
            {
                "state": state,
                "text": raw_text,
                "max_chars": max_chars,
                "expected": {
                    "bars": content.bars,
                    "text": canonical_text,
                    "emphasized": content.emphasized,
                    "badge": content.badge,
                    "tone": content.tone or None,
                    "dismissible": content.dismissible,
                    "meta": content.meta,
                    "action": content.action or None,
                },
            }
        )

    return {
        "transcribing_status": [
            {
                "detail": detail,
                "refine": refine,
                "expected": transcribing_status(detail, refine=refine),
            }
            for detail, refine in transcribing_inputs
        ],
        "describe_content": describe_cases,
    }


def build_core_contracts() -> dict[str, Any]:
    """Build deterministic core contract fixtures from the live Python code."""
    return {
        "schema_version": 5,
        "app_states": [state.value for state in AppState],
        "daemon_error_codes": [code.value for code in DaemonErrorCode],
        "message_types": [msg.name.lower() for msg in MessageType],
        "parse_bool": [
            {"input": value, "expected": parse_bool(value)}
            for value in _PARSE_BOOL_INPUTS
        ],
        "parse_env_line": [
            {
                "input": raw,
                "key": parse_env_line(raw)[0],
                "value": parse_env_line(raw)[1],
            }
            for raw in _PARSE_ENV_LINE_INPUTS
        ],
        "format_env_assignment": [
            {"key": key, "value": value, "expected": _format_env_assignment(key, value)}
            for key, value in _FORMAT_ENV_INPUTS
        ],
        "env_updates": [
            _export_env_update_case(name, lines, updates, preserve)
            for name, lines, updates, preserve in _ENV_UPDATE_CASES
        ],
        "dotenv_resolve": _export_dotenv_resolve(),
        "onboarding_choices": [choice.value for choice in OnboardingChoice],
        "onboarding_steps": {
            "total_steps": total_steps(),
            "steps": [
                {
                    "value": step.value,
                    "next": next_step(step).value,
                    "prev": prev_step(step).value,
                    "step_index": step_index(step),
                }
                for step in ONBOARDING_ORDER
            ],
        },
        "vocabulary": _export_vocabulary_contracts(),
        "prompts": _export_prompts_contracts(),
        "history": _export_history_contracts(),
        "hotkeys": _export_hotkey_contracts(),
        "hold_state_trace": _export_hold_state_contracts(),
        "transcript_delivery": _export_transcript_delivery_contracts(),
        "animation": _export_animation_contracts(),
        "overlay_style": _export_overlay_style_contracts(),
        "overlay_text": _export_overlay_text_contracts(),
        "daemon_status_feedback": _export_daemon_status_feedback_contracts(),
        "overlay_feedback": _export_overlay_feedback_contracts(),
    }


def render_core_contracts_json() -> str:
    return json.dumps(build_core_contracts(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    GOLDEN_FILE.write_text(render_core_contracts_json(), encoding="utf-8")
    print(f"Wrote {GOLDEN_FILE.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
