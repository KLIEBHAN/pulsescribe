//! Deepgram REST API provider (`nova-3`, `nova-2`) with streaming file upload and vocabulary biasing.
//!
//! Mirrors `providers/deepgram.py`. WebSocket live streaming lives in a separate module (PR 2.4).

use std::fmt;

use pulsescribe_core::vocabulary::DEEPGRAM_KEYWORD_LIMIT;
use reqwest::header::{AUTHORIZATION, CONTENT_LENGTH, CONTENT_TYPE};
use reqwest::Client;
use serde::Deserialize;
use url::Url;

use crate::http::{
    audio_mime_for_path, join_base_url, open_streaming_file_body, read_checked_response,
    read_env_non_empty, validate_header_secret,
};
use crate::{
    normalize_auto_language, ProviderError, TranscriptionRequest, DEEPGRAM_API_BASE_URL,
    DEFAULT_DEEPGRAM_MODEL,
};

pub const DEEPGRAM_API_KEY_ENV_VAR: &str = "DEEPGRAM_API_KEY";

/// Build the Deepgram `/v1/listen` URL including model, language/auto-detect, and vocabulary params.
///
/// Matches `providers/deepgram.py`: `nova-3*` uses `keyterm`, older models use `keywords`,
/// capped at `DEEPGRAM_KEYWORD_LIMIT` (`100`).
pub fn build_listen_url(
    base_url: &str,
    model: &str,
    language: Option<&str>,
    keywords: &[String],
) -> Result<Url, ProviderError> {
    let mut url = join_base_url(base_url, &["listen"])?;
    let normalized_lang = normalize_auto_language(language);
    let vocab_param = if model.trim().to_ascii_lowercase().starts_with("nova-3") {
        "keyterm"
    } else {
        "keywords"
    };

    {
        let mut query = url.query_pairs_mut();
        query.append_pair("model", model);
        query.append_pair("smart_format", "true");
        query.append_pair("punctuate", "true");
        if let Some(lang) = normalized_lang.as_deref() {
            query.append_pair("language", lang);
        } else {
            query.append_pair("detect_language", "true");
        }
        for keyword in keywords
            .iter()
            .map(|k| k.trim())
            .filter(|k| !k.is_empty())
            .take(DEEPGRAM_KEYWORD_LIMIT)
        {
            query.append_pair(vocab_param, keyword);
        }
    }

    Ok(url)
}

#[derive(Debug, Deserialize)]
struct DeepgramResponse {
    results: Option<DeepgramResults>,
}

#[derive(Debug, Deserialize)]
struct DeepgramResults {
    #[serde(default)]
    channels: Vec<DeepgramChannel>,
}

#[derive(Debug, Deserialize)]
struct DeepgramChannel {
    #[serde(default)]
    alternatives: Vec<DeepgramAlternative>,
}

#[derive(Debug, Deserialize)]
struct DeepgramAlternative {
    transcript: Option<String>,
}

fn extract_transcript(body: &str) -> Result<String, ProviderError> {
    let parsed: DeepgramResponse =
        serde_json::from_str(body).map_err(|err| ProviderError::InvalidResponse {
            provider: DeepgramRestProvider::NAME,
            detail: format!("invalid JSON response: {err}"),
        })?;

    let transcript = parsed
        .results
        .and_then(|r| r.channels.into_iter().next())
        .and_then(|c| c.alternatives.into_iter().next())
        .and_then(|a| a.transcript);

    match transcript {
        Some(text) => Ok(text),
        None => {
            tracing::warn!(
                provider = DeepgramRestProvider::NAME,
                "Deepgram response contained no transcript alternative"
            );
            Ok(String::new())
        }
    }
}

/// Deepgram `/v1/listen` REST provider.
#[derive(Clone)]
pub struct DeepgramRestProvider {
    client: Client,
    base_url: String,
    api_key: String,
}

impl fmt::Debug for DeepgramRestProvider {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("DeepgramRestProvider")
            .field("base_url", &self.base_url)
            .field("api_key", &"[REDACTED]")
            .finish_non_exhaustive()
    }
}

impl DeepgramRestProvider {
    pub const NAME: &'static str = "deepgram";

    /// Create a Deepgram REST provider targeting `https://api.deepgram.com/v1`.
    pub fn new(client: Client, api_key: impl Into<String>) -> Result<Self, ProviderError> {
        Self::with_base_url(client, DEEPGRAM_API_BASE_URL, api_key)
    }

