//! Shared HTTP transport, URL construction, multipart file streaming, and bounded response helpers.

use std::path::Path;

use reqwest::header::{AUTHORIZATION, CONTENT_TYPE};
use reqwest::multipart::{Form, Part};
use reqwest::{Body, Client, Response};
use tokio::fs::File;
use tokio_util::io::ReaderStream;
use url::{Host, Url};

use crate::ProviderError;

/// Cap error response body reads so a malformed/HTML error page cannot exhaust RAM.
pub(crate) const MAX_ERROR_BODY_BYTES: usize = 16 * 1024;
/// Cap normal API response reads (16 MiB is far above any realistic transcript or model list).
pub(crate) const MAX_RESPONSE_BODY_BYTES: usize = 16 * 1024 * 1024;
/// Stream chunk size for disk-to-HTTP uploads without buffering whole audio files in RAM.
pub(crate) const UPLOAD_STREAM_CHUNK_BYTES: usize = 64 * 1024;

/// Read a trimmed, non-empty environment variable value.
pub(crate) fn read_env_non_empty(var: &str) -> Option<String> {
    std::env::var(var)
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
}

/// Validate a base HTTP(S) URL and append path segments while preserving any existing path prefix.
pub(crate) fn join_base_url(base_url: &str, segments: &[&str]) -> Result<Url, ProviderError> {
    let trimmed = base_url.trim();
    if trimmed.is_empty() {
        return Err(ProviderError::InvalidInput(
            "base URL must not be empty".to_string(),
        ));
    }

    let mut url = Url::parse(trimmed).map_err(|err| {
        ProviderError::InvalidInput(format!("invalid base URL '{trimmed}': {err}"))
    })?;

    match url.scheme() {
        "http" | "https" => {}
        scheme => {
            return Err(ProviderError::InvalidInput(format!(
                "unsupported URL scheme '{scheme}' in '{trimmed}' (expected http or https)"
            )));
        }
    }
    if url.host().is_none() {
        return Err(ProviderError::InvalidInput(format!(
            "base URL '{trimmed}' is missing a host"
        )));
    }
    url.set_fragment(None);

    let mut clean_path = url.path().trim_end_matches('/').to_string();
    for segment in segments {
        clean_path.push('/');
        clean_path.push_str(segment);
    }
    url.set_path(&clean_path);

    Ok(url)
}

/// Reject ASCII control characters (such as CR/LF) in credentials before putting them in HTTP headers.
pub(crate) fn validate_header_secret(label: &str, value: &str) -> Result<(), ProviderError> {
    if value.bytes().any(|b| b < 0x20 || b == 0x7f) {
        return Err(ProviderError::InvalidInput(format!(
            "{label} contains invalid control characters"
        )));
    }
    Ok(())
}

/// Returns `true` when `url` points to a loopback address (`localhost`, `127.0.0.0/8`, `::1`).
pub(crate) fn is_loopback_url(url: &Url) -> bool {
    match url.host() {
        Some(Host::Domain(domain)) => domain.eq_ignore_ascii_case("localhost"),
        Some(Host::Ipv4(ip)) => ip.is_loopback(),
        Some(Host::Ipv6(ip)) => {
            ip.is_loopback() || ip.to_ipv4_mapped().is_some_and(|v4| v4.is_loopback())
        }
        None => false,
    }
}

/// Infer an audio MIME type from a file extension for multipart and raw binary uploads.
pub(crate) fn audio_mime_for_path(path: &Path) -> &'static str {
    match path
        .extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| ext.to_ascii_lowercase())
        .as_deref()
    {
        Some("wav") => "audio/wav",
        Some("mp3") => "audio/mpeg",
        Some("m4a" | "mp4") => "audio/mp4",
        Some("ogg" | "oga") => "audio/ogg",
        Some("flac") => "audio/flac",
        Some("webm") => "audio/webm",
        Some("opus") => "audio/opus",
        _ => "application/octet-stream",
    }
}

