//! Cloud transcription providers for PulseScribe (`OpenAI`, `Groq`, `Deepgram REST`,
//! and `OpenAI-Compatible` servers).
//!
//! Uses a shared pooled `reqwest::Client` (rustls with OS root certificates) and streams
//! audio files directly from disk without buffering whole files in memory.

pub mod deepgram;
pub mod groq;
mod http;
pub mod openai;
pub mod openai_compatible;

use std::fmt;
use std::path::PathBuf;
use std::str::FromStr;
use std::time::Duration;

use reqwest::Client;
use thiserror::Error;

pub const DEFAULT_OPENAI_MODEL: &str = "gpt-4o-transcribe";
pub const DEFAULT_GROQ_MODEL: &str = "whisper-large-v3";
pub const DEFAULT_DEEPGRAM_MODEL: &str = "nova-3";
pub const DEFAULT_OPENAI_COMPATIBLE_MODEL: &str = "whisper-1";

pub const OPENAI_API_BASE_URL: &str = "https://api.openai.com/v1";
pub const GROQ_API_BASE_URL: &str = "https://api.groq.com/openai/v1";
pub const DEEPGRAM_API_BASE_URL: &str = "https://api.deepgram.com/v1";

const DEFAULT_CONNECT_TIMEOUT: Duration = Duration::from_secs(15);
const DEFAULT_REQUEST_TIMEOUT: Duration = Duration::from_secs(120);

/// Output format requested by the caller (primarily used by OpenAI `whisper-1`).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum ResponseFormat {
    #[default]
    Text,
    Json,
    Srt,
    Vtt,
}

impl ResponseFormat {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Text => "text",
            Self::Json => "json",
            Self::Srt => "srt",
            Self::Vtt => "vtt",
        }
    }
}

impl fmt::Display for ResponseFormat {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

impl FromStr for ResponseFormat {
    type Err = ProviderError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.trim().to_ascii_lowercase().as_str() {
            "" | "text" => Ok(Self::Text),
            "json" => Ok(Self::Json),
            "srt" => Ok(Self::Srt),
            "vtt" => Ok(Self::Vtt),
            other => Err(ProviderError::InvalidInput(format!(
                "unsupported response format '{other}' (expected text, json, srt, or vtt)"
            ))),
        }
    }
}

/// Normalized request payload passed to cloud REST transcription providers.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TranscriptionRequest {
    pub audio_path: PathBuf,
    pub model: Option<String>,
    pub language: Option<String>,
    pub response_format: ResponseFormat,
    pub keywords: Vec<String>,
}

impl TranscriptionRequest {
    pub fn new(audio_path: impl Into<PathBuf>) -> Self {
        Self {
            audio_path: audio_path.into(),
            model: None,
            language: None,
            response_format: ResponseFormat::Text,
            keywords: Vec::new(),
        }
    }

    pub fn with_model(mut self, model: impl Into<String>) -> Self {
        self.model = Some(model.into());
        self
    }

    pub fn with_language(mut self, language: impl Into<String>) -> Self {
        self.language = Some(language.into());
        self
    }

    pub fn with_response_format(mut self, response_format: ResponseFormat) -> Self {
        self.response_format = response_format;
        self
    }

    pub fn with_keywords(mut self, keywords: Vec<String>) -> Self {
        self.keywords = keywords;
        self
    }
}

/// Errors returned by cloud transcription providers.
#[derive(Debug, Error)]
pub enum ProviderError {
    #[error("{0}")]
    MissingConfig(String),

    #[error("{0}")]
    InvalidInput(String),

    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    #[error("HTTP request failed: {0}")]
    Http(#[from] reqwest::Error),

    #[error("{provider} API returned HTTP {status}: {body}")]
    ApiStatus {
        provider: &'static str,
        status: u16,
        body: String,
    },

    #[error("{provider} returned an invalid response: {detail}")]
    InvalidResponse {
        provider: &'static str,
        detail: String,
    },
}

/// Return `None` for unset, blank, or `"auto"` language sentinels (`providers/_language.py`).
pub fn normalize_auto_language(language: Option<&str>) -> Option<String> {
    let trimmed = language?.trim();
    if trimmed.is_empty() || trimmed.eq_ignore_ascii_case("auto") {
        return None;
    }
    Some(trimmed.to_string())
}

/// Build the shared pooled HTTP client used across cloud providers.
pub fn build_http_client() -> Result<Client, ProviderError> {
    Ok(Client::builder()
        .connect_timeout(DEFAULT_CONNECT_TIMEOUT)
        .timeout(DEFAULT_REQUEST_TIMEOUT)
        .user_agent(concat!("pulsescribe/", env!("CARGO_PKG_VERSION")))
        .build()?)
}

/// Polymorphic cloud REST transcription provider dispatcher.
#[derive(Debug, Clone)]
pub enum CloudProvider {
    OpenAi(openai::OpenAiProvider),
    Groq(groq::GroqProvider),
    Deepgram(deepgram::DeepgramRestProvider),
    OpenAiCompatible(openai_compatible::OpenAiCompatibleProvider),
}

impl CloudProvider {
    /// Construct a cloud provider from a PulseScribe mode name (`api`, `openai`, `groq`,
    /// `deepgram`, `openai_compatible`) using environment credentials.
    pub fn from_mode_env(mode: &str, client: Client) -> Result<Self, ProviderError> {
        match mode.trim().to_ascii_lowercase().as_str() {
            "api" | "openai" => Ok(Self::OpenAi(openai::OpenAiProvider::from_env(client)?)),
            "groq" => Ok(Self::Groq(groq::GroqProvider::from_env(client)?)),
            "deepgram" => Ok(Self::Deepgram(deepgram::DeepgramRestProvider::from_env(
                client,
            )?)),
            "openai_compatible" => Ok(Self::OpenAiCompatible(
                openai_compatible::OpenAiCompatibleProvider::from_env(client)?,
            )),
            other => Err(ProviderError::InvalidInput(format!(
                "unsupported cloud transcription mode '{other}'"
            ))),
        }
    }

