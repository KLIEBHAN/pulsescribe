//! LLM transcript refinement across `Groq`, `OpenAI` (Responses API), `OpenRouter`, and `Gemini`.
//!
//! Uses the shared pooled `reqwest::Client` with per-request `LLM_REFINE_TIMEOUT` (`30s` default)
//! to reuse TLS/HTTP2 connections across dictations without heavyweight provider SDKs.

use std::fmt;
use std::str::FromStr;
use std::time::Duration;

use pulsescribe_core::env::parse_bool;
use pulsescribe_core::prompts::PromptsConfig;
use reqwest::header::{HeaderName, AUTHORIZATION};
use reqwest::Client;
use serde_json::{json, Value};

use crate::groq::GROQ_API_KEY_ENV_VAR;
use crate::http::{
    join_base_url, read_checked_response, read_env_non_empty, redact_url_for_debug,
    validate_header_secret,
};
use crate::openai::OPENAI_API_KEY_ENV_VAR;
use crate::refine::context::{detect_context_from_env, resolve_refine_prompt};
use crate::{ProviderError, GROQ_API_BASE_URL, OPENAI_API_BASE_URL};

pub const DEFAULT_REFINE_PROVIDER: RefineProviderKind = RefineProviderKind::Groq;
pub const DEFAULT_REFINE_MODEL: &str = "openai/gpt-oss-120b";
pub const DEFAULT_OPENAI_REFINE_MODEL: &str = "gpt-4o-mini";
pub const DEFAULT_GEMINI_REFINE_MODEL: &str = "gemini-3-flash-preview";

pub const OPENROUTER_API_BASE_URL: &str = "https://openrouter.ai/api/v1";
pub const GEMINI_API_BASE_URL: &str = "https://generativelanguage.googleapis.com/v1beta";

pub const DEFAULT_REFINE_TIMEOUT: Duration = Duration::from_secs(30);

pub const REFINE_PROVIDER_ENV_VAR: &str = "PULSESCRIBE_REFINE_PROVIDER";
pub const REFINE_MODEL_ENV_VAR: &str = "PULSESCRIBE_REFINE_MODEL";
pub const OPENROUTER_API_KEY_ENV_VAR: &str = "OPENROUTER_API_KEY";
pub const GEMINI_API_KEY_ENV_VAR: &str = "GEMINI_API_KEY";
pub const OPENROUTER_PROVIDER_ORDER_ENV_VAR: &str = "OPENROUTER_PROVIDER_ORDER";
pub const OPENROUTER_ALLOW_FALLBACKS_ENV_VAR: &str = "OPENROUTER_ALLOW_FALLBACKS";

static GEMINI_API_KEY_HEADER: HeaderName = HeaderName::from_static("x-goog-api-key");

/// Supported LLM backends for transcript refinement.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum RefineProviderKind {
    #[default]
    Groq,
    OpenAi,
    OpenRouter,
    Gemini,
}

impl RefineProviderKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Groq => "groq",
            Self::OpenAi => "openai",
            Self::OpenRouter => "openrouter",
            Self::Gemini => "gemini",
        }
    }

    pub const fn api_key_env_var(self) -> &'static str {
        match self {
            Self::Groq => GROQ_API_KEY_ENV_VAR,
            Self::OpenAi => OPENAI_API_KEY_ENV_VAR,
            Self::OpenRouter => OPENROUTER_API_KEY_ENV_VAR,
            Self::Gemini => GEMINI_API_KEY_ENV_VAR,
        }
    }

    pub const fn default_model(self) -> &'static str {
        match self {
            Self::Gemini => DEFAULT_GEMINI_REFINE_MODEL,
            Self::OpenAi => DEFAULT_OPENAI_REFINE_MODEL,
            Self::Groq | Self::OpenRouter => DEFAULT_REFINE_MODEL,
        }
    }

    pub const fn default_base_url(self) -> &'static str {
        match self {
            Self::Groq => GROQ_API_BASE_URL,
            Self::OpenAi => OPENAI_API_BASE_URL,
            Self::OpenRouter => OPENROUTER_API_BASE_URL,
            Self::Gemini => GEMINI_API_BASE_URL,
        }
    }
}

impl fmt::Display for RefineProviderKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