/// Stream a file from disk into a `reqwest::Body` along with its byte length.
pub(crate) async fn open_streaming_file_body(path: &Path) -> Result<(Body, u64), ProviderError> {
    let file = File::open(path).await?;
    let len = file.metadata().await?.len();
    let stream = ReaderStream::with_capacity(file, UPLOAD_STREAM_CHUNK_BYTES);
    Ok((Body::wrap_stream(stream), len))
}

/// Captured HTTP response body and content type after verifying a 2xx status code.
pub(crate) struct CheckedResponse {
    pub body: String,
    pub is_json: bool,
}

/// Verify HTTP status and read a UTF-8 response body up to a strict byte limit.
pub(crate) async fn read_checked_response(
    provider: &'static str,
    response: Response,
) -> Result<CheckedResponse, ProviderError> {
    let status = response.status();
    let is_json = response
        .headers()
        .get(CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .is_some_and(|ct| ct.to_ascii_lowercase().contains("json"));

    if !status.is_success() {
        let raw_error = read_bounded_bytes(provider, response, MAX_ERROR_BODY_BYTES, false)
            .await
            .unwrap_or_default();
        let text = String::from_utf8_lossy(&raw_error);
        let snippet = truncate_chars(text.trim(), 512);
        return Err(ProviderError::ApiStatus {
            provider,
            status: status.as_u16(),
            body: snippet,
        });
    }

    let bytes = read_bounded_bytes(provider, response, MAX_RESPONSE_BODY_BYTES, true).await?;
    let body = String::from_utf8(bytes).map_err(|err| ProviderError::InvalidResponse {
        provider,
        detail: format!("response body is not valid UTF-8: {err}"),
    })?;
    Ok(CheckedResponse { body, is_json })
}

async fn read_bounded_bytes(
    provider: &'static str,
    mut response: Response,
    limit: usize,
    error_on_overflow: bool,
) -> Result<Vec<u8>, ProviderError> {
    let hint = response
        .content_length()
        .and_then(|n| usize::try_from(n).ok())
        .map_or(0, |n| n.min(limit).min(64 * 1024));
    let mut buf = Vec::with_capacity(hint);

    while let Some(chunk) = response.chunk().await? {
        let remaining = limit.saturating_sub(buf.len());
        if chunk.len() > remaining {
            if error_on_overflow {
                return Err(ProviderError::InvalidResponse {
                    provider,
                    detail: format!("response body exceeded {limit}-byte limit"),
                });
            }
            buf.extend_from_slice(&chunk[..remaining]);
            break;
        }
        buf.extend_from_slice(&chunk);
    }

    Ok(buf)
}

pub(crate) fn truncate_chars(input: &str, max_chars: usize) -> String {
    let mut chars = input.chars();
    let prefix: String = chars.by_ref().take(max_chars).collect();
    if chars.next().is_some() {
        format!("{prefix}...")
    } else {
        prefix
    }
}

/// Parameters for an OpenAI-style multipart `/audio/transcriptions` upload.
pub(crate) struct MultipartTranscriptionParams<'a> {
    pub provider: &'static str,
    pub url: Url,
    pub api_key: &'a str,
    pub audio_path: &'a Path,
    pub model: &'a str,
    pub language: Option<&'a str>,
    pub response_format: &'a str,
    pub temperature: Option<&'a str>,
}

/// Send an OpenAI-compatible multipart `/audio/transcriptions` request streaming audio from disk.
pub(crate) async fn post_multipart_transcription(
    client: &Client,
    params: MultipartTranscriptionParams<'_>,
) -> Result<CheckedResponse, ProviderError> {
    let (body, file_len) = open_streaming_file_body(params.audio_path).await?;
    let filename = params
        .audio_path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("audio.wav")
        .to_string();
    let mime = audio_mime_for_path(params.audio_path);

    let file_part = Part::stream_with_length(body, file_len)
        .file_name(filename)
        .mime_str(mime)
        .map_err(|err| ProviderError::InvalidInput(format!("invalid MIME type '{mime}': {err}")))?;

    let mut form = Form::new()
        .part("file", file_part)
        .text("model", params.model.to_string())
        .text("response_format", params.response_format.to_string());

    if let Some(language) = params.language {
        form = form.text("language", language.to_string());
    }
    if let Some(temperature) = params.temperature {
        form = form.text("temperature", temperature.to_string());
    }

    let response = client
        .post(params.url)
        .header(AUTHORIZATION, format!("Bearer {}", params.api_key))
        .multipart(form)
        .send()
        .await?;

    read_checked_response(params.provider, response).await
}

