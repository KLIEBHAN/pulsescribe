//! Local speech recognition abstractions, model catalog, Hugging Face cache discovery,
//! streaming model downloads, and optional native backends (`whisper-rs` + `sherpa-onnx` C-API)
//! for PulseScribe (ADR-003 Phase 4 / 5a).

pub mod cache;
pub mod catalog;
pub mod download;
pub mod manager;
#[cfg(feature = "local-sherpa")]
pub mod sherpa_backend;
#[cfg(feature = "local-sherpa")]
mod sherpa_ffi;
#[cfg(feature = "local-whisper")]
pub mod whisper_backend;

use std::borrow::Cow;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;

use pulsescribe_core::vocabulary::LOCAL_KEYWORD_LIMIT;
use thiserror::Error;

pub use cache::{InstalledModel, ModelCache, ModelInstallSource, ModelStatus};
pub use catalog::{
    build_hf_resolve_url, catalog_models, is_canary_language, is_cohere_language,
    resolve_canary_language, resolve_cohere_language, resolve_model_spec, LocalEngineKind,
    ModelSpec, CANARY_LANGUAGES, COHERE_LANGUAGES, DEFAULT_HF_BASE_URL, DEFAULT_LOCAL_MODEL_ID,
    STREAMING_PREVIEW_MODEL_ID,
};
pub use download::{DownloadProgress, ModelDownloader};
pub use manager::{LocalAsrManager, LocalRuntimeCapabilities};
#[cfg(feature = "local-sherpa")]
pub use sherpa_backend::{
    default_sherpa_library_candidates, SherpaOfflineEngine, SherpaOnlineModel,
    SherpaStreamingSession, SHERPA_LIB_DIR_ENV_VAR, SHERPA_SUPPORTED_ABI_PREFIX,
};
#[cfg(feature = "local-whisper")]
pub use whisper_backend::WhisperEngine;

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

/// Synchronous local speech-to-text engine (`whisper-rs` and `sherpa-onnx` offline backends).
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

fn strip_nuls(input: &str) -> Cow<'_, str> {
    if input.contains('\0') {
        Cow::Owned(input.replace('\0', ""))
    } else {
        Cow::Borrowed(input)
    }
}

/// Normalize an optional BCP-47/ISO language tag to its lowercase base ISO code (`"de-DE"` ->
/// `"de"`, `"en_US"` -> `"en"`), treating `None`, blank, or `"auto"` as `None` and stripping any
/// interior NUL bytes.
pub(crate) fn normalize_language(language: Option<&str>) -> Option<String> {
    let cleaned = strip_nuls(language?);
    let trimmed = cleaned.trim();
    if trimmed.is_empty() || trimmed.eq_ignore_ascii_case("auto") {
        return None;
    }
    let base = trimmed.split(['-', '_']).next().unwrap_or("").trim();
    if base.is_empty() || base.eq_ignore_ascii_case("auto") {
        return None;
    }
    Some(base.to_ascii_lowercase())
}