    /// Create a Deepgram REST provider with a custom base URL (useful for tests).
    pub fn with_base_url(
        client: Client,
        base_url: impl Into<String>,
        api_key: impl Into<String>,
    ) -> Result<Self, ProviderError> {
        let base_url = base_url.into().trim().to_string();
        let api_key = api_key.into().trim().to_string();
        if api_key.is_empty() {
            return Err(ProviderError::MissingConfig(
                "DEEPGRAM_API_KEY nicht gesetzt. Registrierung unter https://console.deepgram.com (200$ Startguthaben)"
                    .to_string(),
            ));
        }
        validate_header_secret(DEEPGRAM_API_KEY_ENV_VAR, &api_key)?;
        let _ = join_base_url(&base_url, &["listen"])?;
        Ok(Self {
            client,
            base_url,
            api_key,
        })
    }

    /// Create a Deepgram REST provider reading `DEEPGRAM_API_KEY` from the environment.
    pub fn from_env(client: Client) -> Result<Self, ProviderError> {
        let api_key = read_env_non_empty(DEEPGRAM_API_KEY_ENV_VAR).ok_or_else(|| {
            ProviderError::MissingConfig(
                "DEEPGRAM_API_KEY nicht gesetzt. Registrierung unter https://console.deepgram.com (200$ Startguthaben)"
                    .to_string(),
            )
        })?;
        Self::new(client, api_key)
    }

    pub fn default_model(&self) -> &'static str {
        DEFAULT_DEEPGRAM_MODEL
    }

    /// Transcribe an audio file by streaming its raw bytes directly to Deepgram `/v1/listen`.
    pub async fn transcribe(
        &self,
        request: &TranscriptionRequest,
    ) -> Result<String, ProviderError> {
        let model = request
            .model
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .unwrap_or(DEFAULT_DEEPGRAM_MODEL);
        let url = build_listen_url(
            &self.base_url,
            model,
            request.language.as_deref(),
            &request.keywords,
        )?;
        let (body, file_len) = open_streaming_file_body(&request.audio_path).await?;
        let mime = audio_mime_for_path(&request.audio_path);

        tracing::info!(
            provider = Self::NAME,
            model,
            audio_kb = file_len / 1024,
            language = normalize_auto_language(request.language.as_deref())
                .as_deref()
                .unwrap_or("auto"),
            vocab = request.keywords.len().min(DEEPGRAM_KEYWORD_LIMIT),
            "starting cloud transcription"
        );

        let response = self
            .client
            .post(url)
            .header(AUTHORIZATION, format!("Token {}", self.api_key))
            .header(CONTENT_TYPE, mime)
            .header(CONTENT_LENGTH, file_len)
            .body(body)
            .send()
            .await?;

        let checked = read_checked_response(Self::NAME, response).await?;
        let result = extract_transcript(&checked.body)?;
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
    fn build_listen_url_selects_keyterm_for_nova3_and_keywords_for_nova2() {
        let keywords: Vec<String> = (0..120).map(|i| format!("term{i}")).collect();
        let nova3 =
            build_listen_url(DEEPGRAM_API_BASE_URL, "nova-3", Some("de"), &keywords).unwrap();
        let pairs: Vec<(String, String)> = nova3.query_pairs().into_owned().collect();
        assert!(pairs.contains(&("model".to_string(), "nova-3".to_string())));
        assert!(pairs.contains(&("language".to_string(), "de".to_string())));
        assert!(!pairs.iter().any(|(k, _)| k == "detect_language"));
        assert_eq!(pairs.iter().filter(|(k, _)| k == "keyterm").count(), 100);
        assert_eq!(pairs.iter().filter(|(k, _)| k == "keywords").count(), 0);

        let nova2 = build_listen_url(
            DEEPGRAM_API_BASE_URL,
            "nova-2",
            Some("auto"),
            &["PulseScribe".to_string(), "  ".to_string()],
        )
        .unwrap();
        let pairs2: Vec<(String, String)> = nova2.query_pairs().into_owned().collect();
        assert!(pairs2.contains(&("detect_language".to_string(), "true".to_string())));
        assert!(!pairs2.iter().any(|(k, _)| k == "language"));
        assert_eq!(pairs2.iter().filter(|(k, _)| k == "keywords").count(), 1);
    }

    #[test]
    fn extract_transcript_returns_empty_string_when_alternatives_missing() {
        assert_eq!(
            extract_transcript(r#"{"results":{"channels":[]}}"#).unwrap(),
            ""
        );
        assert_eq!(
            extract_transcript(
                r#"{"results":{"channels":[{"alternatives":[{"transcript":"Hallo Welt"}]}]}}"#
            )
            .unwrap(),
            "Hallo Welt"
        );
    }
}