impl FromStr for RefineProviderKind {
    type Err = ProviderError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.trim().to_ascii_lowercase().as_str() {
            "groq" => Ok(Self::Groq),
            "openai" => Ok(Self::OpenAi),
            "openrouter" => Ok(Self::OpenRouter),
            "gemini" => Ok(Self::Gemini),
            _ => Err(ProviderError::InvalidInput(format!(
                "Unbekannter Refine-Provider '{s}'. Unterstützt: gemini, groq, openai, openrouter"
            ))),
        }
    }
}

/// Optional OpenRouter provider-routing hints (`OPENROUTER_PROVIDER_ORDER` / `OPENROUTER_ALLOW_FALLBACKS`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OpenRouterRouting {
    pub order: Vec<String>,
    pub allow_fallbacks: bool,
}

impl OpenRouterRouting {
    /// Parse routing preferences from a comma-separated provider list and fallback flag.
    ///
    /// Returns `None` if no valid non-blank provider entries remain after normalization.
    pub fn parse(raw_order: &str, allow_fallbacks: bool) -> Option<Self> {
        let order: Vec<String> = raw_order
            .split(',')
            .map(str::trim)
            .filter(|entry| !entry.is_empty())
            .map(ToOwned::to_owned)
            .collect();
        if order.is_empty() {
            tracing::warn!(
                "OPENROUTER_PROVIDER_ORDER ignored (no valid providers after normalization)"
            );
            return None;
        }
        Some(Self {
            order,
            allow_fallbacks,
        })
    }

    /// Load optional OpenRouter routing hints from the current environment.
    pub fn from_env() -> Option<Self> {
        let raw_order = read_env_non_empty(OPENROUTER_PROVIDER_ORDER_ENV_VAR)?;
        let allow_fallbacks = read_env_non_empty(OPENROUTER_ALLOW_FALLBACKS_ENV_VAR)
            .and_then(|v| parse_bool(Some(&v)))
            .unwrap_or(true);
        Self::parse(&raw_order, allow_fallbacks)
    }
}

/// Inputs for a transcript refinement request.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RefineRequest {
    pub transcript: String,
    pub model: Option<String>,
    pub prompt: Option<String>,
    pub context: Option<String>,
    pub frontmost_app: Option<String>,
    pub voice_commands: bool,
}

impl RefineRequest {
    pub fn new(transcript: impl Into<String>) -> Self {
        Self {
            transcript: transcript.into(),
            model: None,
            prompt: None,
            context: None,
            frontmost_app: None,
            voice_commands: true,
        }
    }

    pub fn with_model(mut self, model: impl Into<String>) -> Self {
        self.model = Some(model.into());
        self
    }

    pub fn with_prompt(mut self, prompt: impl Into<String>) -> Self {
        self.prompt = Some(prompt.into());
        self
    }

    pub fn with_context(mut self, context: impl Into<String>) -> Self {
        self.context = Some(context.into());
        self
    }

    pub fn with_frontmost_app(mut self, app_name: impl Into<String>) -> Self {
        self.frontmost_app = Some(app_name.into());
        self
    }

    pub fn with_voice_commands(mut self, enabled: bool) -> Self {
        self.voice_commands = enabled;
        self
    }
}

/// Pooled HTTP client for LLM transcript refinement.
#[derive(Clone)]
pub struct LlmRefineProvider {
    client: Client,
    kind: RefineProviderKind,
    base_url: String,
    api_key: String,
    default_model: String,
    timeout: Duration,
    openrouter_routing: Option<OpenRouterRouting>,
}

impl fmt::Debug for LlmRefineProvider {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LlmRefineProvider")
            .field("kind", &self.kind)
            .field("base_url", &redact_url_for_debug(&self.base_url))
            .field("api_key", &"[REDACTED]")
            .field("default_model", &self.default_model)
            .field("timeout", &self.timeout)
            .field("openrouter_routing", &self.openrouter_routing)
            .finish_non_exhaustive()
    }
}

impl LlmRefineProvider {
    /// Create a refine provider targeting the backend's default API endpoint.
    pub fn new(
        client: Client,
        kind: RefineProviderKind,
        api_key: impl Into<String>,
    ) -> Result<Self, ProviderError> {
        Self::with_base_url(client, kind, kind.default_base_url(), api_key)
    }