/// Build a comma-separated Whisper `initial_prompt` from custom vocabulary keywords.
///
/// Strips interior NUL bytes, trims blanks, deduplicates case-insensitively while preserving order,
/// and caps at `LOCAL_KEYWORD_LIMIT` (`50`), matching `utils/vocabulary.py`.
pub fn format_whisper_initial_prompt(keywords: &[String]) -> Option<String> {
    let mut seen = Vec::with_capacity(keywords.len().min(LOCAL_KEYWORD_LIMIT));
    let mut prompt_terms = Vec::with_capacity(keywords.len().min(LOCAL_KEYWORD_LIMIT));

    for raw in keywords {
        let cleaned = strip_nuls(raw);
        let trimmed = cleaned.trim();
        if trimmed.is_empty() {
            continue;
        }
        let lowered = trimmed.to_lowercase();
        if seen.iter().any(|existing| existing == &lowered) {
            continue;
        }
        seen.push(lowered);
        prompt_terms.push(trimmed.to_owned());
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

/// Maximum piece length (in seconds) for NeMo offline models (`parakeet-v3` and `canary-1b-v2`).
///
/// Mirrors `providers/nemo_model.py`: Canary emits empty output beyond ~37 s and Parakeet drops
/// words on long utterances; 15 s segments cut at the quietest 100 ms in the final 5 s preserve
/// full accuracy.
pub const NEMO_SEGMENT_SECONDS: usize = 15;
const NEMO_CUT_SEARCH_SECONDS: usize = 5;

/// Split mono `f32` audio into pieces of at most [`NEMO_SEGMENT_SECONDS`], cutting each long piece
/// at the midpoint of the quietest 100 ms frame in its last 5 seconds.
pub fn split_nemo_segments(samples: &[f32], sample_rate: u32) -> Vec<&[f32]> {
    if samples.is_empty() || sample_rate == 0 {
        return Vec::new();
    }
    let sr = sample_rate as usize;
    let limit = NEMO_SEGMENT_SECONDS.saturating_mul(sr);
    let search = NEMO_CUT_SEARCH_SECONDS.saturating_mul(sr);
    let frame = (sr / 10).max(1);
    if limit == 0 || samples.len() <= limit {
        return vec![samples];
    }

    let mut pieces = Vec::new();
    let mut start = 0usize;
    while samples.len().saturating_sub(start) > limit {
        let window_start = start + limit.saturating_sub(search);
        let window_end = start + limit;
        let window = &samples[window_start..window_end];
        let n_frames = window.len() / frame;
        let cut = if n_frames == 0 {
            window_end
        } else {
            let mut best_frame = 0usize;
            let mut best_energy = f64::INFINITY;
            for f in 0..n_frames {
                let slice = &window[f * frame..(f + 1) * frame];
                let energy: f64 = slice
                    .iter()
                    .map(|&s| {
                        let v = f64::from(s);
                        v * v
                    })
                    .sum();
                if energy < best_energy {
                    best_energy = energy;
                    best_frame = f;
                }
            }
            window_start + best_frame * frame + frame / 2
        };
        let cut = cut.max(start + 1).min(samples.len());
        pieces.push(&samples[start..cut]);
        start = cut;
    }
    if start < samples.len() {
        pieces.push(&samples[start..]);
    }
    pieces
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn format_whisper_initial_prompt_deduplicates_trims_strips_nul_and_caps() {
        assert_eq!(format_whisper_initial_prompt(&[]), None);
        assert_eq!(
            format_whisper_initial_prompt(&["  ".to_owned(), "\0".to_owned()]),
            None
        );

        let terms = vec![
            " Pulse\0Scribe ".to_owned(),
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

    #[test]
    fn normalize_language_strips_nul_region_and_handles_auto() {
        assert_eq!(normalize_language(None), None);
        assert_eq!(normalize_language(Some("  auto  ")), None);
        assert_eq!(normalize_language(Some("DE\0")), Some("de".to_owned()));
        assert_eq!(normalize_language(Some("de-DE")), Some("de".to_owned()));
        assert_eq!(normalize_language(Some("en_US")), Some("en".to_owned()));
        assert_eq!(normalize_language(Some("pt-BR")), Some("pt".to_owned()));
    }

    #[test]
    fn split_nemo_segments_cuts_at_quietest_100ms_frame() {
        let sr = 16_000usize;
        // 18 s of loud tone with a 200 ms silent gap centered at 12.05 s (inside [10s..15s]).
        let mut audio = vec![0.5_f32; 18 * sr];
        for s in &mut audio[12 * sr..(12 * sr + sr / 5)] {
            *s = 0.0;
        }
        let pieces = split_nemo_segments(&audio, sr as u32);
        assert_eq!(pieces.len(), 2);
        // Cut should fall cleanly inside the 12.0 s - 12.2 s quiet window.
        assert!(pieces[0].len() >= 12 * sr && pieces[0].len() <= 12 * sr + sr / 5);
        assert_eq!(pieces[0].len() + pieces[1].len(), audio.len());

        // Short audio stays intact.
        let short = vec![0.1_f32; 5 * sr];
        assert_eq!(split_nemo_segments(&short, sr as u32).len(), 1);
        assert!(split_nemo_segments(&[], sr as u32).is_empty());
    }
}
