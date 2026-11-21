//! OpenAI-compatible self-hosted / LAN transcription provider and `/models` discovery.
//!
//! Mirrors `providers/openai_compatible.py`:
//! - Uses `OPENAI_COMPATIBLE_BASE_URL`, optional `OPENAI_COMPATIBLE_API_KEY` (never falls back to
//!   `OPENAI_API_KEY`), and optional `OPENAI_COMPATIBLE_MODEL` (default: `whisper-1`).
//! - Logs a warning if a non-placeholder API key is sent over plain HTTP to a non-loopback host.
//! - Exposes `list_server_models()` with a short per-request timeout for settings UI model pickers.

use std::collections::HashSet;
use std::time::Duration;

use reqwest::header::AUTHORIZATION;
use reqwest::Client;
use serde::Deserialize;
use url::Url;

use crate::http::{
    is_loopback_url, join_base_url, parse_json_text_field, post_multipart_transcription,
    read_checked_response, read_env_non_empty, validate_header_secret,
    MultipartTranscriptionParams,
};
use crate::{
    normalize_auto_language, ProviderError, TranscriptionRequest, DEFAULT_OPENAI_COMPATIBLE_MODEL,
};

pub const BASE_URL_ENV_VAR: &str = "OPENAI_COMPATIBLE_BASE_URL";
pub const API_KEY_ENV_VAR: &str = "OPENAI_COMPATIBLE_API_KEY";
pub const MODEL_ENV_VAR: &str = "OPENAI_COMPATIBLE_MODEL";
pub const PLACEHOLDER_API_KEY: &str = "not-needed";
pub const DEFAULT_LIST_MODELS_TIMEOUT: Duration = Duration::from_secs(2);

/// Returns `true` when a real (non-placeholder) key would travel over plain HTTP to a non-loopback host.
pub fn is_unencrypted_remote_key(base_url: &str, api_key: Option<&str>) -> bool {
    let Some(key) = api_key.map(str::trim).filter(|k| !k.is_empty()) else {
        return false;
    };
    if key == PLACEHOLDER_API_KEY {
        return false;
    }
    let Ok(url) = Url::parse(base_url.trim()) else {
        return false;
    };
    url.scheme() == "http" && !is_loopback_url(&url)
}

fn warn_if_unencrypted_remote_key(base_url: &str, api_key: Option<&str>) {
    if is_unencrypted_remote_key(base_url, api_key) {
        let host = Url::parse(base_url.trim())
            .ok()
            .and_then(|u| u.host_str().map(ToOwned::to_owned))
            .unwrap_or_else(|| "?".to_string());
        tracing::warn!(
            env_var = API_KEY_ENV_VAR,
            host = %host,
            "API key is sent unencrypted over plain HTTP; use HTTPS outside a trusted LAN"
        );
    }
}

fn effective_api_key(api_key: Option<&str>) -> &str {
    api_key
        .map(str::trim)
        .filter(|k| !k.is_empty())
        .unwrap_or(PLACEHOLDER_API_KEY)
}

#[derive(Debug, Deserialize)]
struct ModelsListResponse {
    data: Vec<ModelEntry>,
}

#[derive(Debug, Deserialize)]
struct ModelEntry {
    id: Option<String>,
}

/// Fetch model IDs from `{base_url}/models` (OpenAI format), deduplicated in server order.
///
/// Applies `timeout` strictly to this request so slow or unreachable servers never stall UI callers.
pub async fn list_server_models(
    client: &Client,
    base_url: &str,
    api_key: Option<&str>,
    timeout: Duration,
) -> Result<Vec<String>, ProviderError> {
    let url = join_base_url(base_url, &["models"])?;
    let auth_key = effective_api_key(api_key);
    validate_header_secret(API_KEY_ENV_VAR, auth_key)?;
    warn_if_unencrypted_remote_key(base_url, api_key);

    let response = client
        .get(url)
        .header(AUTHORIZATION, format!("Bearer {auth_key}"))
        .timeout(timeout)
        .send()
        .await?;

    let checked = read_checked_response(OpenAiCompatibleProvider::NAME, response).await?;
    let parsed: ModelsListResponse =
        serde_json::from_str(&checked.body).map_err(|err| ProviderError::InvalidResponse {
            provider: OpenAiCompatibleProvider::NAME,
            detail: format!("invalid /models JSON response: {err}"),
        })?;

    let mut seen = HashSet::new();
    let mut ids = Vec::with_capacity(parsed.data.len());
    for entry in parsed.data {
        if let Some(id) = entry.id.filter(|s| !s.trim().is_empty()) {
            if seen.insert(id.clone()) {
                ids.push(id);
            }
        }
    }
    Ok(ids)
}

/// Self-hosted / custom OpenAI-compatible `/audio/transcriptions` provider.
#[derive(Debug, Clone)]
pub struct OpenAiCompatibleProvider {
    client: Client,
    base_url: String,
    api_key: Option<String>,
    default_model: String,
}

impl OpenAiCompatibleProvider {
    pub const NAME: &'static str = "openai_compatible";