    /// Create a refine provider targeting a custom HTTP(S) endpoint (useful for tests or proxies).
    pub fn with_base_url(
        client: Client,
        kind: RefineProviderKind,
        base_url: impl Into<String>,
        api_key: impl Into<String>,
    ) -> Result<Self, ProviderError> {
        let base_url = base_url.into().trim().to_string();
        let api_key = api_key.into().trim().to_string();
        if api_key.is_empty() {
            return Err(ProviderError::MissingConfig(format!(
                "{} nicht gesetzt",
                kind.api_key_env_var()
            )));
        }
        validate_header_secret(kind.api_key_env_var(), &api_key)?;
        let _ = join_base_url(&base_url, &["health"])?;

        Ok(Self {
            client,
            kind,
            base_url,
            api_key,
            default_model: kind.default_model().to_string(),
            timeout: DEFAULT_REFINE_TIMEOUT,
            openrouter_routing: None,
        })
    }

    /// Resolve provider, model, API key, and optional OpenRouter routing from overrides and env.
    ///
    /// Precedence matches `refine/llm.py`:
    /// - Provider: `provider_override` > `PULSESCRIBE_REFINE_PROVIDER` > `groq`
    /// - Model: `model_override` > `PULSESCRIBE_REFINE_MODEL` > provider default
    pub fn from_env(
        client: Client,
        provider_override: Option<&str>,
        model_override: Option<&str>,
    ) -> Result<Self, ProviderError> {
        let raw_provider = provider_override
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(ToOwned::to_owned)
            .or_else(|| read_env_non_empty(REFINE_PROVIDER_ENV_VAR))
            .unwrap_or_else(|| DEFAULT_REFINE_PROVIDER.as_str().to_string());
        let kind: RefineProviderKind = raw_provider.parse()?;

        let api_key = read_env_non_empty(kind.api_key_env_var()).ok_or_else(|| {
            ProviderError::MissingConfig(format!("{} nicht gesetzt", kind.api_key_env_var()))
        })?;

        let mut provider = Self::new(client, kind, api_key)?;
        let model = model_override
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(ToOwned::to_owned)
            .or_else(|| read_env_non_empty(REFINE_MODEL_ENV_VAR));
        if let Some(model) = model {
            provider = provider.with_default_model(model)?;
        }
        if kind == RefineProviderKind::OpenRouter {
            provider.openrouter_routing = OpenRouterRouting::from_env();
        }
        Ok(provider)
    }

    pub fn kind(&self) -> RefineProviderKind {
        self.kind
    }

    pub fn default_model(&self) -> &str {
        &self.default_model
    }

    pub fn with_default_model(mut self, model: impl Into<String>) -> Result<Self, ProviderError> {
        let trimmed = model.into().trim().to_string();
        if trimmed.is_empty() {
            return Err(ProviderError::InvalidInput(
                "refine model must not be empty".to_string(),
            ));
        }
        self.default_model = trimmed;
        Ok(self)
    }

    pub fn with_timeout(mut self, timeout: Duration) -> Self {
        self.timeout = timeout;
        self
    }

    pub fn with_openrouter_routing(mut self, routing: Option<OpenRouterRouting>) -> Self {
        self.openrouter_routing = routing;
        self
    }

    /// Refine a transcript using context detection (unless an explicit prompt is provided).
    ///
    /// Empty or whitespace-only transcripts return unchanged without making an HTTP call.
    pub async fn refine(
        &self,
        request: &RefineRequest,
        prompts: &PromptsConfig,
    ) -> Result<String, ProviderError> {
        if request.transcript.trim().is_empty() {
            return Ok(request.transcript.clone());
        }

        let instruction =
            if let Some(custom) = request.prompt.as_deref().filter(|p| !p.trim().is_empty()) {
                custom.to_string()
            } else {
                let detected = detect_context_from_env(
                    request.context.as_deref(),
                    request.frontmost_app.as_deref(),
                    prompts,
                );
                tracing::info!(
                    context = detected.context,
                    source = detected.source.as_str(),
                    app = detected.app_name.as_deref().unwrap_or(""),
                    "resolved refine context"
                );
                resolve_refine_prompt(None, &detected, prompts, request.voice_commands)
            };

        self.refine_with_prompt(&request.transcript, &instruction, request.model.as_deref())
            .await
    }

