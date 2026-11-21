//! OpenAI Audio Transcriptions REST provider (`gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, `whisper-1`).
//!
//! Mirrors `providers/openai.py`, including JSON-only response-format mapping for `gpt-4o-*` models.

use reqwest::Client;

use crate::http::{
    join_base_url, parse_json_text_field, post_multipart_transcription, read_env_non_empty,
    validate_header_secret, MultipartTranscriptionParams,
};
use crate::{
    normalize_auto_language, ProviderError, ResponseFormat, TranscriptionRequest,
    DEFAULT_OPENAI_MODEL, OPENAI_API_BASE_URL,
};

pub const OPENAI_API_KEY_ENV_VAR: &str = "OPENAI_API_KEY";

const JSON_ONLY_MODEL_PREFIXES: &[&str] = &["gpt-4o-transcribe", "gpt-4o-mini-transcribe"];

/// Returns `true` for OpenAI transcription models that only accept `response_format=json`.
pub fn uses_json_only_response_format(model: &str) -> bool {
    let normalized = model.trim().to_ascii_lowercase();
    JSON_ONLY_MODEL_PREFIXES
        .iter()
        .any(|prefix| normalized.starts_with(prefix))
}

/// Map the requested CLI format to the OpenAI API `response_format` for `model`.
pub fn resolve_api_response_format(
    model: &str,
    requested: ResponseFormat,
) -> Result<&'static str, ProviderError> {
    if !uses_json_only_response_format(model) {
        return Ok(requested.as_str());
    }

    match requested {
        ResponseFormat::Text | ResponseFormat::Json => Ok("json"),
        ResponseFormat::Srt | ResponseFormat::Vtt => Err(ProviderError::InvalidInput(format!(
            "OpenAI-Modell '{model}' unterstützt kein Ausgabeformat '{}'. \
             Für SRT/VTT bitte '--model whisper-1' verwenden.",
            requested.as_str()
        ))),
    }
}

/// OpenAI `/v1/audio/transcriptions` provider.
#[derive(Debug, Clone)]
pub struct OpenAiProvider {
    client: Client,
    base_url: String,
    api_key: String,
}

impl OpenAiProvider {
    pub const NAME: &'static str = "openai";

    /// Create an OpenAI provider targeting `https://api.openai.com/v1`.
    pub fn new(client: Client, api_key: impl Into<String>) -> Result<Self, ProviderError> {
        Self::with_base_url(client, OPENAI_API_BASE_URL, api_key)
    }

    /// Create an OpenAI provider with a custom base URL (useful for tests or gateways).
    pub fn with_base_url(
        client: Client,
        base_url: impl Into<String>,
        api_key: impl Into<String>,
    ) -> Result<Self, ProviderError> {
        let base_url = base_url.into().trim().to_string();
        let api_key = api_key.into().trim().to_string();
        if api_key.is_empty() {
            return Err(ProviderError::MissingConfig(
                "OPENAI_API_KEY nicht gesetzt. Bitte `export OPENAI_API_KEY='sk-...'` ausführen."
                    .to_string(),
            ));
        }
        validate_header_secret(OPENAI_API_KEY_ENV_VAR, &api_key)?;
        // Validate URL shape up front so callers get immediate feedback.
        let _ = join_base_url(&base_url, &["audio", "transcriptions"])?;
        Ok(Self {
            client,
            base_url,
            api_key,
        })
    }

    /// Create an OpenAI provider reading `OPENAI_API_KEY` from the environment.
    pub fn from_env(client: Client) -> Result<Self, ProviderError> {
        let api_key = read_env_non_empty(OPENAI_API_KEY_ENV_VAR).ok_or_else(|| {
            ProviderError::MissingConfig(
                "OPENAI_API_KEY nicht gesetzt. Bitte `export OPENAI_API_KEY='sk-...'` ausführen."
                    .to_string(),
            )
        })?;
        Self::new(client, api_key)
    }

    pub fn default_model(&self) -> &'static str {
        DEFAULT_OPENAI_MODEL
    }

    /// Transcribe an audio file via OpenAI's `/audio/transcriptions` endpoint.
    pub async fn transcribe(
        &self,
        request: &TranscriptionRequest,
    ) -> Result<String, ProviderError> {
        let model = request
            .model
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .unwrap_or(DEFAULT_OPENAI_MODEL);
        let language = normalize_auto_language(request.language.as_deref());
        let api_format = resolve_api_response_format(model, request.response_format)?;
        let url = join_base_url(&self.base_url, &["audio", "transcriptions"])?;

        tracing::info!(
            provider = Self::NAME,
            model,
            language = language.as_deref().unwrap_or("auto"),
            format = request.response_format.as_str(),
            "starting cloud transcription"
        );

        let response = post_multipart_transcription(
            &self.client,
            MultipartTranscriptionParams {
                provider: Self::NAME,
                url,
                api_key: &self.api_key,
                audio_path: &request.audio_path,
                model,
                language: language.as_deref(),
                response_format: api_format,
                temperature: None,
            },
        )
        .await?;

        let result = match request.response_format {
            ResponseFormat::Text => {
                if api_format == "json" || response.is_json {
                    parse_json_text_field(Self::NAME, &response.body)?
                } else {
                    response.body
                }
            }
            ResponseFormat::Json => {
                let value: serde_json::Value =
                    serde_json::from_str(&response.body).map_err(|err| {
                        ProviderError::InvalidResponse {
                            provider: Self::NAME,
                            detail: format!("invalid JSON response: {err}"),
                        }
                    })?;
                serde_json::to_string_pretty(&value).map_err(|err| {
                    ProviderError::InvalidResponse {
                        provider: Self::NAME,
                        detail: format!("failed formatting JSON response: {err}"),
                    }
                })?
            }
            ResponseFormat::Srt | ResponseFormat::Vtt => response.body,
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
    fn gpt4o_models_map_text_and_json_to_json_and_reject_subtitles() {
        assert!(uses_json_only_response_format("gpt-4o-transcribe"));
        assert!(uses_json_only_response_format(
            "GPT-4O-MINI-TRANSCRIBE-2025"
        ));
        assert!(!uses_json_only_response_format("whisper-1"));

        assert_eq!(
            resolve_api_response_format("gpt-4o-transcribe", ResponseFormat::Text).unwrap(),
            "json"
        );
        assert_eq!(
            resolve_api_response_format("gpt-4o-transcribe", ResponseFormat::Json).unwrap(),
            "json"
        );
        assert!(matches!(
            resolve_api_response_format("gpt-4o-transcribe", ResponseFormat::Srt),
            Err(ProviderError::InvalidInput(_))
        ));
        assert!(matches!(
            resolve_api_response_format("gpt-4o-mini-transcribe", ResponseFormat::Vtt),
            Err(ProviderError::InvalidInput(_))
        ));

        assert_eq!(
            resolve_api_response_format("whisper-1", ResponseFormat::Srt).unwrap(),
            "srt"
        );
        assert_eq!(
            resolve_api_response_format("whisper-1", ResponseFormat::Vtt).unwrap(),
            "vtt"
        );
    }
}
