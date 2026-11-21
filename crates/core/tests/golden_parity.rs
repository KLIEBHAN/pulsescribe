use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

use pulsescribe_core::env::{
    apply_env_updates_to_lines, format_env_assignment, parse_bool, parse_dotenv_str, parse_env_line,
};
use pulsescribe_core::onboarding::ONBOARDING_ORDER;
use pulsescribe_core::{AppState, DaemonErrorCode, OnboardingChoice, OnboardingStep};
use serde::Deserialize;

const SUPPORTED_SCHEMA_VERSION: u32 = 2;

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