    /// Refine a transcript with an already-resolved instruction prompt (no environment reads).
    pub async fn refine_with_prompt(
        &self,
        transcript: &str,
        prompt: &str,
        model_override: Option<&str>,
    ) -> Result<String, ProviderError> {
        if transcript.trim().is_empty() {
            return Ok(transcript.to_string());
        }
        let model = model_override
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .unwrap_or(&self.default_model);
        let full_prompt = format!("{prompt}\n\nTranskript:\n{transcript}");

        tracing::info!(
            provider = self.kind.as_str(),
            model,
            input_chars = transcript.chars().count(),
            "starting LLM transcript refinement"
        );

        let refined = match self.kind {
            RefineProviderKind::Groq => {
                self.execute_chat_completions(model, &full_prompt, None)
                    .await?
            }
            RefineProviderKind::OpenRouter => {
                self.execute_chat_completions(model, &full_prompt, self.openrouter_routing.as_ref())
                    .await?
            }
            RefineProviderKind::OpenAi => {
                self.execute_openai_responses(model, &full_prompt).await?
            }
            RefineProviderKind::Gemini => self.execute_gemini(model, &full_prompt).await?,
        };

        tracing::debug!(
            provider = self.kind.as_str(),
            output_chars = refined.chars().count(),
            "LLM transcript refinement completed"
        );
        Ok(refined)
    }

    async fn execute_chat_completions(
        &self,
        model: &str,
        full_prompt: &str,
        routing: Option<&OpenRouterRouting>,
    ) -> Result<String, ProviderError> {
        let url = join_base_url(&self.base_url, &["chat", "completions"])?;
        let mut payload = json!({
            "model": model,
            "messages": [{ "role": "user", "content": full_prompt }],
        });
        if let Some(routing) = routing.filter(|r| !r.order.is_empty()) {
            payload["provider"] = json!({
                "order": routing.order,
                "allow_fallbacks": routing.allow_fallbacks,
            });
        }

        let response = self
            .client
            .post(url)
            .timeout(self.timeout)
            .header(AUTHORIZATION, format!("Bearer {}", self.api_key))
            .json(&payload)
            .send()
            .await?;

        let checked = read_checked_response(self.kind.as_str(), response).await?;
        extract_choice_message_text(&checked.body, self.kind.as_str())
    }

    async fn execute_openai_responses(
        &self,
        model: &str,
        full_prompt: &str,
    ) -> Result<String, ProviderError> {
        let url = join_base_url(&self.base_url, &["responses"])?;
        let mut payload = json!({
            "model": model,
            "input": full_prompt,
        });
        if let Some(effort) = openai_reasoning_effort(model) {
            payload["reasoning"] = json!({ "effort": effort });
        }

        let response = self
            .client
            .post(url)
            .timeout(self.timeout)
            .header(AUTHORIZATION, format!("Bearer {}", self.api_key))
            .json(&payload)
            .send()
            .await?;

        let checked = read_checked_response(self.kind.as_str(), response).await?;
        extract_openai_responses_text(&checked.body)
    }

    async fn execute_gemini(
        &self,
        model: &str,
        full_prompt: &str,
    ) -> Result<String, ProviderError> {
        let model_id = sanitize_gemini_model_id(model)?;
        let segment = format!("{model_id}:generateContent");
        let url = join_base_url(&self.base_url, &["models", &segment])?;

        let mut payload = json!({
            "contents": [{
                "role": "user",
                "parts": [{ "text": full_prompt }],
            }],
        });
        if let Some(thinking_config) = gemini_thinking_config(model_id) {
            payload["generationConfig"] = json!({
                "thinkingConfig": thinking_config,
            });
        }

        let response = self
            .client
            .post(url)
            .timeout(self.timeout)
            .header(GEMINI_API_KEY_HEADER.clone(), &self.api_key)
            .json(&payload)
            .send()
            .await?;

        let checked = read_checked_response(self.kind.as_str(), response).await?;
        extract_gemini_text(&checked.body)
    }
}

/// Only the original GPT-5 family (`gpt-5`, `gpt-5-mini`, `gpt-5-nano`, `gpt-5-2025-*`) accepts
/// `reasoning.effort = "minimal"`; newer `gpt-5.1`/`gpt-5.2` models reject `"minimal"`.
fn openai_reasoning_effort(model: &str) -> Option<&'static str> {
    let lower = model.trim().to_ascii_lowercase();
    if lower == "gpt-5" || lower.starts_with("gpt-5-") {
        Some("minimal")
    } else {
        None
    }
}

