//! App-aware context detection and prompt resolution for LLM transcript refinement.
//!
//! Priority matches `refine/context.py`:
//! `CLI override > PULSESCRIBE_CONTEXT > Frontmost App (PULSESCRIBE_APP_CONTEXTS > prompts.toml > defaults) > default`.

use std::collections::BTreeMap;

use pulsescribe_core::prompts::{PromptsConfig, KNOWN_CONTEXTS};

use crate::http::read_env_non_empty;

pub const CONTEXT_ENV_VAR: &str = "PULSESCRIBE_CONTEXT";
pub const APP_CONTEXTS_ENV_VAR: &str = "PULSESCRIBE_APP_CONTEXTS";

/// Indicates where a detected refinement context originated.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ContextSource {
    Cli,
    Env,
    App,
    Default,
}

impl ContextSource {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Cli => "CLI",
            Self::Env => "ENV",
            Self::App => "App",
            Self::Default => "Default",
        }
    }
}

/// Resolved context name, optional frontmost application name, and resolution source.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DetectedContext {
    pub context: &'static str,
    pub app_name: Option<String>,
    pub source: ContextSource,
}

/// Normalize a context name to one of the known contexts (`default`, `email`, `chat`, `code`).
///
/// Unknown or blank values return `None` so callers safely fall back instead of silently running
/// the default prompt under a misleading custom context label.
pub fn normalize_context_value(value: Option<&str>) -> Option<&'static str> {
    let normalized = value?.trim().to_ascii_lowercase();
    if normalized.is_empty() {
        return None;
    }
    KNOWN_CONTEXTS
        .iter()
        .copied()
        .find(|known| *known == normalized)
}

/// Parse `PULSESCRIBE_APP_CONTEXTS` JSON (`{"AppName": "email"}`) into a validated map.
///
/// Malformed JSON, non-object roots, non-string entries, blank keys, and unknown context names
/// are logged and skipped without failing the refinement pipeline.
pub fn parse_app_contexts_json(raw: &str) -> BTreeMap<String, String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return BTreeMap::new();
    }

    let parsed: serde_json::Value = match serde_json::from_str(trimmed) {
        Ok(value) => value,
        Err(err) => {
            tracing::warn!(error = %err, "PULSESCRIBE_APP_CONTEXTS contains invalid JSON");
            return BTreeMap::new();
        }
    };

    let Some(object) = parsed.as_object() else {
        tracing::warn!("PULSESCRIBE_APP_CONTEXTS must be a JSON object");
        return BTreeMap::new();
    };

    let mut result: BTreeMap<String, String> = BTreeMap::new();
    for (key, value) in object {
        let Some(raw_context) = value.as_str() else {
            tracing::warn!(
                app = key,
                "ignoring non-string value in PULSESCRIBE_APP_CONTEXTS"
            );
            continue;
        };
        let normalized_key = key.trim();
        if normalized_key.is_empty() {
            continue;
        }
        let Some(normalized_ctx) = normalize_context_value(Some(raw_context)) else {
            tracing::warn!(
                app = normalized_key,
                context = raw_context,
                "ignoring unknown context in PULSESCRIBE_APP_CONTEXTS"
            );
            continue;
        };
        result.retain(|existing, _| !existing.eq_ignore_ascii_case(normalized_key));
        result.insert(normalized_key.to_string(), normalized_ctx.to_string());
    }
    result
}

/// Map a frontmost application name to a known context (case-insensitive).
///
/// Priority: `env_overrides` (`PULSESCRIBE_APP_CONTEXTS`) > `prompts.app_contexts`
/// (`~/.pulsescribe/prompts.toml` merged with defaults) > `"default"`.
pub fn context_for_app(
    app_name: &str,
    env_overrides: &BTreeMap<String, String>,
    prompts: &PromptsConfig,
) -> &'static str {
    let target = app_name.trim();
    if target.is_empty() {
        return "default";
    }

    for (key, value) in env_overrides {
        if key.eq_ignore_ascii_case(target) {
            return normalize_context_value(Some(value)).unwrap_or("default");
        }
    }

    for (key, value) in &prompts.app_contexts {
        if key.eq_ignore_ascii_case(target) {
            return normalize_context_value(Some(value)).unwrap_or("default");
        }
    }

    "default"
}

/// Resolve the refinement context using explicit inputs (pure, OS-independent).
///
/// Priority: `override_ctx` (CLI) > `env_context` (`PULSESCRIBE_CONTEXT`) > `frontmost_app` > `"default"`.
pub fn detect_context_with(
    override_ctx: Option<&str>,
    env_context: Option<&str>,
    frontmost_app: Option<&str>,
    env_app_contexts: &BTreeMap<String, String>,
    prompts: &PromptsConfig,
) -> DetectedContext {
    if let Some(raw) = override_ctx.filter(|s| !s.trim().is_empty()) {
        if let Some(context) = normalize_context_value(Some(raw)) {
            return DetectedContext {
                context,
                app_name: None,
                source: ContextSource::Cli,
            };
        }
        tracing::warn!(context = raw, "ignoring unknown context from CLI override");
    }

    if let Some(raw) = env_context.filter(|s| !s.trim().is_empty()) {
        if let Some(context) = normalize_context_value(Some(raw)) {
            return DetectedContext {
                context,
                app_name: None,
                source: ContextSource::Env,
            };
        }
        tracing::warn!(context = raw, "ignoring unknown context from ENV");
    }

    if let Some(app) = frontmost_app.map(str::trim).filter(|s| !s.is_empty()) {
        let context = context_for_app(app, env_app_contexts, prompts);
        return DetectedContext {
            context,
            app_name: Some(app.to_string()),
            source: ContextSource::App,
        };
    }

    DetectedContext {
        context: "default",
        app_name: None,
        source: ContextSource::Default,
    }
}

