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
    resolve_canary_language, resolve_cohere_language, resolve_model_spec, resolve_qwen3_language,
    LocalEngineKind, ModelSpec, CANARY_LANGUAGES, COHERE_LANGUAGES, DEFAULT_HF_BASE_URL,
    DEFAULT_LOCAL_MODEL_ID, STREAMING_PREVIEW_MODEL_ID,
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

/// Conservative cap on Qwen3-ASR hotwords so prompt tokens stay well below the 512-token KV cache
/// budget alongside 15 s of audio (~195 audio tokens) and generated output tokens.
const QWEN3_HOTWORD_LIMIT: usize = 20;
const QWEN3_HOTWORD_MAX_CHARS: usize = 200;

/// Format custom vocabulary keywords as ASCII comma-separated hotwords for Qwen3-ASR.
///
/// Strips interior NUL bytes and commas (used as delimiter by `sherpa-onnx`), deduplicates
/// case-insensitively while preserving order, and caps both item count and total length so the
/// Qwen3-ASR prompt stays within its KV-cache budget.
pub fn format_qwen3_hotwords(keywords: &[String]) -> Option<String> {
    let mut seen = Vec::with_capacity(keywords.len().min(QWEN3_HOTWORD_LIMIT));
    let mut terms = Vec::with_capacity(keywords.len().min(QWEN3_HOTWORD_LIMIT));
    let mut total_chars = 0usize;

    for raw in keywords {
        let cleaned = strip_nuls(raw);
        let no_commas = cleaned.replace(',', " ");
        let trimmed = no_commas.trim();
        if trimmed.is_empty() {
            continue;
        }
        let lowered = trimmed.to_lowercase();
        if seen.iter().any(|existing| existing == &lowered) {
            continue;
        }
        let term_chars = trimmed.chars().count();
        let sep_chars = usize::from(!terms.is_empty());
        if total_chars + sep_chars + term_chars > QWEN3_HOTWORD_MAX_CHARS {
            continue;
        }
        seen.push(lowered);
        terms.push(trimmed.to_owned());
        total_chars += sep_chars + term_chars;
        if terms.len() >= QWEN3_HOTWORD_LIMIT {
            break;
        }
    }

    if terms.is_empty() {
        None
    } else {
        Some(terms.join(","))
    }
}

/// Maximum piece length (in seconds) for NeMo Canary and encoder-decoder offline models
/// (`canary-1b-v2`, `cohere-transcribe`, `qwen3-asr-0.6b`).
///
/// Mirrors `providers/nemo_model.py`: Canary emits empty output beyond ~37 s and Qwen3-ASR's
/// 512-token KV cache budget is sized around ~15 s audio pieces.
pub const NEMO_SEGMENT_SECONDS: usize = 15;

/// Maximum piece length (in seconds) for NeMo TDT/RNN-T transducers (`parakeet-ultra`,
/// `parakeet-v3`).
///
/// Parakeet Ultra is trained for up to 30 s segments; cutting at 25 s avoids mid-word boundary
/// splits on typical 15–25 s dictations and cuts multi-segment overhead without dropping words.
pub const TRANSDUCER_SEGMENT_SECONDS: usize = 25;

const NEMO_CUT_SEARCH_SECONDS: usize = 5;

/// Conservative dead-silence ceiling RMS threshold for trimming leading/trailing silence before
/// offline CPU ASR (`0.0008`, ~`-62 dBFS`), kept well below `SPEECH_VAD_THRESHOLD_RMS` (`0.002`)
/// so quiet/whispered speech (e.g. `0.001` RMS) preceding or following louder speech is never
/// clipped. On very quiet recordings the threshold scales down further to 5% of peak frame RMS.
const SILENCE_TRIM_MAX_THRESHOLD_RMS: f32 = 0.0008;
const SILENCE_TRIM_RELATIVE_RATIO: f64 = 0.05;
const SILENCE_TRIM_FRAME_MS: usize = 20;
const SILENCE_TRIM_PADDING_MS: usize = 250;