/// Choose model-compatible Gemini `thinkingConfig`:
/// - Gemini 3 (`gemini-3*`): `thinkingLevel` (`MINIMAL` for Flash, `LOW` otherwise)
/// - Gemini 2.5 (`gemini-2.5*`): `thinkingBudget` (`0` for Flash, `128` for Pro)
/// - Earlier models: omit `thinkingConfig`
fn gemini_thinking_config(model_id: &str) -> Option<Value> {
    let lower = model_id.to_ascii_lowercase();
    if lower.starts_with("gemini-3") {
        let level = if lower.contains("flash") {
            "MINIMAL"
        } else {
            "LOW"
        };
        return Some(json!({ "thinkingLevel": level }));
    }
    if lower.starts_with("gemini-2.5") {
        let budget = if lower.contains("flash") { 0 } else { 128 };
        return Some(json!({ "thinkingBudget": budget }));
    }
    None
}

/// Apply LLM refinement when enabled, gracefully returning the original transcript on any error
/// or empty LLM response (matching `refine.llm.maybe_refine_transcript`).
pub async fn maybe_refine(
    provider: Option<&LlmRefineProvider>,
    request: &RefineRequest,
    prompts: &PromptsConfig,
    enabled: bool,
) -> String {
    if !enabled || request.transcript.trim().is_empty() {
        return request.transcript.clone();
    }
    let Some(provider) = provider else {
        tracing::warn!("LLM refinement skipped: no refine provider configured");
        return request.transcript.clone();
    };

    match provider.refine(request, prompts).await {
        Ok(refined) if !refined.trim().is_empty() => refined,
        Ok(_) => {
            tracing::warn!("LLM refinement returned empty text; keeping original transcript");
            request.transcript.clone()
        }
        Err(err) => {
            tracing::warn!(error = %err, "LLM refinement failed; keeping original transcript");
            request.transcript.clone()
        }
    }
}

fn sanitize_gemini_model_id(model: &str) -> Result<&str, ProviderError> {
    let trimmed = model.trim();
    let id = trimmed.strip_prefix("models/").unwrap_or(trimmed);
    if id.is_empty()
        || id.contains("..")
        || id
            .bytes()
            .any(|b| b <= 0x20 || b == 0x7f || matches!(b, b'/' | b'\\' | b'?' | b'#' | b'%'))
    {
        return Err(ProviderError::InvalidInput(format!(
            "invalid Gemini model identifier '{model}'"
        )));
    }
    Ok(id)
}

fn extract_choice_message_text(
    body: &str,
    provider: &'static str,
) -> Result<String, ProviderError> {
    let parsed: Value =
        serde_json::from_str(body).map_err(|err| ProviderError::InvalidResponse {
            provider,
            detail: format!("invalid JSON response: {err}"),
        })?;
    let first_choice = parsed
        .get("choices")
        .and_then(Value::as_array)
        .and_then(|choices| choices.first())
        .ok_or_else(|| ProviderError::InvalidResponse {
            provider,
            detail: format!("{provider}-Antwort enthält keine choices"),
        })?;
    let content = first_choice
        .get("message")
        .and_then(|m| m.get("content"))
        .unwrap_or(&Value::Null);
    extract_message_content(content)
        .map_err(|detail| ProviderError::InvalidResponse { provider, detail })
}

/// Extract text from OpenAI/Groq/OpenRouter message content (`string`, part array, or object).
///
/// Preserves inner whitespace across segmented content parts (`["Hello", " World"]` -> `"Hello World"`)
/// and trims only the outer boundaries, matching `refine/llm.py::_extract_message_content`.
pub fn extract_message_content(content: &Value) -> Result<String, String> {
    extract_message_content_inner(content, true)
}

fn extract_message_content_inner(content: &Value, strip_outer: bool) -> Result<String, String> {
    let finish = |text: String| {
        if strip_outer {
            text.trim().to_string()
        } else {
            text
        }
    };
    match content {
        Value::Null => Ok(String::new()),
        Value::String(text) => Ok(finish(text.clone())),
        Value::Array(items) => {
            let mut out = String::new();
            for item in items {
                out.push_str(&extract_message_content_inner(item, false)?);
            }
            Ok(finish(out))
        }
        Value::Object(map) => {
            if let Some(Value::String(text)) = map.get("text") {
                return Ok(finish(text.clone()));
            }
            if let Some(parts @ Value::Array(_)) = map.get("parts") {
                return extract_message_content_inner(parts, strip_outer);
            }
            if let Some(nested) = map.get("content") {
                return extract_message_content_inner(nested, strip_outer);
            }
            Ok(String::new())
        }
        other => Err(format!("unexpected message content JSON type: {other}")),
    }
}

