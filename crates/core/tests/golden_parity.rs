use std::fs;
use std::path::PathBuf;

use pulsescribe_core::{AppState, DaemonErrorCode};
use serde::Deserialize;

const SUPPORTED_SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CoreContracts {
    schema_version: u32,
    app_states: Vec<String>,
    daemon_error_codes: Vec<String>,
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