/// Trim dead leading and trailing silence from mono `f32` audio, preserving 250 ms of padding on
/// both sides of the first and last active 20 ms frame.
///
/// Uses a dedicated dead-silence floor `min(0.0008, 0.05 * max_frame_rms)` so dead hotkey
/// lead-in/tail silence is skipped while quiet speech before or after louder speech remains intact.
/// Returns `samples` unchanged when empty, when `sample_rate == 0`, or when no frame exceeds the
/// dead-silence floor.
pub fn trim_leading_trailing_silence(samples: &[f32], sample_rate: u32) -> &[f32] {
    if samples.is_empty() || sample_rate == 0 {
        return samples;
    }
    let sr = sample_rate as usize;
    let frame = ((sr * SILENCE_TRIM_FRAME_MS) / 1000).max(1);
    let max_threshold_sq =
        f64::from(SILENCE_TRIM_MAX_THRESHOLD_RMS) * f64::from(SILENCE_TRIM_MAX_THRESHOLD_RMS);

    let frame_rms_sq = |chunk: &[f32]| -> f64 {
        let mut sum_sq = 0.0_f64;
        for &sample in chunk {
            if sample.is_finite() {
                let v = f64::from(sample);
                sum_sq += v * v;
            }
        }
        sum_sq / chunk.len() as f64
    };

    let mut max_frame_sq = 0.0_f64;
    let mut offset = 0usize;
    while offset < samples.len() {
        let end = (offset + frame).min(samples.len());
        let rms_sq = frame_rms_sq(&samples[offset..end]);
        if rms_sq > max_frame_sq {
            max_frame_sq = rms_sq;
        }
        offset = end;
    }
    if max_frame_sq <= max_threshold_sq {
        return samples;
    }

    let effective_threshold_sq = max_threshold_sq
        .min(max_frame_sq * SILENCE_TRIM_RELATIVE_RATIO * SILENCE_TRIM_RELATIVE_RATIO);

    let mut first_active = 0usize;
    offset = 0;
    while offset < samples.len() {
        let end = (offset + frame).min(samples.len());
        if frame_rms_sq(&samples[offset..end]) > effective_threshold_sq {
            first_active = offset;
            break;
        }
        offset = end;
    }

    let mut last_active = samples.len();
    let mut end = samples.len();
    while end > first_active {
        let start = end.saturating_sub(frame).max(first_active);
        if frame_rms_sq(&samples[start..end]) > effective_threshold_sq {
            last_active = end;
            break;
        }
        end = start;
    }

    let pad = (sr * SILENCE_TRIM_PADDING_MS) / 1000;
    let trimmed_start = first_active.saturating_sub(pad);
    let trimmed_end = last_active.saturating_add(pad).min(samples.len());
    &samples[trimmed_start..trimmed_end]
}

/// Split mono `f32` audio into pieces of at most [`NEMO_SEGMENT_SECONDS`], cutting each long piece
/// at the midpoint of the quietest 100 ms frame in its last 5 seconds.
pub fn split_nemo_segments(samples: &[f32], sample_rate: u32) -> Vec<&[f32]> {
    split_audio_segments(samples, sample_rate, NEMO_SEGMENT_SECONDS)
}