/// Extract assistant text from an OpenAI `/v1/responses` JSON payload.
///
/// Supports both top-level `"output_text"` and the wire-format `"output"` message array.
pub fn extract_openai_responses_text(body: &str) -> Result<String, ProviderError> {
    let parsed: Value =
        serde_json::from_str(body).map_err(|err| ProviderError::InvalidResponse {
            provider: RefineProviderKind::OpenAi.as_str(),
            detail: format!("invalid JSON response: {err}"),
        })?;

    let top_level_output_text = parsed.get("output_text").and_then(Value::as_str);
    if let Some(text) = top_level_output_text.filter(|s| !s.trim().is_empty()) {
        return Ok(text.trim().to_string());
    }

    let Some(output_items) = parsed.get("output").and_then(Value::as_array) else {
        if let Some(text) = top_level_output_text {
            return Ok(text.trim().to_string());
        }
        return Err(ProviderError::InvalidResponse {
            provider: RefineProviderKind::OpenAi.as_str(),
            detail: "JSON response missing 'output_text' and 'output' array".to_string(),
        });
    };

    let mut collected = String::new();
    for item in output_items {
        let Some(parts) = item.get("content").and_then(Value::as_array) else {
            continue;
        };
        for part in parts {
            let part_type = part.get("type").and_then(Value::as_str);
            if matches!(part_type, None | Some("output_text") | Some("text")) {
                if let Some(text) = part.get("text").and_then(Value::as_str) {
                    collected.push_str(text);
                }
            }
        }
    }

    Ok(collected.trim().to_string())
}