    /// Create a provider for a custom OpenAI-compatible transcription server.
    pub fn new(
        client: Client,
        base_url: impl Into<String>,
        api_key: Option<String>,
        default_model: Option<String>,
    ) -> Result<Self, ProviderError> {
        let base_url = base_url.into().trim().to_string();
        if base_url.is_empty() {
            return Err(ProviderError::MissingConfig(format!(
                "{BASE_URL_ENV_VAR} nicht gesetzt. \
                 Bitte die Server-Adresse eintragen, z. B. {BASE_URL_ENV_VAR}=http://mac-mini:7860/v1"
            )));
        }
        let _ = join_base_url(&base_url, &["audio", "transcriptions"])?;

        let api_key = api_key
            .map(|k| k.trim().to_string())
            .filter(|k| !k.is_empty());
        if let Some(key) = api_key.as_deref() {
            validate_header_secret(API_KEY_ENV_VAR, key)?;
        }
        let default_model = default_model
            .map(|m| m.trim().to_string())
            .filter(|m| !m.is_empty())
            .unwrap_or_else(|| DEFAULT_OPENAI_COMPATIBLE_MODEL.to_string());

        warn_if_unencrypted_remote_key(&base_url, api_key.as_deref());

        Ok(Self {
            client,
            base_url,
            api_key,
            default_model,
        })
    }

    /// Create a provider reading `OPENAI_COMPATIBLE_*` variables (never `OPENAI_API_KEY`).
    pub fn from_env(client: Client) -> Result<Self, ProviderError> {
        Self::from_env_lookup(client, read_env_non_empty)
    }

    fn from_env_lookup(
        client: Client,
        lookup: impl Fn(&str) -> Option<String>,
    ) -> Result<Self, ProviderError> {
        let base_url = lookup(BASE_URL_ENV_VAR).ok_or_else(|| {
            ProviderError::MissingConfig(format!(
                "{BASE_URL_ENV_VAR} nicht gesetzt. \
                 Bitte die Server-Adresse eintragen, z. B. {BASE_URL_ENV_VAR}=http://mac-mini:7860/v1"
            ))
        })?;
        let api_key = lookup(API_KEY_ENV_VAR);
        let default_model = lookup(MODEL_ENV_VAR);
        Self::new(client, base_url, api_key, default_model)
    }

    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    pub fn default_model(&self) -> &str {
        &self.default_model
    }

    /// Upload audio to `{base_url}/audio/transcriptions` with `response_format=json`.
    pub async fn transcribe(
        &self,
        request: &TranscriptionRequest,
    ) -> Result<String, ProviderError> {
        let model = request
            .model
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .unwrap_or(&self.default_model);
        let language = normalize_auto_language(request.language.as_deref());
        let url = join_base_url(&self.base_url, &["audio", "transcriptions"])?;

        tracing::info!(
            provider = Self::NAME,
            model,
            host = url.host_str().unwrap_or("?"),
            language = language.as_deref().unwrap_or("auto"),
            "starting cloud transcription"
        );

        let response = post_multipart_transcription(
            &self.client,
            MultipartTranscriptionParams {
                provider: Self::NAME,
                url,
                api_key: effective_api_key(self.api_key.as_deref()),
                audio_path: &request.audio_path,
                model,
                language: language.as_deref(),
                response_format: "json",
                temperature: None,
            },
        )
        .await?;

        // We always request `response_format=json`; tolerate servers that omit the JSON
        // Content-Type header while still returning a JSON object, or fall back to plain text.
        let result = if response.is_json {
            parse_json_text_field(Self::NAME, &response.body)?
        } else if response.body.trim_start().starts_with('{') {
            parse_json_text_field(Self::NAME, &response.body).unwrap_or(response.body)
        } else {
            response.body
        };
        tracing::debug!(
            provider = Self::NAME,
            chars = result.chars().count(),
            "cloud transcription completed"
        );
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn from_env_lookup_never_falls_back_to_openai_api_key() {
        let client = crate::build_http_client().unwrap();
        let provider = OpenAiCompatibleProvider::from_env_lookup(client, |var| match var {
            BASE_URL_ENV_VAR => Some("http://mac-mini:7860/v1".to_string()),
            "OPENAI_API_KEY" => Some("sk-must-not-be-used".to_string()),
            _ => None,
        })
        .unwrap();
        assert_eq!(provider.api_key, None);
        assert_eq!(provider.default_model(), DEFAULT_OPENAI_COMPATIBLE_MODEL);
    }

    #[test]
    fn unencrypted_remote_key_warns_only_for_real_key_over_non_loopback_http() {
        assert!(is_unencrypted_remote_key(
            "http://mac-mini:7860/v1",
            Some("secret-token")
        ));
        assert!(!is_unencrypted_remote_key(
            "https://mac-mini:7860/v1",
            Some("secret-token")
        ));
        assert!(!is_unencrypted_remote_key(
            "http://localhost:7860/v1",
            Some("secret-token")
        ));
        assert!(!is_unencrypted_remote_key(
            "http://127.0.0.1:7860/v1",
            Some("secret-token")
        ));
        assert!(!is_unencrypted_remote_key(
            "http://[::1]:7860/v1",
            Some("secret-token")
        ));
        assert!(!is_unencrypted_remote_key(
            "http://mac-mini:7860/v1",
            Some(PLACEHOLDER_API_KEY)
        ));
        assert!(!is_unencrypted_remote_key("http://mac-mini:7860/v1", None));
    }
}
