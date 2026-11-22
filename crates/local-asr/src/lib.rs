//! Local speech recognition abstractions, model catalog, Hugging Face cache discovery,
//! and streaming model downloads for PulseScribe (ADR-003 Phase 4 / 5a).
//!
//! Keeps model resolution, cache reuse (`~/.pulsescribe/models/` + `~/.cache/huggingface/hub/`),
//! and engine traits 100% pure Rust with optional native ML backends behind Cargo features.

pub mod cache;
pub mod catalog;
pub mod download;

use std::sync::atomic::AtomicBool;
use std::sync::Arc;

use pulsescribe_core::vocabulary::LOCAL_KEYWORD_LIMIT;
use thiserror::Error;

pub use cache::{InstalledModel, ModelCache, ModelInstallSource, ModelStatus};
pub use catalog::{
    build_hf_resolve_url, catalog_models, is_canary_language, resolve_model_spec, LocalEngineKind,
    ModelSpec, CANARY_LANGUAGES, DEFAULT_HF_BASE_URL, DEFAULT_LOCAL_MODEL_ID,
};
pub use download::{DownloadProgress, ModelDownloader};

/// Canonical mono sample rate required by all local ASR engines (16 kHz).
pub const LOCAL_ASR_SAMPLE_RATE: u32 = 16_000;

/// Request passed to a [`LocalAsrEngine`] for offline transcription.
#[derive(Debug, Clone)]
pub struct LocalAsrRequest {
    /// Mono normalized `f32` audio samples at 16 kHz.
    pub samples_16k: Vec<f32>,
    /// Optional ISO-639-1 language code (`"de"`, `"en"`, …); `None` enables auto-detect.
    pub language: Option<String>,
    /// Optional custom vocabulary biasing keywords (capped at `LOCAL_KEYWORD_LIMIT`).
    pub keywords: Vec<String>,
    /// Cooperative cancellation flag polled by native backends during decoding.
    pub cancel_flag: Arc<AtomicBool>,
}

impl LocalAsrRequest {
    pub fn new(samples_16k: Vec<f32>) -> Self {
        Self {
            samples_16k,
            language: None,
            keywords: Vec::new(),
            cancel_flag: Arc::new(AtomicBool::new(false)),
        }
    }

    pub fn with_language(mut self, language: Option<String>) -> Self {
        self.language = language;
        self
    }

    pub fn with_keywords(mut self, keywords: Vec<String>) -> Self {
        self.keywords = keywords;
        self
    }

    pub fn with_cancel_flag(mut self, cancel_flag: Arc<AtomicBool>) -> Self {
        self.cancel_flag = cancel_flag;
        self
    }
}

/// Errors produced by local ASR model resolution, downloading, loading, or inference.
#[derive(Debug, Error)]
pub enum LocalAsrError {
    #[error("unknown local model '{0}'")]
    UnknownModel(String),

    #[error("local model '{0}' is not downloaded yet")]
    ModelNotDownloaded(String),

    #[error("invalid local model configuration: {0}")]
    InvalidModel(String),

    #[error("required local ASR runtime is unavailable: {0}")]
    MissingRuntime(String),

    #[error("local ASR operation cancelled")]
    Cancelled,

    #[error("model download failed: {0}")]
    Download(String),

    #[error("local inference failed: {0}")]
    Inference(String),

    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
}

impl LocalAsrError {
    pub const fn is_cancelled(&self) -> bool {
        matches!(self, Self::Cancelled)
    }
}

/// Synchronous local speech-to-text engine (implemented by `whisper-rs` and `sherpa-onnx`
/// offline backends in PR 4.2, and mockable in tests).
pub trait LocalAsrEngine: Send + Sync {
    /// Canonical catalog model identifier (or file stem for custom local paths).
    fn model_id(&self) -> &str;

    /// Short backend identifier (`"whisper-rs"`, `"sherpa-onnx"`).
    fn backend_name(&self) -> &'static str;

    /// Transcribe 16 kHz mono `f32` audio samples to text.
    fn transcribe(&self, request: &LocalAsrRequest) -> Result<String, LocalAsrError>;
}

/// Stateful local streaming recognition session (e.g., Nemotron 3.5 live preview).
pub trait LocalStreamingSession: Send {
    /// Feed 16 kHz mono `f32` audio samples and decode any ready frames.
    fn feed(&mut self, samples_16k: &[f32]) -> Result<(), LocalAsrError>;

    /// Return the cumulative recognized preview text so far.
    fn text(&self) -> Result<String, LocalAsrError>;
}

/// Build a comma-separated Whisper `initial_prompt` from custom vocabulary keywords.
///
/// Trims blanks, deduplicates case-insensitively while preserving order, and caps at
/// `LOCAL_KEYWORD_LIMIT` (`50`), matching `utils/vocabulary.py`.
pub fn format_whisper_initial_prompt(keywords: &[String]) -> Option<String> {
    let mut seen = Vec::with_capacity(keywords.len().min(LOCAL_KEYWORD_LIMIT));
    let mut prompt_terms = Vec::with_capacity(keywords.len().min(LOCAL_KEYWORD_LIMIT));

    for raw in keywords {
        let trimmed = raw.trim();
        if trimmed.is_empty() {
            continue;
        }
        let lowered = trimmed.to_lowercase();
        if seen.iter().any(|existing| existing == &lowered) {
            continue;
        }
        seen.push(lowered);
        prompt_terms.push(trimmed);
        if prompt_terms.len() >= LOCAL_KEYWORD_LIMIT {
            break;
        }
    }

    if prompt_terms.is_empty() {
        None
    } else {
        Some(prompt_terms.join(", "))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn format_whisper_initial_prompt_deduplicates_trims_and_caps() {
        assert_eq!(format_whisper_initial_prompt(&[]), None);
        assert_eq!(
            format_whisper_initial_prompt(&["  ".to_owned(), "".to_owned()]),
            None
        );

        let terms = vec![
            " PulseScribe ".to_owned(),
            "pulsescribe".to_owned(),
            "Deepgram".to_owned(),
            "Tauri".to_owned(),
        ];
        assert_eq!(
            format_whisper_initial_prompt(&terms).as_deref(),
            Some("PulseScribe, Deepgram, Tauri")
        );

        let many: Vec<String> = (0..80).map(|i| format!("Term{i}")).collect();
        let prompt = format_whisper_initial_prompt(&many).unwrap();
        assert_eq!(prompt.split(", ").count(), LOCAL_KEYWORD_LIMIT);
    }
}