/// Extract answer text from a Gemini `generateContent` JSON payload, ignoring `thought: true` parts.
pub fn extract_gemini_text(body: &str) -> Result<String, ProviderError> {
    let parsed: Value =
        serde_json::from_str(body).map_err(|err| ProviderError::InvalidResponse {
            provider: RefineProviderKind::Gemini.as_str(),
            detail: format!("invalid JSON response: {err}"),
        })?;

    let candidate = parsed
        .get("candidates")
        .and_then(Value::as_array)
        .and_then(|c| c.first())
        .ok_or_else(|| ProviderError::InvalidResponse {
            provider: RefineProviderKind::Gemini.as_str(),
            detail: "Gemini response contains no candidates".to_string(),
        })?;

    let Some(parts) = candidate
        .get("content")
        .and_then(|c| c.get("parts"))
        .and_then(Value::as_array)
    else {
        return Ok(String::new());
    };

    let mut collected = String::new();
    for part in parts {
        if part.get("thought").and_then(Value::as_bool) == Some(true) {
            continue;
        }
        if let Some(text) = part.get("text").and_then(Value::as_str) {
            collected.push_str(text);
        }
    }

    Ok(collected.trim().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::build_http_client;

    #[test]
    fn provider_kind_parsing_and_defaults_match_python_contract() {
        assert_eq!(
            "GROQ".parse::<RefineProviderKind>().unwrap(),
            RefineProviderKind::Groq
        );
        assert_eq!(
            " openai ".parse::<RefineProviderKind>().unwrap(),
            RefineProviderKind::OpenAi
        );
        assert_eq!(
            "OpenRouter".parse::<RefineProviderKind>().unwrap(),
            RefineProviderKind::OpenRouter
        );
        assert_eq!(
            "gemini".parse::<RefineProviderKind>().unwrap(),
            RefineProviderKind::Gemini
        );
        assert_eq!(
            RefineProviderKind::Groq.default_model(),
            DEFAULT_REFINE_MODEL
        );
        assert_eq!(
            RefineProviderKind::OpenRouter.default_model(),
            DEFAULT_REFINE_MODEL
        );
        assert_eq!(
            RefineProviderKind::OpenAi.default_model(),
            DEFAULT_OPENAI_REFINE_MODEL
        );
        assert_eq!(
            RefineProviderKind::Gemini.default_model(),
            DEFAULT_GEMINI_REFINE_MODEL
        );

        assert_eq!(openai_reasoning_effort("gpt-5"), Some("minimal"));
        assert_eq!(openai_reasoning_effort("gpt-5-mini"), Some("minimal"));
        assert_eq!(openai_reasoning_effort("gpt-5-nano"), Some("minimal"));
        assert_eq!(openai_reasoning_effort("gpt-5.1"), None);
        assert_eq!(openai_reasoning_effort("gpt-4o-mini"), None);

        assert_eq!(
            gemini_thinking_config("gemini-3-flash-preview"),
            Some(json!({ "thinkingLevel": "MINIMAL" }))
        );
        assert_eq!(
            gemini_thinking_config("gemini-3-pro-preview"),
            Some(json!({ "thinkingLevel": "LOW" }))
        );
        assert_eq!(
            gemini_thinking_config("gemini-2.5-flash"),
            Some(json!({ "thinkingBudget": 0 }))
        );
        assert_eq!(
            gemini_thinking_config("gemini-2.5-pro"),
            Some(json!({ "thinkingBudget": 128 }))
        );
        assert_eq!(gemini_thinking_config("gemini-2.0-flash"), None);

        let err = "groqq".parse::<RefineProviderKind>().unwrap_err();
        assert!(err
            .to_string()
            .contains("Unbekannter Refine-Provider 'groqq'"));
    }

    #[test]
    fn openrouter_routing_trims_and_ignores_blank_entries() {
        let parsed = OpenRouterRouting::parse(" openai, , groq,  ", false).unwrap();
        assert_eq!(parsed.order, vec!["openai", "groq"]);
        assert!(!parsed.allow_fallbacks);
        assert!(OpenRouterRouting::parse(" ,  , ", true).is_none());
    }

    #[test]
    fn extract_message_content_preserves_inner_whitespace_across_parts() {
        let parts = json!([
            { "type": "text", "text": " Hello" },
            { "type": "text", "text": " World " }
        ]);
        assert_eq!(extract_message_content(&parts).unwrap(), "Hello World");

        let obj = json!({ "text": " refined " });
        assert_eq!(extract_message_content(&obj).unwrap(), "refined");

        assert_eq!(extract_message_content(&Value::Null).unwrap(), "");
    }

    #[test]
    fn extract_openai_responses_supports_output_array_and_output_text() {
        let wire = json!({
            "output": [
                { "type": "reasoning", "summary": [] },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        { "type": "output_text", "text": " Hallo" },
                        { "type": "output_text", "text": " Welt. " }
                    ]
                }
            ]
        })
        .to_string();
        assert_eq!(extract_openai_responses_text(&wire).unwrap(), "Hallo Welt.");

        let shorthand = json!({ "output_text": " Direkt " }).to_string();
        assert_eq!(extract_openai_responses_text(&shorthand).unwrap(), "Direkt");
    }

    #[test]
    fn extract_gemini_text_skips_thought_parts_and_preserves_answer() {
        let body = json!({
            "candidates": [{
                "content": {
                    "parts": [
                        { "text": "internal chain of thought", "thought": true },
                        { "text": " Sauberer" },
                        { "text": " Text. " }
                    ]
                }
            }]
        })
        .to_string();
        assert_eq!(extract_gemini_text(&body).unwrap(), "Sauberer Text.");
    }

    #[test]
    fn gemini_model_id_rejects_path_injection_and_accepts_models_prefix() {
        assert_eq!(
            sanitize_gemini_model_id("models/gemini-3-flash-preview").unwrap(),
            "gemini-3-flash-preview"
        );
        assert!(sanitize_gemini_model_id("../evil").is_err());
        assert!(sanitize_gemini_model_id("gemini?key=leak").is_err());
        assert!(sanitize_gemini_model_id("   ").is_err());
    }

    #[test]
    fn refine_provider_debug_redacts_api_key_and_url_credentials() {
        let client = build_http_client().unwrap();
        let provider = LlmRefineProvider::with_base_url(
            client,
            RefineProviderKind::OpenRouter,
            "https://user:pw@proxy.example/v1?secret=xyz",
            "or-secret-token",
        )
        .unwrap();
        let debug = format!("{provider:?}");
        assert!(!debug.contains("or-secret-token"));
        assert!(!debug.contains("pw"));
        assert!(!debug.contains("secret=xyz"));
        assert!(debug.contains("[REDACTED]"));
    }
}
