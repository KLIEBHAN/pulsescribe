use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

use pulsescribe_core::env::{
    apply_env_updates_to_lines, format_env_assignment, parse_bool, parse_dotenv_str, parse_env_line,
};
use pulsescribe_core::history::{
    format_transcripts_for_display, format_transcripts_for_welcome,
    merge_recent_transcript_entries, select_recent_lines_within_bytes,
    DISPLAY_TRANSCRIPTS_EMPTY_MESSAGE, WELCOME_TRANSCRIPTS_EMPTY_MESSAGE,
};
use pulsescribe_core::onboarding::ONBOARDING_ORDER;
use pulsescribe_core::prompts::{
    build_prompt_overrides_from_editor_state, format_app_mappings, normalize_prompt_editor_context,
    parse_and_merge_prompts_toml, parse_app_mappings, KNOWN_CONTEXTS,
};
use pulsescribe_core::vocabulary::{
    analyze_vocabulary_text, parse_vocabulary_text, split_vocabulary_text,
};
use pulsescribe_core::{
    AppState, DaemonErrorCode, OnboardingChoice, OnboardingStep, PromptEditorContext,
    PromptOverrides, PromptsConfig, TranscriptEntry,
};
use serde::Deserialize;
use serde_json::{Map, Value};

const SUPPORTED_SCHEMA_VERSION: u32 = 3;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CoreContracts {
    schema_version: u32,
    app_states: Vec<String>,
    daemon_error_codes: Vec<String>,
    parse_bool: Vec<ParseBoolCase>,
    parse_env_line: Vec<ParseEnvLineCase>,
    format_env_assignment: Vec<FormatEnvCase>,
    env_updates: Vec<EnvUpdateCase>,
    dotenv_resolve: DotenvResolveCase,
    onboarding_choices: Vec<String>,
    onboarding_steps: OnboardingStepsContract,
    vocabulary: VocabularyContracts,
    prompts: PromptsContracts,
    history: HistoryContracts,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct VocabularyContracts {
    split_text: Vec<VocabSplitCase>,
    analyze_text: Vec<VocabAnalyzeCase>,
    parse_text: Vec<VocabParseCase>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct VocabSplitCase {
    input: Option<String>,
    expected: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct VocabAnalyzeCase {
    name: String,
    input: Option<String>,
    keywords: Vec<String>,
    issues: Vec<String>,
    input_count: usize,
    duplicate_count: usize,
    exceeds_local_limit: bool,
    exceeds_deepgram_limit: bool,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct VocabParseCase {
    name: String,
    input: String,
    expected_data: Map<String, Value>,
    expected_issues: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PromptsContracts {
    known_contexts: Vec<String>,
    defaults: PromptDefaultsContract,
    editor_contexts: Vec<PromptEditorContextContract>,
    normalize_editor_context: Vec<NormalizeEditorContextCase>,
    format_app_mappings: FormatAppMappingsCase,
    parse_app_mappings: ParseAppMappingsCase,
    filter_overrides: FilterOverridesCase,
    editor_build_overrides: EditorBuildOverridesCase,
    toml_roundtrip: TomlRoundtripCase,
    refine_prompt_cases: Vec<RefinePromptCase>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PromptDefaultsContract {
    voice_commands_instruction: String,
    prompts: BTreeMap<String, String>,
    app_contexts: BTreeMap<String, String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PromptEditorContextContract {
    key: String,
    label: String,
    description: String,
    placeholder: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct NormalizeEditorContextCase {
    input: Option<String>,
    expected: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct FormatAppMappingsCase {
    input: BTreeMap<String, String>,
    expected: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ParseAppMappingsCase {
    input: String,
    expected: BTreeMap<String, String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct FilterOverridesCase {
    voice_commands_instruction: String,
    prompts: BTreeMap<String, String>,
    app_contexts: BTreeMap<String, String>,
    expected_voice_commands_instruction: Option<String>,
    expected_prompts: BTreeMap<String, String>,
    expected_app_contexts: BTreeMap<String, String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct EditorBuildOverridesCase {
    existing_code_prompt: String,
    drafts: BTreeMap<String, String>,
    contexts: Vec<String>,
    expected_voice_commands_instruction: Option<String>,
    expected_prompts: BTreeMap<String, String>,
    expected_app_contexts: BTreeMap<String, String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct TomlRoundtripCase {
    overrides_voice_commands: String,
    overrides_prompts: BTreeMap<String, String>,
    overrides_app_contexts: BTreeMap<String, String>,
    serialized_toml: String,
    expected_merged_voice_commands: String,
    expected_merged_prompts: BTreeMap<String, String>,
    expected_merged_app_contexts: BTreeMap<String, String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RefinePromptCase {
    context: String,
    voice_commands: bool,
    expected: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct HistoryContracts {
    display_empty_message: String,
    welcome_empty_message: String,
    select_recent_lines: Vec<SelectRecentLinesCase>,
    merge_recent_entries: Vec<MergeRecentEntriesCase>,
    format_entries: Vec<Value>,
    format_display_empty: String,
    format_display_newest_first: String,
    format_display_oldest_first: String,
    format_welcome_empty: String,
    format_welcome_newest_first: String,
    format_welcome_oldest_first: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SelectRecentLinesCase {
    name: String,
    lines: Vec<String>,
    max_size_bytes: usize,
    expected: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct MergeRecentEntriesCase {
    name: String,
    previous: Vec<Value>,
    appended: Vec<Value>,
    max_entries: usize,
    expected_texts: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct DotenvResolveCase {
    process_env: BTreeMap<String, String>,
    content: String,
    expected: BTreeMap<String, String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ParseBoolCase {
    input: Option<String>,
    expected: Option<bool>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ParseEnvLineCase {
    input: String,
    key: Option<String>,
    value: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct FormatEnvCase {
    key: String,
    value: String,
    expected: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct EnvUpdateCase {
    name: String,
    lines: Vec<String>,
    updates: Vec<(String, Option<String>)>,
    preserve_following_duplicates: bool,
    expected_lines: Vec<String>,
    expected_changed: bool,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct OnboardingStepsContract {
    total_steps: usize,
    steps: Vec<OnboardingStepCase>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct OnboardingStepCase {
    value: String,
    next: String,
    prev: String,
    step_index: usize,
}

fn load_contracts() -> CoreContracts {
    let path =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/golden/core_contracts.json");
    let raw = fs::read_to_string(&path)
        .unwrap_or_else(|err| panic!("failed to read {}: {err}", path.display()));
    let contracts: CoreContracts = serde_json::from_str(&raw).expect("valid core_contracts.json");
    assert_eq!(
        contracts.schema_version, SUPPORTED_SCHEMA_VERSION,
        "unsupported core_contracts.json schema_version"
    );
    contracts
}

#[test]
fn app_states_match_python_golden_fixture() {
    let contracts = load_contracts();
    assert!(!contracts.app_states.is_empty());

    let expected = [
        AppState::Idle,
        AppState::Loading,
        AppState::Listening,
        AppState::Recording,
        AppState::Transcribing,
        AppState::Refining,
        AppState::Done,
        AppState::NoSpeech,
        AppState::Error,
    ];

    let rust_values: Vec<String> = expected.iter().map(|s| s.as_str().to_string()).collect();
    assert_eq!(rust_values, contracts.app_states);

    for raw in &contracts.app_states {
        let raw_value = serde_json::Value::String(raw.clone());
        let parsed: AppState = serde_json::from_value(raw_value.clone())
            .unwrap_or_else(|err| panic!("failed to deserialize AppState {raw:?}: {err}"));
        assert_eq!(parsed.as_str(), raw);
        let serialized = serde_json::to_value(parsed)
            .unwrap_or_else(|err| panic!("failed to serialize AppState {parsed:?}: {err}"));
        assert_eq!(serialized, raw_value);
    }
}

#[test]
fn daemon_error_codes_match_python_golden_fixture() {
    let contracts = load_contracts();
    assert!(!contracts.daemon_error_codes.is_empty());

    let expected = [
        DaemonErrorCode::MissingApiKey,
        DaemonErrorCode::Busy,
        DaemonErrorCode::Timeout,
        DaemonErrorCode::InvalidProvider,
        DaemonErrorCode::InputMonitoring,
        DaemonErrorCode::AccessibilityPermission,
        DaemonErrorCode::MicrophonePermission,
        DaemonErrorCode::MicrophoneUnavailable,
        DaemonErrorCode::Connection,
        DaemonErrorCode::MissingDependency,
        DaemonErrorCode::Unknown,
    ];

    let rust_values: Vec<String> = expected.iter().map(|c| c.as_str().to_string()).collect();
    assert_eq!(rust_values, contracts.daemon_error_codes);

    for raw in &contracts.daemon_error_codes {
        let raw_value = serde_json::Value::String(raw.clone());
        let parsed: DaemonErrorCode = serde_json::from_value(raw_value.clone())
            .unwrap_or_else(|err| panic!("failed to deserialize DaemonErrorCode {raw:?}: {err}"));
        assert_eq!(parsed.as_str(), raw);
        let serialized = serde_json::to_value(parsed)
            .unwrap_or_else(|err| panic!("failed to serialize DaemonErrorCode {parsed:?}: {err}"));
        assert_eq!(serialized, raw_value);
    }
}

#[test]
fn env_contracts_match_python_golden_fixture() {
    let contracts = load_contracts();

    for case in &contracts.parse_bool {
        assert_eq!(
            parse_bool(case.input.as_deref()),
            case.expected,
            "parse_bool mismatch for {:?}",
            case.input
        );
    }

    for case in &contracts.parse_env_line {
        let expected = match (&case.key, &case.value) {
            (Some(k), Some(v)) => Some((k.clone(), v.clone())),
            (None, None) => None,
            other => panic!("unexpected half-null fixture entry: {other:?}"),
        };
        assert_eq!(
            parse_env_line(&case.input),
            expected,
            "parse_env_line mismatch for {:?}",
            case.input
        );
    }

    for case in &contracts.format_env_assignment {
        let formatted = format_env_assignment(&case.key, &case.value).unwrap();
        assert_eq!(formatted, case.expected);
    }

    for case in &contracts.env_updates {
        let (actual_lines, actual_changed) = apply_env_updates_to_lines(
            &case.lines,
            &case.updates,
            case.preserve_following_duplicates,
        )
        .unwrap_or_else(|err| panic!("case '{}' failed: {err}", case.name));
        assert_eq!(actual_lines, case.expected_lines, "case '{}'", case.name);
        assert_eq!(
            actual_changed, case.expected_changed,
            "changed flag mismatch for case '{}'",
            case.name
        );
    }

    let resolved = parse_dotenv_str(
        &contracts.dotenv_resolve.content,
        &contracts.dotenv_resolve.process_env,
    );
    assert_eq!(resolved, contracts.dotenv_resolve.expected);
}

#[test]
fn onboarding_contracts_match_python_golden_fixture() {
    let contracts = load_contracts();

    let expected_choices = [
        OnboardingChoice::Fast,
        OnboardingChoice::Private,
        OnboardingChoice::Advanced,
    ];
    let rust_choices: Vec<String> = expected_choices
        .iter()
        .map(|c| c.as_str().to_string())
        .collect();
    assert_eq!(rust_choices, contracts.onboarding_choices);
    for raw in &contracts.onboarding_choices {
        let raw_value = serde_json::Value::String(raw.clone());
        let parsed: OnboardingChoice = serde_json::from_value(raw_value.clone()).unwrap();
        assert_eq!(OnboardingChoice::parse(raw), Some(parsed));
        assert_eq!(serde_json::to_value(parsed).unwrap(), raw_value);
    }

    assert_eq!(
        OnboardingStep::total_steps(),
        contracts.onboarding_steps.total_steps
    );
    let rust_steps: Vec<String> = ONBOARDING_ORDER
        .iter()
        .map(|s| s.as_str().to_string())
        .collect();
    let fixture_steps: Vec<String> = contracts
        .onboarding_steps
        .steps
        .iter()
        .map(|s| s.value.clone())
        .collect();
    assert_eq!(rust_steps, fixture_steps);

    for case in &contracts.onboarding_steps.steps {
        let raw_value = serde_json::Value::String(case.value.clone());
        let step: OnboardingStep = serde_json::from_value(raw_value.clone()).unwrap();
        assert_eq!(OnboardingStep::parse(&case.value), Some(step));
        assert_eq!(serde_json::to_value(step).unwrap(), raw_value);
        assert_eq!(step.next_step().as_str(), case.next);
        assert_eq!(step.prev_step().as_str(), case.prev);
        assert_eq!(step.step_index(), case.step_index);
    }
}

#[test]
fn vocabulary_contracts_match_python_golden_fixture() {
    let contracts = load_contracts();

    for case in &contracts.vocabulary.split_text {
        assert_eq!(
            split_vocabulary_text(case.input.as_deref()),
            case.expected,
            "split_vocabulary_text mismatch for {:?}",
            case.input
        );
    }

    for case in &contracts.vocabulary.analyze_text {
        let actual = analyze_vocabulary_text(case.input.as_deref());
        assert_eq!(actual.keywords, case.keywords, "case '{}'", case.name);
        assert_eq!(actual.issues, case.issues, "case '{}'", case.name);
        assert_eq!(actual.input_count, case.input_count, "case '{}'", case.name);
        assert_eq!(
            actual.duplicate_count, case.duplicate_count,
            "case '{}'",
            case.name
        );
        assert_eq!(
            actual.exceeds_local_limit, case.exceeds_local_limit,
            "case '{}'",
            case.name
        );
        assert_eq!(
            actual.exceeds_deepgram_limit, case.exceeds_deepgram_limit,
            "case '{}'",
            case.name
        );
    }

    for case in &contracts.vocabulary.parse_text {
        let (actual_data, actual_issues) = parse_vocabulary_text(&case.input);
        assert_eq!(actual_data, case.expected_data, "case '{}'", case.name);
        assert_eq!(actual_issues, case.expected_issues, "case '{}'", case.name);
    }
}

#[test]
fn prompts_contracts_match_python_golden_fixture() {
    let contracts = load_contracts();
    let p = &contracts.prompts;

    let rust_known: Vec<String> = KNOWN_CONTEXTS.iter().map(|s| (*s).to_string()).collect();
    assert_eq!(rust_known, p.known_contexts);

    let defaults = PromptsConfig::defaults();
    assert_eq!(
        defaults.voice_commands_instruction,
        p.defaults.voice_commands_instruction
    );
    assert_eq!(defaults.prompts, p.defaults.prompts);
    assert_eq!(defaults.app_contexts, p.defaults.app_contexts);

    let rust_editor_keys: Vec<String> = PromptEditorContext::ALL
        .iter()
        .map(|c| c.as_str().to_string())
        .collect();
    let fixture_editor_keys: Vec<String> =
        p.editor_contexts.iter().map(|c| c.key.clone()).collect();
    assert_eq!(rust_editor_keys, fixture_editor_keys);

    for case in &p.editor_contexts {
        let raw_value = Value::String(case.key.clone());
        let parsed: PromptEditorContext =
            serde_json::from_value(raw_value.clone()).unwrap_or_else(|err| {
                panic!(
                    "failed to deserialize PromptEditorContext {:?}: {err}",
                    case.key
                )
            });
        assert_eq!(serde_json::to_value(parsed).unwrap(), raw_value);
        assert_eq!(parsed.label(), case.label);
        assert_eq!(parsed.description(), case.description);
        assert_eq!(parsed.placeholder(), case.placeholder);
    }

    for case in &p.normalize_editor_context {
        assert_eq!(
            normalize_prompt_editor_context(case.input.as_deref()),
            case.expected,
            "normalize_prompt_editor_context mismatch for {:?}",
            case.input
        );
    }

    assert_eq!(
        format_app_mappings(&p.format_app_mappings.input),
        p.format_app_mappings.expected
    );
    assert_eq!(
        parse_app_mappings(&p.parse_app_mappings.input),
        p.parse_app_mappings.expected
    );

    let filter_input = PromptsConfig {
        voice_commands_instruction: p.filter_overrides.voice_commands_instruction.clone(),
        prompts: p.filter_overrides.prompts.clone(),
        app_contexts: p.filter_overrides.app_contexts.clone(),
    };
    let filtered = filter_input.filter_overrides(&defaults);
    assert_eq!(
        filtered.voice_commands_instruction,
        p.filter_overrides.expected_voice_commands_instruction
    );
    assert_eq!(filtered.prompts, p.filter_overrides.expected_prompts);
    assert_eq!(
        filtered.app_contexts,
        p.filter_overrides.expected_app_contexts
    );

    let mut existing = PromptsConfig::defaults();
    existing.prompts.insert(
        "code".to_string(),
        p.editor_build_overrides.existing_code_prompt.clone(),
    );
    let ctx_refs: Vec<&str> = p
        .editor_build_overrides
        .contexts
        .iter()
        .map(String::as_str)
        .collect();
    let built = build_prompt_overrides_from_editor_state(
        &existing,
        &p.editor_build_overrides.drafts,
        Some(&ctx_refs),
        Some(&defaults),
    );
    assert_eq!(
        built.voice_commands_instruction,
        p.editor_build_overrides.expected_voice_commands_instruction
    );
    assert_eq!(built.prompts, p.editor_build_overrides.expected_prompts);
    assert_eq!(
        built.app_contexts,
        p.editor_build_overrides.expected_app_contexts
    );

    // Parse Python-generated TOML AND assert Rust's own TOML serializer matches and round-trips.
    let parsed_from_python = parse_and_merge_prompts_toml(&p.toml_roundtrip.serialized_toml)
        .expect("Python serialized prompts.toml must parse in Rust");
    assert_eq!(
        parsed_from_python.voice_commands_instruction,
        p.toml_roundtrip.expected_merged_voice_commands
    );
    assert_eq!(
        parsed_from_python.prompts,
        p.toml_roundtrip.expected_merged_prompts
    );
    assert_eq!(
        parsed_from_python.app_contexts,
        p.toml_roundtrip.expected_merged_app_contexts
    );

    let rust_overrides = PromptOverrides {
        voice_commands_instruction: Some(p.toml_roundtrip.overrides_voice_commands.clone()),
        prompts: p.toml_roundtrip.overrides_prompts.clone(),
        app_contexts: p.toml_roundtrip.overrides_app_contexts.clone(),
    };
    let rust_toml = rust_overrides.to_toml_string();
    assert_eq!(rust_toml, p.toml_roundtrip.serialized_toml);
    let parsed_from_rust =
        parse_and_merge_prompts_toml(&rust_toml).expect("Rust serialized prompts.toml must parse");
    assert_eq!(parsed_from_rust, parsed_from_python);

    for case in &p.refine_prompt_cases {
        assert_eq!(
            defaults.refine_prompt_for_context(&case.context, case.voice_commands),
            case.expected,
            "refine_prompt mismatch for context={:?} vc={}",
            case.context,
            case.voice_commands
        );
    }
}

#[test]
fn history_contracts_match_python_golden_fixture() {
    let contracts = load_contracts();
    let h = &contracts.history;

    assert_eq!(DISPLAY_TRANSCRIPTS_EMPTY_MESSAGE, h.display_empty_message);
    assert_eq!(WELCOME_TRANSCRIPTS_EMPTY_MESSAGE, h.welcome_empty_message);

    for case in &h.select_recent_lines {
        assert_eq!(
            select_recent_lines_within_bytes(&case.lines, case.max_size_bytes),
            case.expected,
            "select_recent_lines mismatch for case '{}'",
            case.name
        );
    }

    for case in &h.merge_recent_entries {
        let prev: Vec<TranscriptEntry> = case
            .previous
            .iter()
            .cloned()
            .filter_map(TranscriptEntry::from_json_value)
            .collect();
        let app: Vec<TranscriptEntry> = case
            .appended
            .iter()
            .cloned()
            .filter_map(TranscriptEntry::from_json_value)
            .collect();
        let merged = merge_recent_transcript_entries(&prev, &app, case.max_entries);
        let texts: Vec<String> = merged.into_iter().map(|e| e.text).collect();
        assert_eq!(texts, case.expected_texts, "merge case '{}'", case.name);
    }

    let entries: Vec<TranscriptEntry> = h
        .format_entries
        .iter()
        .cloned()
        .filter_map(TranscriptEntry::from_json_value)
        .collect();
    assert_eq!(entries.len(), h.format_entries.len());

    assert_eq!(
        format_transcripts_for_display(&[], true),
        h.format_display_empty
    );
    assert_eq!(
        format_transcripts_for_display(&entries, true),
        h.format_display_newest_first
    );
    assert_eq!(
        format_transcripts_for_display(&entries, false),
        h.format_display_oldest_first
    );
    assert_eq!(
        format_transcripts_for_welcome(&[], true),
        h.format_welcome_empty
    );
    assert_eq!(
        format_transcripts_for_welcome(&entries, true),
        h.format_welcome_newest_first
    );
    assert_eq!(
        format_transcripts_for_welcome(&entries, false),
        h.format_welcome_oldest_first
    );
}