/// Resolve context reading `PULSESCRIBE_CONTEXT` and `PULSESCRIBE_APP_CONTEXTS` from process env.
pub fn detect_context_from_env(
    override_ctx: Option<&str>,
    frontmost_app: Option<&str>,
    prompts: &PromptsConfig,
) -> DetectedContext {
    let env_context = read_env_non_empty(CONTEXT_ENV_VAR);
    let env_app_contexts = read_env_non_empty(APP_CONTEXTS_ENV_VAR)
        .map(|raw| parse_app_contexts_json(&raw))
        .unwrap_or_default();

    detect_context_with(
        override_ctx,
        env_context.as_deref(),
        frontmost_app,
        &env_app_contexts,
        prompts,
    )
}

/// Resolve the final system/instruction prompt for refinement.
///
/// Non-blank explicit prompts override context prompts verbatim; blank or unset prompts use
/// `prompts.refine_prompt_for_context(detected.context, voice_commands)`.
pub fn resolve_refine_prompt(
    explicit_prompt: Option<&str>,
    detected: &DetectedContext,
    prompts: &PromptsConfig,
    voice_commands: bool,
) -> String {
    if let Some(custom) = explicit_prompt.filter(|p| !p.trim().is_empty()) {
        return custom.to_string();
    }
    prompts.refine_prompt_for_context(detected.context, voice_commands)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_app_contexts_json_normalizes_values_and_rejects_invalid_shapes() {
        let parsed =
            parse_app_contexts_json(r#"{" CustomApp ": " CHAT ", "Bad": "unknown", "Num": 42}"#);
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed.get("CustomApp").map(String::as_str), Some("chat"));

        assert!(parse_app_contexts_json("not json").is_empty());
        assert!(parse_app_contexts_json(r#"["Slack", "chat"]"#).is_empty());
    }

    #[test]
    fn context_for_app_applies_env_over_prompts_case_insensitively() {
        let prompts = PromptsConfig::defaults();
        let empty = BTreeMap::new();
        assert_eq!(context_for_app("Slack", &empty, &prompts), "chat");
        assert_eq!(context_for_app("OUTLOOK", &empty, &prompts), "email");
        assert_eq!(context_for_app("VS Code", &empty, &prompts), "code");
        assert_eq!(context_for_app("Safari", &empty, &prompts), "default");

        let env_map = parse_app_contexts_json(r#"{"Slack": "CODE", "Safari": "chat"}"#);
        assert_eq!(context_for_app("slack", &env_map, &prompts), "code");
        assert_eq!(context_for_app("Safari", &env_map, &prompts), "chat");

        // Case-variant user override in prompts.toml must replace the default entry.
        let merged_toml = pulsescribe_core::prompts::parse_and_merge_prompts_toml(
            "[app_contexts]\nslack = \"code\"\n",
        )
        .unwrap();
        assert_eq!(context_for_app("Slack", &empty, &merged_toml), "code");
        assert_eq!(context_for_app("slack", &empty, &merged_toml), "code");

        let mut overrides = pulsescribe_core::prompts::PromptOverrides::default();
        overrides
            .app_contexts
            .insert("slack".to_string(), "email".to_string());
        let merged_overrides = overrides.merge_with_defaults();
        assert_eq!(context_for_app("Slack", &empty, &merged_overrides), "email");
    }

    #[test]
    fn detect_context_precedence_cli_over_env_over_app_over_default() {
        let prompts = PromptsConfig::defaults();
        let empty = BTreeMap::new();

        let cli = detect_context_with(Some("EMAIL"), Some("chat"), Some("Code"), &empty, &prompts);
        assert_eq!(cli.context, "email");
        assert_eq!(cli.source, ContextSource::Cli);
        assert_eq!(cli.app_name, None);

        let env = detect_context_with(None, Some(" CHAT "), Some("Code"), &empty, &prompts);
        assert_eq!(env.context, "chat");
        assert_eq!(env.source, ContextSource::Env);

        let app = detect_context_with(None, Some("invalid"), Some("Slack"), &empty, &prompts);
        assert_eq!(app.context, "chat");
        assert_eq!(app.source, ContextSource::App);
        assert_eq!(app.app_name.as_deref(), Some("Slack"));

        let fallback = detect_context_with(None, Some("invalid"), None, &empty, &prompts);
        assert_eq!(fallback.context, "default");
        assert_eq!(fallback.source, ContextSource::Default);
    }

    #[test]
    fn resolve_refine_prompt_prefers_non_blank_explicit_prompt() {
        let prompts = PromptsConfig::defaults();
        let detected = DetectedContext {
            context: "email",
            app_name: Some("Mail".to_string()),
            source: ContextSource::App,
        };
        assert_eq!(
            resolve_refine_prompt(Some("Custom prompt"), &detected, &prompts, true),
            "Custom prompt"
        );
        let fallback = resolve_refine_prompt(Some("   "), &detected, &prompts, true);
        assert!(fallback.contains("E-Mail"));
        assert!(fallback.contains("neuer Absatz"));
    }
}