/// Split mono `f32` audio into pieces of at most `max_segment_seconds`, cutting each long piece at
/// the midpoint of the quietest 100 ms frame in its last 5 seconds.
///
/// Returns an empty vector when `samples` is empty, `sample_rate == 0`, or
/// `max_segment_seconds == 0`.
pub fn split_audio_segments(
    samples: &[f32],
    sample_rate: u32,
    max_segment_seconds: usize,
) -> Vec<&[f32]> {
    if samples.is_empty() || sample_rate == 0 || max_segment_seconds == 0 {
        return Vec::new();
    }
    let sr = sample_rate as usize;
    let limit = max_segment_seconds.saturating_mul(sr);
    let search = NEMO_CUT_SEARCH_SECONDS.saturating_mul(sr);
    let frame = (sr / 10).max(1);
    if samples.len() <= limit {
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
    fn format_qwen3_hotwords_strips_commas_deduplicates_and_caps_budget() {
        assert_eq!(format_qwen3_hotwords(&[]), None);
        let terms = vec![
            " Pulse\0Scribe ".to_owned(),
            "pulsescribe".to_owned(),
            "TATEX, GmbH".to_owned(),
            "Kliebhan".to_owned(),
        ];
        assert_eq!(
            format_qwen3_hotwords(&terms).as_deref(),
            Some("PulseScribe,TATEX  GmbH,Kliebhan")
        );

        // Single oversized keyword must not bypass the character cap.
        let oversized = "x".repeat(QWEN3_HOTWORD_MAX_CHARS + 50);
        assert_eq!(
            format_qwen3_hotwords(std::slice::from_ref(&oversized)),
            None
        );
        assert_eq!(
            format_qwen3_hotwords(&[oversized, "PulseScribe".to_owned()]).as_deref(),
            Some("PulseScribe")
        );

        // Long terms exercise the character budget.
        let many_long: Vec<String> = (0..40).map(|i| format!("Fachbegriff{i:02}")).collect();
        let long_hotwords = format_qwen3_hotwords(&many_long).unwrap();
        assert!(long_hotwords.chars().count() <= QWEN3_HOTWORD_MAX_CHARS);
        assert!(long_hotwords.split(',').count() < QWEN3_HOTWORD_LIMIT);

        // Short terms stay well within the character budget and exercise the item-count cap.
        let many_short: Vec<String> = (0..30).map(|i| format!("k{i:02}")).collect();
        let short_hotwords = format_qwen3_hotwords(&many_short).unwrap();
        assert_eq!(short_hotwords.split(',').count(), QWEN3_HOTWORD_LIMIT);
        assert!(short_hotwords.chars().count() <= QWEN3_HOTWORD_MAX_CHARS);
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

        // Short audio stays intact; zero limit returns empty.
        let short = vec![0.1_f32; 5 * sr];
        assert_eq!(split_nemo_segments(&short, sr as u32).len(), 1);
        assert!(split_nemo_segments(&[], sr as u32).is_empty());
        assert!(split_audio_segments(&short, sr as u32, 0).is_empty());

        // Transducer limit (25 s) keeps an 18 s utterance intact and splits >25 s audio at the
        // quietest 100 ms gap inside [20s..25s].
        let transducer_pieces = split_audio_segments(&audio, sr as u32, TRANSDUCER_SEGMENT_SECONDS);
        assert_eq!(transducer_pieces.len(), 1);
        assert_eq!(transducer_pieces[0].len(), audio.len());

        let mut long_audio = vec![0.5_f32; 30 * sr];
        for s in &mut long_audio[22 * sr..(22 * sr + sr / 5)] {
            *s = 0.0;
        }
        let long_pieces = split_audio_segments(&long_audio, sr as u32, TRANSDUCER_SEGMENT_SECONDS);
        assert_eq!(long_pieces.len(), 2);
        assert!(long_pieces[0].len() >= 22 * sr && long_pieces[0].len() <= 22 * sr + sr / 5);
        assert!(long_pieces
            .iter()
            .all(|p| p.len() <= TRANSDUCER_SEGMENT_SECONDS * sr));
        assert_eq!(
            long_pieces[0].len() + long_pieces[1].len(),
            long_audio.len()
        );
    }

    #[test]
    fn trim_leading_trailing_silence_keeps_padding_and_preserves_quiet_or_noisy_audio() {
        let sr = 16_000usize;
        let pad = (sr * SILENCE_TRIM_PADDING_MS) / 1000;
        assert!(trim_leading_trailing_silence(&[], sr as u32).is_empty());
        assert_eq!(trim_leading_trailing_silence(&[0.5; 100], 0).len(), 100);

        // All-quiet audio must not be emptied.
        let quiet = vec![0.0005_f32; sr];
        assert_eq!(
            trim_leading_trailing_silence(&quiet, sr as u32),
            quiet.as_slice()
        );

        // 1.0 s silence + 0.5 s speech + 1.0 s silence -> exact speech window plus 250 ms pad each side.
        let mut padded = vec![0.0_f32; sr];
        padded.extend(std::iter::repeat_n(0.1_f32, sr / 2));
        padded.extend(std::iter::repeat_n(0.0_f32, sr));
        let expected_start = sr - pad;
        let expected_end = sr + sr / 2 + pad;
        let trimmed = trim_leading_trailing_silence(&padded, sr as u32);
        assert_eq!(trimmed, &padded[expected_start..expected_end]);

        // Quiet speech (0.001 RMS) both before and after loud speech (0.1 RMS) must be retained,
        // even when separated by >250 ms from the loud center.
        let mut mixed = vec![0.0_f32; sr];
        mixed.extend(std::iter::repeat_n(0.001_f32, sr));
        mixed.extend(std::iter::repeat_n(0.1_f32, sr / 2));
        mixed.extend(std::iter::repeat_n(0.001_f32, sr));
        mixed.extend(std::iter::repeat_n(0.0_f32, sr));
        let trimmed_mixed = trim_leading_trailing_silence(&mixed, sr as u32);
        assert_eq!(trimmed_mixed, &mixed[(sr - pad)..(3 * sr + sr / 2 + pad)]);

        // Soft speech onset (0.0004 RMS) on a very quiet recording (0.004 RMS body) stays intact
        // thanks to the adaptive threshold (5% of 0.004 = 0.0002 < 0.0004).
        let mut quiet_onset = vec![0.0_f32; sr];
        quiet_onset.extend(std::iter::repeat_n(0.0004_f32, sr / 2));
        quiet_onset.extend(std::iter::repeat_n(0.004_f32, sr / 2));
        quiet_onset.extend(std::iter::repeat_n(0.0_f32, sr));
        let trimmed_quiet = trim_leading_trailing_silence(&quiet_onset, sr as u32);
        assert_eq!(trimmed_quiet, &quiet_onset[(sr - pad)..(2 * sr + pad)]);

        // Non-finite samples inside silent regions must not prevent trimming.
        let mut with_nan = vec![0.0_f32; sr];
        with_nan[100] = f32::NAN;
        with_nan.extend(std::iter::repeat_n(0.1_f32, sr / 2));
        with_nan.extend(std::iter::repeat_n(0.0_f32, sr));
        let trimmed_nan = trim_leading_trailing_silence(&with_nan, sr as u32);
        assert_eq!(trimmed_nan, &with_nan[expected_start..expected_end]);

        // Continuously active (noisy) audio stays untouched.
        let noisy = vec![0.05_f32; sr];
        assert_eq!(
            trim_leading_trailing_silence(&noisy, sr as u32),
            noisy.as_slice()
        );
    }
}
