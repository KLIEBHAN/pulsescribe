//! Groq Whisper REST provider (`whisper-large-v3`, `distil-whisper-large-v3-en`).
//!
//! Mirrors `providers/groq.py`: OpenAI-compatible multipart upload with `response_format=text`
//! and `temperature=0.0` for deterministic output.

use reqwest::Client;

use crate::http::{
    extract_json_or_text_transcript, join_base_url, post_multipart_transcription,
    read_env_non_empty, validate_header_secret, MultipartTranscriptionParams,
};
use crate::{
    normalize_auto_language, ProviderError, TranscriptionRequest, DEFAULT_GROQ_MODEL,
    GROQ_API_BASE_URL,
};

pub const GROQ_API_KEY_ENV_VAR: &str = "GROQ_API_KEY";

/// Groq `/openai/v1/audio/transcriptions` provider.
#[derive(Debug, Clone)]
pub struct GroqProvider {
    client: Client,
    base_url: String,
    api_key: String,
}

impl GroqProvider {
    pub const NAME: &'static str = "groq";

    /// Create a Groq provider targeting `https://api.groq.com/openai/v1`.
    pub fn new(client: Client, api_key: impl Into<String>) -> Result<Self, ProviderError> {
        Self::with_base_url(client, GROQ_API_BASE_URL, api_key)
    }

    /// Create a Groq provider with a custom base URL (useful for tests).
    pub fn with_base_url(
        client: Client,
        base_url: impl Into<String>,
        api_key: impl Into<String>,
    ) -> Result<Self, ProviderError> {
        let base_url = base_url.into().trim().to_string();
        let api_key = api_key.into().trim().to_string();
        if api_key.is_empty() {
            return Err(ProviderError::MissingConfig(
                "GROQ_API_KEY nicht gesetzt. Registrierung unter https://console.groq.com (kostenlose Credits)"
                    .to_string(),
            ));
        }
        validate_header_secret(GROQ_API_KEY_ENV_VAR, &api_key)?;
        let _ = join_base_url(&base_url, &["audio", "transcriptions"])?;
        Ok(Self {
            client,
            base_url,
            api_key,
        })
    }

    /// Create a Groq provider reading `GROQ_API_KEY` from the environment.
    pub fn from_env(client: Client) -> Result<Self, ProviderError> {
        let api_key = read_env_non_empty(GROQ_API_KEY_ENV_VAR).ok_or_else(|| {
            ProviderError::MissingConfig(
                "GROQ_API_KEY nicht gesetzt. Registrierung unter https://console.groq.com (kostenlose Credits)"
                    .to_string(),
            )
        })?;
        Self::new(client, api_key)
    }

    pub fn default_model(&self) -> &'static str {
        DEFAULT_GROQ_MODEL
    }

    /// Transcribe an audio file via Groq's Whisper API.
    pub async fn transcribe(
        &self,
        request: &TranscriptionRequest,
    ) -> Result<String, ProviderError> {
        let model = request
            .model
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .unwrap_or(DEFAULT_GROQ_MODEL);
        let language = normalize_auto_language(request.language.as_deref());
        let url = join_base_url(&self.base_url, &["audio", "transcriptions"])?;

        tracing::info!(
            provider = Self::NAME,
            model,
            language = language.as_deref().unwrap_or("auto"),
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
                response_format: "text",
                temperature: Some("0.0"),
            },
        )
        .await?;

        let result = extract_json_or_text_transcript(Self::NAME, response)?;
        tracing::debug!(
            provider = Self::NAME,
            chars = result.chars().count(),
            "cloud transcription completed"
        );
        Ok(result)
    }
}
