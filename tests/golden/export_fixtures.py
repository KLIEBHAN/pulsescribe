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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from refine.prompts import (  # noqa: E402
    CONTEXT_PROMPTS,
    DEFAULT_APP_CONTEXTS,
    VOICE_COMMANDS_INSTRUCTION,
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
from utils.state import AppState, DaemonErrorCode  # noqa: E402
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


def build_core_contracts() -> dict[str, Any]:
    """Build deterministic core contract fixtures from the live Python code."""
    return {
        "schema_version": 3,
        "app_states": [state.value for state in AppState],
        "daemon_error_codes": [code.value for code in DaemonErrorCode],
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
    }


def render_core_contracts_json() -> str:
    return json.dumps(build_core_contracts(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    GOLDEN_FILE.write_text(render_core_contracts_json(), encoding="utf-8")
    print(f"Wrote {GOLDEN_FILE.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