    pub fn name(&self) -> &'static str {
        match self {
            Self::OpenAi(_) => openai::OpenAiProvider::NAME,
            Self::Groq(_) => groq::GroqProvider::NAME,
            Self::Deepgram(_) => deepgram::DeepgramRestProvider::NAME,
            Self::OpenAiCompatible(_) => openai_compatible::OpenAiCompatibleProvider::NAME,
        }
    }

    pub fn default_model(&self) -> &str {
        match self {
            Self::OpenAi(p) => p.default_model(),
            Self::Groq(p) => p.default_model(),
            Self::Deepgram(p) => p.default_model(),
            Self::OpenAiCompatible(p) => p.default_model(),
        }
    }

    /// Returns `true` when this provider supports live streaming transcription.
    ///
    /// All REST file-upload providers return `false`; live Deepgram WebSocket streaming lives in a
    /// dedicated streaming client (PR 2.4).
    pub fn supports_streaming(&self) -> bool {
        false
    }

    pub async fn transcribe(
        &self,
        request: &TranscriptionRequest,
    ) -> Result<String, ProviderError> {
        match self {
            Self::OpenAi(p) => p.transcribe(request).await,
            Self::Groq(p) => p.transcribe(request).await,
            Self::Deepgram(p) => p.transcribe(request).await,
            Self::OpenAiCompatible(p) => p.transcribe(request).await,
        }
    }
}

pub use deepgram::{build_listen_url, DeepgramRestProvider};
pub use groq::GroqProvider;
pub use openai::OpenAiProvider;
pub use openai_compatible::{
    is_unencrypted_remote_key, list_server_models, OpenAiCompatibleProvider,
    DEFAULT_LIST_MODELS_TIMEOUT,
};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_auto_language_strips_auto_and_whitespace() {
        assert_eq!(normalize_auto_language(None), None);
        assert_eq!(normalize_auto_language(Some("")), None);
        assert_eq!(normalize_auto_language(Some("   ")), None);
        assert_eq!(normalize_auto_language(Some("auto")), None);
        assert_eq!(normalize_auto_language(Some(" AUTO ")), None);
        assert_eq!(
            normalize_auto_language(Some(" de ")),
            Some("de".to_string())
        );
    }

    #[test]
    fn response_format_parses_case_insensitively() {
        assert_eq!(
            "TEXT".parse::<ResponseFormat>().unwrap(),
            ResponseFormat::Text
        );
        assert_eq!(
            "json".parse::<ResponseFormat>().unwrap(),
            ResponseFormat::Json
        );
        assert_eq!(
            "srt".parse::<ResponseFormat>().unwrap(),
            ResponseFormat::Srt
        );
        assert_eq!(
            "vtt".parse::<ResponseFormat>().unwrap(),
            ResponseFormat::Vtt
        );
        assert!("xml".parse::<ResponseFormat>().is_err());
    }

    #[test]
    fn provider_debug_output_redacts_api_keys_and_rest_reports_non_streaming() {
        let client = build_http_client().unwrap();
        let providers = [
            CloudProvider::OpenAi(OpenAiProvider::new(client.clone(), "sk-secret-openai").unwrap()),
            CloudProvider::Groq(GroqProvider::new(client.clone(), "gsk-secret-groq").unwrap()),
            CloudProvider::Deepgram(
                DeepgramRestProvider::new(client.clone(), "dg-secret-token").unwrap(),
            ),
            CloudProvider::OpenAiCompatible(
                OpenAiCompatibleProvider::new(
                    client,
                    "https://lan.example/v1",
                    Some("lan-secret-key".to_string()),
                    None,
                )
                .unwrap(),
            ),
        ];

        for provider in &providers {
            assert!(!provider.supports_streaming());
            let debug = format!("{provider:?}");
            assert!(!debug.contains("sk-secret-openai"));
            assert!(!debug.contains("gsk-secret-groq"));
            assert!(!debug.contains("dg-secret-token"));
            assert!(!debug.contains("lan-secret-key"));
            assert!(debug.contains("[REDACTED]"));
        }
    }
}