/// Extract a transcript string from a JSON object (`{"text": "..."}`) or plain text response.
pub(crate) fn extract_json_or_text_transcript(
    provider: &'static str,
    response: CheckedResponse,
) -> Result<String, ProviderError> {
    if response.is_json {
        return parse_json_text_field(provider, &response.body);
    }
    Ok(response.body)
}

/// Parse a JSON response object and return its required string `"text"` field.
pub(crate) fn parse_json_text_field(
    provider: &'static str,
    body: &str,
) -> Result<String, ProviderError> {
    let value: serde_json::Value =
        serde_json::from_str(body).map_err(|err| ProviderError::InvalidResponse {
            provider,
            detail: format!("invalid JSON response: {err}"),
        })?;
    value
        .get("text")
        .and_then(serde_json::Value::as_str)
        .map(ToOwned::to_owned)
        .ok_or_else(|| ProviderError::InvalidResponse {
            provider,
            detail: "JSON response missing string 'text' field".to_string(),
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn join_base_url_preserves_version_prefix_with_or_without_trailing_slash() {
        assert_eq!(
            join_base_url("https://api.openai.com/v1", &["audio", "transcriptions"])
                .unwrap()
                .as_str(),
            "https://api.openai.com/v1/audio/transcriptions"
        );
        assert_eq!(
            join_base_url("http://mac-mini:7860/v1/", &["models"])
                .unwrap()
                .as_str(),
            "http://mac-mini:7860/v1/models"
        );
        assert_eq!(
            join_base_url("http://mac-mini:7860/v1///", &["models"])
                .unwrap()
                .as_str(),
            "http://mac-mini:7860/v1/models"
        );
        assert_eq!(
            join_base_url("http://127.0.0.1:8080", &["models"])
                .unwrap()
                .as_str(),
            "http://127.0.0.1:8080/models"
        );
        assert!(join_base_url("ftp://example.com/v1", &["models"]).is_err());
        assert!(join_base_url("   ", &["models"]).is_err());
    }

    #[test]
    fn loopback_detection_handles_ipv4_ipv6_and_localhost() {
        assert!(is_loopback_url(
            &Url::parse("http://localhost:7860/v1").unwrap()
        ));
        assert!(is_loopback_url(
            &Url::parse("http://LOCALHOST:7860/v1").unwrap()
        ));
        assert!(is_loopback_url(
            &Url::parse("http://127.0.0.1:7860/v1").unwrap()
        ));
        assert!(is_loopback_url(
            &Url::parse("http://[::1]:7860/v1").unwrap()
        ));
        assert!(!is_loopback_url(
            &Url::parse("http://mac-mini:7860/v1").unwrap()
        ));
        assert!(!is_loopback_url(
            &Url::parse("http://192.168.1.50:7860/v1").unwrap()
        ));
    }

    #[test]
    fn truncate_chars_preserves_utf8_boundaries() {
        assert_eq!(truncate_chars("Grüße", 10), "Grüße");
        assert_eq!(truncate_chars("Grüße aus München", 5), "Grüße...");
    }

    #[test]
    fn validate_header_secret_rejects_control_characters() {
        assert!(validate_header_secret("OPENAI_API_KEY", "sk-valid-key_123").is_ok());
        assert!(matches!(
            validate_header_secret("OPENAI_API_KEY", "sk-bad\r\nX-Evil: 1"),
            Err(ProviderError::InvalidInput(_))
        ));
    }
}
