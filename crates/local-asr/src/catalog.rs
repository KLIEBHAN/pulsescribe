//! Curated local ASR model catalog matching ADR-003 and Python's `providers/nemo_model.py`
//! / `providers/streaming_model.py`.

use std::path::{Component, Path};

use serde::{Deserialize, Serialize};
use url::Url;

use crate::LocalAsrError;

/// Default Hugging Face Hub base URL for direct model file downloads.
pub const DEFAULT_HF_BASE_URL: &str = "https://huggingface.co";

/// Default local model matching `config.py::DEFAULT_LOCAL_MODEL` (`"turbo"` -> `"large-v3-turbo"`).
pub const DEFAULT_LOCAL_MODEL_ID: &str = "large-v3-turbo";

/// Curated local streaming preview model identifier (Nemotron 3.5 int8 via `sherpa-onnx` CPU).
pub const STREAMING_PREVIEW_MODEL_ID: &str = "nemotron-streaming";

/// The 25 European languages supported by NVIDIA Canary 1B v2 (`providers/nemo_model.py`).
pub const CANARY_LANGUAGES: [&str; 25] = [
    "bg", "cs", "da", "de", "el", "en", "es", "et", "fi", "fr", "hr", "hu", "it", "lt", "lv", "mt",
    "nl", "pl", "pt", "ro", "ru", "sk", "sl", "sv", "uk",
];

/// The 14 languages supported by Cohere Transcribe (`cohere-transcribe-03-2026`).
pub const COHERE_LANGUAGES: [&str; 14] = [
    "ar", "de", "el", "en", "es", "fr", "it", "ja", "ko", "nl", "pl", "pt", "vi", "zh",
];

fn is_supported_language(lang: &str, supported: &[&str]) -> bool {
    let base = lang
        .trim()
        .split(['-', '_'])
        .next()
        .unwrap_or("")
        .to_ascii_lowercase();
    supported.contains(&base.as_str())
}

/// Returns `true` when `lang` is one of the 25 languages trained into Canary 1B v2.
pub fn is_canary_language(lang: &str) -> bool {
    is_supported_language(lang, &CANARY_LANGUAGES)
}

/// Returns `true` when `lang` is one of the 14 languages supported by Cohere Transcribe.
pub fn is_cohere_language(lang: &str) -> bool {
    is_supported_language(lang, &COHERE_LANGUAGES)
}

/// Resolve the effective 2-letter language code for NVIDIA Canary 1B v2, matching
/// `providers/nemo_model.py::canary_language()`.
///
/// Uses `language` if it maps to one of [`CANARY_LANGUAGES`]; otherwise checks the system locale
/// environment (`LC_ALL`, `LC_MESSAGES`, `LANG`) and finally falls back to `"en"`.
pub fn resolve_canary_language(language: Option<&str>) -> String {
    resolve_supported_language_with_system(
        language,
        detect_system_locale_language().as_deref(),
        &CANARY_LANGUAGES,
    )
}

/// Resolve the effective 2-letter language code for Cohere Transcribe.
///
/// Cohere Transcribe requires an explicit language tag from [`COHERE_LANGUAGES`]; unsupported or
/// `"auto"` values fall back to the system locale when supported, otherwise `"en"`.
pub fn resolve_cohere_language(language: Option<&str>) -> String {
    resolve_supported_language_with_system(
        language,
        detect_system_locale_language().as_deref(),
        &COHERE_LANGUAGES,
    )
}

/// Map a normalized ISO language tag (e.g. `"de"`, `"de-DE"`, `"en_US"`) to the full capitalized
/// English language name expected by `sherpa-onnx`'s Qwen3-ASR stream `"language"` option.
///
/// Returns `None` for `None`, `"auto"`, or unmapped tags so Qwen3-ASR auto-detects the language.
pub fn resolve_qwen3_language(language: Option<&str>) -> Option<&'static str> {
    let code = crate::normalize_language(language)?;
    match code.as_str() {
        "ar" => Some("Arabic"),
        "cs" => Some("Czech"),
        "da" => Some("Danish"),
        "de" => Some("German"),
        "el" => Some("Greek"),
        "en" => Some("English"),
        "es" => Some("Spanish"),
        "fa" => Some("Persian"),
        "fi" => Some("Finnish"),
        "fil" | "tl" => Some("Filipino"),
        "fr" => Some("French"),
        "hi" => Some("Hindi"),
        "hu" => Some("Hungarian"),
        "id" => Some("Indonesian"),
        "it" => Some("Italian"),
        "ja" => Some("Japanese"),
        "ko" => Some("Korean"),
        "mk" => Some("Macedonian"),
        "ms" => Some("Malay"),
        "nl" => Some("Dutch"),
        "pl" => Some("Polish"),
        "pt" => Some("Portuguese"),
        "ro" => Some("Romanian"),
        "ru" => Some("Russian"),
        "sv" => Some("Swedish"),
        "th" => Some("Thai"),
        "tr" => Some("Turkish"),
        "vi" => Some("Vietnamese"),
        "yue" => Some("Cantonese"),
        "zh" => Some("Chinese"),
        _ => None,
    }
}

fn resolve_supported_language_with_system(
    language: Option<&str>,
    system_language: Option<&str>,
    supported: &[&str],
) -> String {
    if let Some(code) =
        crate::normalize_language(language).filter(|l| is_supported_language(l, supported))
    {
        return code;
    }
    if let Some(sys) =
        crate::normalize_language(system_language).filter(|l| is_supported_language(l, supported))
    {
        return sys;
    }
    "en".to_owned()
}

fn detect_system_locale_language() -> Option<String> {
    for key in ["LC_ALL", "LC_MESSAGES", "LANG"] {
        let Some(raw) = std::env::var(key).ok() else {
            continue;
        };
        let cleaned = raw.split('.').next().unwrap_or("").trim();
        if cleaned.is_empty()
            || cleaned.eq_ignore_ascii_case("C")
            || cleaned.eq_ignore_ascii_case("POSIX")
        {
            continue;
        }
        if let Some(code) = crate::normalize_language(Some(cleaned)) {
            return Some(code);
        }
    }
    None
}

/// Native inference runtime family for a catalog model.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LocalEngineKind {
    /// GGML Whisper model executed via `whisper-rs` (Metal on macOS, CPU on Windows).
    Whisper,
    /// Offline NeMo Parakeet TDT model executed on Apple Silicon CoreML / Neural Engine via FluidAudio.
    CoreMlParakeet,
    /// Offline NeMo RNN-T / TDT model (e.g. Parakeet v3 int8) executed via `sherpa-onnx` CPU.
    SherpaOfflineTransducer,
    /// Offline NeMo Canary encoder-decoder model executed via `sherpa-onnx` CPU.
    SherpaOfflineCanary,
    /// Offline Cohere Transcribe encoder-decoder model executed via `sherpa-onnx` CPU.
    SherpaOfflineCohere,
    /// Offline Qwen3-ASR encoder-decoder model with per-stream hotword biasing via `sherpa-onnx` CPU.
    SherpaOfflineQwen3Asr,
    /// Online streaming NeMo transducer (Nemotron 3.5) executed via `sherpa-onnx` CPU.
    SherpaOnlineTransducer,
}

impl LocalEngineKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Whisper => "whisper",
            Self::CoreMlParakeet => "coreml_parakeet",
            Self::SherpaOfflineTransducer => "sherpa_offline_transducer",
            Self::SherpaOfflineCanary => "sherpa_offline_canary",
            Self::SherpaOfflineCohere => "sherpa_offline_cohere",
            Self::SherpaOfflineQwen3Asr => "sherpa_offline_qwen3_asr",
            Self::SherpaOnlineTransducer => "sherpa_online_transducer",
        }
    }

    pub const fn is_streaming_preview(self) -> bool {
        matches!(self, Self::SherpaOnlineTransducer)
    }
}

/// Immutable specification of a downloadable local ASR model.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ModelSpec {
    pub id: &'static str,
    pub display_name: &'static str,
    pub engine: LocalEngineKind,
    pub hf_repo: &'static str,
    /// Pinned commit SHA for third-party exports; `"main"` for official `ggerganov/whisper.cpp`.
    pub hf_revision: &'static str,
    /// Optional direct HTTPS base URL (e.g. a pinned GitHub Release download directory) used
    /// instead of Hugging Face when downloading via the default base URL.
    pub direct_base_url: Option<&'static str>,
    /// When `true`, managed cache entries without a matching `.pulsescribe-revision` marker are
    /// treated as stale so updated model weights automatically replace older installs.
    pub requires_revision_marker: bool,
    pub files: &'static [&'static str],
    pub approx_size_mb: u32,
    pub aliases: &'static [&'static str],
    /// Whether this Whisper model uses fixed 30s positional embeddings and MUST NOT shorten `audio_ctx`
    /// (ADR-003 Finding #2).
    pub requires_full_audio_ctx: bool,
}

impl ModelSpec {
    /// Stable revision identifier persisted in `.pulsescribe-revision` inside the managed cache.
    pub fn revision_key(&self) -> String {
        match self.direct_base_url {
            Some(url) => format!("direct:{}", url.trim_end_matches('/')),
            None => format!("hf:{}@{}", self.hf_repo, self.hf_revision),
        }
    }
}

const WHISPER_GGML_REPO: &str = "ggerganov/whisper.cpp";
#[cfg(not(all(target_os = "macos", target_arch = "aarch64", feature = "local-coreml")))]
const PARAKEET_ULTRA_PR4179_BASE_URL: &str =
    "https://github.com/KLIEBHAN/pulsescribe/releases/download/model-parakeet-ultra-pr4179";

#[cfg(all(target_os = "macos", target_arch = "aarch64", feature = "local-coreml"))]
const PARAKEET_ULTRA_COREML_FILES: &[&str] = &[
    "Preprocessor.mlmodelc/analytics/coremldata.bin",
    "Preprocessor.mlmodelc/coremldata.bin",
    "Preprocessor.mlmodelc/metadata.json",
    "Preprocessor.mlmodelc/model.mil",
    "Preprocessor.mlmodelc/weights/weight.bin",
    "Encoder.mlmodelc/analytics/coremldata.bin",
    "Encoder.mlmodelc/coremldata.bin",
    "Encoder.mlmodelc/model.mil",
    "Encoder.mlmodelc/weights/weight.bin",
    "Decoder.mlmodelc/analytics/coremldata.bin",
    "Decoder.mlmodelc/coremldata.bin",
    "Decoder.mlmodelc/model.mil",
    "Decoder.mlmodelc/weights/weight.bin",
    "JointDecisionv3.mlmodelc/analytics/coremldata.bin",
    "JointDecisionv3.mlmodelc/coremldata.bin",
    "JointDecisionv3.mlmodelc/model.mil",
    "JointDecisionv3.mlmodelc/weights/weight.bin",
    "parakeet_vocab.json",
];

#[cfg(all(target_os = "macos", target_arch = "aarch64", feature = "local-coreml"))]
const PARAKEET_V3_COREML_FILES: &[&str] = &[
    "Preprocessor.mlmodelc/analytics/coremldata.bin",
    "Preprocessor.mlmodelc/coremldata.bin",
    "Preprocessor.mlmodelc/metadata.json",
    "Preprocessor.mlmodelc/model.mil",
    "Preprocessor.mlmodelc/weights/weight.bin",
    "Encoder_v2.mlmodelc/analytics/coremldata.bin",
    "Encoder_v2.mlmodelc/coremldata.bin",
    "Encoder_v2.mlmodelc/model.mil",
    "Encoder_v2.mlmodelc/weights/weight.bin",
    "Decoder.mlmodelc/analytics/coremldata.bin",
    "Decoder.mlmodelc/coremldata.bin",
    "Decoder.mlmodelc/metadata.json",
    "Decoder.mlmodelc/model.mil",
    "Decoder.mlmodelc/weights/weight.bin",
    "JointDecisionv3.mlmodelc/analytics/coremldata.bin",
    "JointDecisionv3.mlmodelc/coremldata.bin",
    "JointDecisionv3.mlmodelc/metadata.json",
    "JointDecisionv3.mlmodelc/model.mil",
    "JointDecisionv3.mlmodelc/weights/weight.bin",
    "parakeet_vocab.json",
];

const CATALOG: &[ModelSpec] = &[
    ModelSpec {
        id: "large-v3-turbo",
        display_name: "Whisper Large v3 Turbo (Q8_0)",
        engine: LocalEngineKind::Whisper,
        hf_repo: WHISPER_GGML_REPO,
        hf_revision: "main",
        direct_base_url: None,
        requires_revision_marker: false,
        files: &["ggml-large-v3-turbo-q8_0.bin"],
        approx_size_mb: 874,
        aliases: &["turbo", "whisper-large-v3-turbo", "large-v3-turbo-q8_0"],
        requires_full_audio_ctx: true,
    },
    ModelSpec {
        id: "large-v3-turbo-q5_0",
        display_name: "Whisper Large v3 Turbo (Q5_0, kompakt)",
        engine: LocalEngineKind::Whisper,
        hf_repo: WHISPER_GGML_REPO,
        hf_revision: "main",
        direct_base_url: None,
        requires_revision_marker: false,
        files: &["ggml-large-v3-turbo-q5_0.bin"],
        approx_size_mb: 574,
        aliases: &["turbo-q5", "turbo-q5_0"],
        requires_full_audio_ctx: true,
    },
    ModelSpec {
        id: "large-v3-turbo-german",
        display_name: "Whisper Large v3 Turbo German (Q5_0, primeline)",
        engine: LocalEngineKind::Whisper,
        hf_repo: "cstr/whisper-large-v3-turbo-german-ggml",
        hf_revision: "d9431a78952938533dc46e75395a9b0b7237fa69",
        direct_base_url: None,
        requires_revision_marker: false,
        files: &["ggml-model-q5_0.bin"],
        approx_size_mb: 574,
        aliases: &["turbo-de", "turbo-german", "whisper-large-v3-turbo-german"],
        requires_full_audio_ctx: true,
    },
    ModelSpec {
        id: "large-v3",
        display_name: "Whisper Large v3 (Q5_0)",
        engine: LocalEngineKind::Whisper,
        hf_repo: WHISPER_GGML_REPO,
        hf_revision: "main",
        direct_base_url: None,
        requires_revision_marker: false,
        files: &["ggml-large-v3-q5_0.bin"],
        approx_size_mb: 1080,
        aliases: &["large", "whisper-large-v3"],
        requires_full_audio_ctx: false,
    },
    ModelSpec {
        id: "medium",
        display_name: "Whisper Medium",
        engine: LocalEngineKind::Whisper,
        hf_repo: WHISPER_GGML_REPO,
        hf_revision: "main",
        direct_base_url: None,
        requires_revision_marker: false,
        files: &["ggml-medium.bin"],
        approx_size_mb: 1530,
        aliases: &["whisper-medium"],
        requires_full_audio_ctx: false,
    },
    ModelSpec {
        id: "small",
        display_name: "Whisper Small",
        engine: LocalEngineKind::Whisper,
        hf_repo: WHISPER_GGML_REPO,
        hf_revision: "main",
        direct_base_url: None,
        requires_revision_marker: false,
        files: &["ggml-small.bin"],
        approx_size_mb: 466,
        aliases: &["whisper-small"],
        requires_full_audio_ctx: false,
    },
    ModelSpec {
        id: "base",
        display_name: "Whisper Base",
        engine: LocalEngineKind::Whisper,
        hf_repo: WHISPER_GGML_REPO,
        hf_revision: "main",
        direct_base_url: None,
        requires_revision_marker: false,
        files: &["ggml-base.bin"],
        approx_size_mb: 142,
        aliases: &["whisper-base"],
        requires_full_audio_ctx: false,
    },
    ModelSpec {
        id: "tiny",
        display_name: "Whisper Tiny",
        engine: LocalEngineKind::Whisper,
        hf_repo: WHISPER_GGML_REPO,
        hf_revision: "main",
        direct_base_url: None,
        requires_revision_marker: false,
        files: &["ggml-tiny.bin"],
        approx_size_mb: 75,
        aliases: &["whisper-tiny"],
        requires_full_audio_ctx: false,
    },
    #[cfg(all(target_os = "macos", target_arch = "aarch64", feature = "local-coreml"))]
    ModelSpec {
        id: "parakeet-ultra",
        display_name: "Parakeet Ultra 0.6B (CoreML, 25 EU-Sprachen, ANE)",
        engine: LocalEngineKind::CoreMlParakeet,
        hf_repo: "FluidInference/parakeet-ultra-coreml",
        hf_revision: "95eaa59a39d4394f047a4dc5cce480388a60d1b6",
        direct_base_url: None,
        requires_revision_marker: true,
        files: PARAKEET_ULTRA_COREML_FILES,
        approx_size_mb: 632,
        aliases: &["parakeet", "parakeet-tdt-0.6b-ultra", "parakeet-ultra-int8"],
        requires_full_audio_ctx: false,
    },
    #[cfg(not(all(target_os = "macos", target_arch = "aarch64", feature = "local-coreml")))]
    ModelSpec {
        id: "parakeet-ultra",
        display_name: "Parakeet Ultra 0.6B (int8, 25 EU-Sprachen, CPU)",
        engine: LocalEngineKind::SherpaOfflineTransducer,
        hf_repo: "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-ultra-int8",
        hf_revision: "model-parakeet-ultra-pr4179",
        direct_base_url: Some(PARAKEET_ULTRA_PR4179_BASE_URL),
        requires_revision_marker: true,
        files: &[
            "encoder.int8.onnx",
            "decoder.int8.onnx",
            "joiner.int8.onnx",
            "tokens.txt",
        ],
        approx_size_mb: 672,
        aliases: &["parakeet", "parakeet-tdt-0.6b-ultra", "parakeet-ultra-int8"],
        requires_full_audio_ctx: false,
    },
    #[cfg(all(target_os = "macos", target_arch = "aarch64", feature = "local-coreml"))]
    ModelSpec {
        id: "parakeet-v3",
        display_name: "Parakeet TDT 0.6B v3 (CoreML, ANE)",
        engine: LocalEngineKind::CoreMlParakeet,
        hf_repo: "FluidInference/parakeet-tdt-0.6b-v3-coreml",
        hf_revision: "7dd20fe6b1797d35f5e3307e8b1732d9a178edfe",
        direct_base_url: None,
        requires_revision_marker: true,
        files: PARAKEET_V3_COREML_FILES,
        approx_size_mb: 633,
        aliases: &["parakeet-tdt-0.6b-v3"],
        requires_full_audio_ctx: false,
    },
    #[cfg(not(all(target_os = "macos", target_arch = "aarch64", feature = "local-coreml")))]
    ModelSpec {
        id: "parakeet-v3",
        display_name: "Parakeet TDT 0.6B v3 (int8, CPU)",
        engine: LocalEngineKind::SherpaOfflineTransducer,
        hf_repo: "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
        hf_revision: "ed5d745623472f97cfc2e6c2916ee248798fc5a8",
        direct_base_url: None,
        requires_revision_marker: false,
        files: &[
            "encoder.int8.onnx",
            "decoder.int8.onnx",
            "joiner.int8.onnx",
            "tokens.txt",
        ],
        approx_size_mb: 670,
        aliases: &["parakeet-tdt-0.6b-v3"],
        requires_full_audio_ctx: false,
    },
    ModelSpec {
        id: "canary-1b-v2",
        display_name: "Canary 1B v2 (int8, 25 EU-Sprachen, CPU)",
        engine: LocalEngineKind::SherpaOfflineCanary,
        hf_repo: "YaroslavGor/sherpa-onnx-nemo-canary-1b-v2-int",
        hf_revision: "8e135081466c7cfe955d1b858544e4bdeeffeb9e",
        direct_base_url: None,
        requires_revision_marker: false,
        files: &["encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt"],
        approx_size_mb: 1020,
        aliases: &["canary", "canary-1b"],
        requires_full_audio_ctx: false,
    },
    ModelSpec {
        id: "cohere-transcribe",
        display_name: "Cohere Transcribe 2B (int8, 14 Sprachen, CPU)",
        engine: LocalEngineKind::SherpaOfflineCohere,
        hf_repo: "csukuangfj2/sherpa-onnx-cohere-transcribe-14-lang-int8-2026-04-01",
        hf_revision: "156a470cf08eefe706a0004f3c52d9ee567ca7a0",
        direct_base_url: None,
        requires_revision_marker: false,
        files: &[
            "encoder.int8.onnx",
            "encoder.int8.onnx.data",
            "decoder.int8.onnx",
            "tokens.txt",
        ],
        approx_size_mb: 2888,
        aliases: &["cohere", "cohere-transcribe-2b"],
        requires_full_audio_ctx: false,
    },
    ModelSpec {
        id: "qwen3-asr-0.6b",
        display_name: "Qwen3-ASR 0.6B (int8, 30 Sprachen + Vokabular, CPU)",
        engine: LocalEngineKind::SherpaOfflineQwen3Asr,
        hf_repo: "csukuangfj2/sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25",
        hf_revision: "68818b2313fe77bd06f6a7c5068ff3ef59d02b8a",
        direct_base_url: None,
        requires_revision_marker: false,
        files: &[
            "conv_frontend.onnx",
            "encoder.int8.onnx",
            "decoder.int8.onnx",
            "tokenizer/merges.txt",
            "tokenizer/tokenizer_config.json",
            "tokenizer/vocab.json",
        ],
        approx_size_mb: 987,
        aliases: &["qwen3-asr", "qwen3"],
        requires_full_audio_ctx: false,
    },
    ModelSpec {
        id: STREAMING_PREVIEW_MODEL_ID,
        display_name: "Nemotron 3.5 Streaming Preview (560 ms, int8, CPU)",
        engine: LocalEngineKind::SherpaOnlineTransducer,
        hf_repo: "csukuangfj2/sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11",
        hf_revision: "ab43d895f5985b1bbab8b6eac8607fcdc05343f3",
        direct_base_url: None,
        requires_revision_marker: false,
        files: &[
            "encoder.int8.onnx",
            "decoder.int8.onnx",
            "joiner.int8.onnx",
            "tokens.txt",
        ],
        approx_size_mb: 682,
        aliases: &["nemotron", "live-preview"],
        requires_full_audio_ctx: false,
    },
];

/// Return all curated local models supported by PulseScribe.
pub const fn catalog_models() -> &'static [ModelSpec] {
    CATALOG
}

/// Resolve a model name or alias (`None` / blank defaults to [`DEFAULT_LOCAL_MODEL_ID`]).
pub fn resolve_model_spec(name: Option<&str>) -> Option<&'static ModelSpec> {
    let query = name
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or(DEFAULT_LOCAL_MODEL_ID);

    CATALOG.iter().find(|spec| {
        spec.id.eq_ignore_ascii_case(query)
            || spec
                .aliases
                .iter()
                .any(|alias| alias.eq_ignore_ascii_case(query))
    })
}

/// Validate that a model identifier, revision, or filename is a single plain path component
/// without traversal, separators, drive prefixes (`C:`), or NUL bytes.
pub(crate) fn validate_safe_filename(filename: &str) -> Result<(), LocalAsrError> {
    if filename.is_empty()
        || filename == "."
        || filename == ".."
        || filename.contains('/')
        || filename.contains('\\')
        || filename.contains(':')
        || filename.contains('\0')
    {
        return Err(LocalAsrError::InvalidModel(format!(
            "unsafe model path component '{filename}'"
        )));
    }
    let mut components = Path::new(filename).components();
    match (components.next(), components.next()) {
        (Some(Component::Normal(_)), None) => Ok(()),
        _ => Err(LocalAsrError::InvalidModel(format!(
            "unsafe model path component '{filename}'"
        ))),
    }
}

/// Validate a relative model file path that may contain nested `/` subdirectories (e.g.
/// `"tokenizer/vocab.json"`), ensuring every `/`-separated segment is a safe plain path component.
pub(crate) fn validate_safe_rel_path(rel_path: &str) -> Result<(), LocalAsrError> {
    if rel_path.is_empty()
        || rel_path.starts_with('/')
        || rel_path.ends_with('/')
        || rel_path.contains('\\')
        || rel_path.contains(':')
        || rel_path.contains('\0')
    {
        return Err(LocalAsrError::InvalidModel(format!(
            "unsafe model file path '{rel_path}'"
        )));
    }
    for segment in rel_path.split('/') {
        validate_safe_filename(segment)?;
    }
    Ok(())
}

/// Validate a Hugging Face `org/repo` identifier so every slash-separated segment is a safe
/// single path component.
pub(crate) fn validate_hf_repo(hf_repo: &str) -> Result<(), LocalAsrError> {
    let mut count = 0usize;
    for part in hf_repo.split('/') {
        validate_safe_filename(part)?;
        count += 1;
    }
    if count == 0 {
        return Err(LocalAsrError::InvalidModel(format!(
            "invalid HF repo '{hf_repo}'"
        )));
    }
    Ok(())
}

/// Build the direct Hugging Face file download URL:
/// `{base_url}/{org}/{repo}/resolve/{revision}/{filename}`.
pub fn build_hf_resolve_url(
    base_url: &str,
    spec: &ModelSpec,
    filename: &str,
) -> Result<Url, LocalAsrError> {
    validate_hf_repo(spec.hf_repo)?;
    validate_safe_filename(spec.hf_revision)?;
    validate_safe_rel_path(filename)?;
    let mut url = Url::parse(base_url.trim())
        .map_err(|_| LocalAsrError::InvalidModel("invalid HF base URL".to_owned()))?;
    if !matches!(url.scheme(), "http" | "https") {
        return Err(LocalAsrError::InvalidModel(
            "HF base URL must use http or https".to_owned(),
        ));
    }
    {
        let mut segments = url.path_segments_mut().map_err(|()| {
            LocalAsrError::InvalidModel("HF base URL cannot have path segments".to_owned())
        })?;
        segments.pop_if_empty();
        for part in spec.hf_repo.split('/') {
            segments.push(part);
        }
        segments.push("resolve");
        segments.push(spec.hf_revision);
        for segment in filename.split('/') {
            segments.push(segment);
        }
    }
    Ok(url)
}

/// Build the effective download URL for `filename`:
/// - Uses `{spec.direct_base_url}/{filename}` when the model specifies a direct release mirror and
///   `hf_base_url` is the default Hugging Face base URL.
/// - Otherwise falls back to [`build_hf_resolve_url`] (allowing custom mirrors and loopback test
///   servers to override all model downloads).
pub fn build_model_file_url(
    hf_base_url: &str,
    spec: &ModelSpec,
    filename: &str,
) -> Result<Url, LocalAsrError> {
    if let Some(direct_base) = spec.direct_base_url {
        if hf_base_url.trim().trim_end_matches('/') == DEFAULT_HF_BASE_URL {
            validate_safe_rel_path(filename)?;
            let mut url = Url::parse(direct_base.trim())
                .map_err(|_| LocalAsrError::InvalidModel("invalid direct base URL".to_owned()))?;
            if !matches!(url.scheme(), "http" | "https") {
                return Err(LocalAsrError::InvalidModel(
                    "direct base URL must use http or https".to_owned(),
                ));
            }
            {
                let mut segments = url.path_segments_mut().map_err(|()| {
                    LocalAsrError::InvalidModel(
                        "direct base URL cannot have path segments".to_owned(),
                    )
                })?;
                segments.pop_if_empty();
                for segment in filename.split('/') {
                    segments.push(segment);
                }
            }
            return Ok(url);
        }
    }
    build_hf_resolve_url(hf_base_url, spec, filename)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolves_default_aliases_and_flags_turbo_audio_ctx() {
        let default_spec = resolve_model_spec(None).unwrap();
        assert_eq!(default_spec.id, "large-v3-turbo");
        assert!(default_spec.requires_full_audio_ctx);

        assert_eq!(
            resolve_model_spec(Some("turbo")).unwrap().id,
            "large-v3-turbo"
        );
        let turbo_de = resolve_model_spec(Some("turbo-de")).unwrap();
        assert_eq!(turbo_de.id, "large-v3-turbo-german");
        assert!(turbo_de.requires_full_audio_ctx);
        assert_eq!(
            resolve_model_spec(Some("qwen3-asr")).unwrap().id,
            "qwen3-asr-0.6b"
        );
        assert_eq!(
            resolve_model_spec(Some("  PARAKEET ")).unwrap().id,
            "parakeet-ultra"
        );
        assert_eq!(
            resolve_model_spec(Some("parakeet-v3")).unwrap().id,
            "parakeet-v3"
        );
        assert_eq!(
            resolve_model_spec(Some("parakeet-ultra")).unwrap().id,
            "parakeet-ultra"
        );
        assert_eq!(
            resolve_model_spec(Some("parakeet-tdt-0.6b-ultra"))
                .unwrap()
                .id,
            "parakeet-ultra"
        );
        assert_eq!(
            resolve_model_spec(Some("canary")).unwrap().id,
            "canary-1b-v2"
        );
        assert_eq!(
            resolve_model_spec(Some("cohere")).unwrap().id,
            "cohere-transcribe"
        );
        assert_eq!(
            resolve_model_spec(Some("cohere-transcribe-2b")).unwrap().id,
            "cohere-transcribe"
        );
        assert_eq!(
            resolve_model_spec(Some("nemotron")).unwrap().id,
            "nemotron-streaming"
        );
        assert!(resolve_model_spec(Some("nonexistent-model")).is_none());
    }

    #[test]
    fn builds_valid_hf_resolve_urls_and_rejects_traversal() {
        let parakeet = resolve_model_spec(Some("parakeet-v3")).unwrap();
        let parakeet_ultra = resolve_model_spec(Some("parakeet-ultra")).unwrap();

        #[cfg(all(target_os = "macos", target_arch = "aarch64", feature = "local-coreml"))]
        {
            assert_eq!(parakeet.engine, LocalEngineKind::CoreMlParakeet);
            assert_eq!(parakeet_ultra.engine, LocalEngineKind::CoreMlParakeet);
            let url = build_hf_resolve_url(
                DEFAULT_HF_BASE_URL,
                parakeet,
                "Encoder_v2.mlmodelc/weights/weight.bin",
            )
            .unwrap();
            assert_eq!(
                url.as_str(),
                "https://huggingface.co/FluidInference/parakeet-tdt-0.6b-v3-coreml/resolve/7dd20fe6b1797d35f5e3307e8b1732d9a178edfe/Encoder_v2.mlmodelc/weights/weight.bin"
            );
            let ultra_url = build_model_file_url(
                DEFAULT_HF_BASE_URL,
                parakeet_ultra,
                "Encoder.mlmodelc/weights/weight.bin",
            )
            .unwrap();
            assert_eq!(
                ultra_url.as_str(),
                "https://huggingface.co/FluidInference/parakeet-ultra-coreml/resolve/95eaa59a39d4394f047a4dc5cce480388a60d1b6/Encoder.mlmodelc/weights/weight.bin"
            );
        }

        #[cfg(not(all(target_os = "macos", target_arch = "aarch64", feature = "local-coreml")))]
        {
            let url =
                build_hf_resolve_url(DEFAULT_HF_BASE_URL, parakeet, "encoder.int8.onnx").unwrap();
            assert_eq!(
                url.as_str(),
                "https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/resolve/ed5d745623472f97cfc2e6c2916ee248798fc5a8/encoder.int8.onnx"
            );
            let ultra_url =
                build_model_file_url(DEFAULT_HF_BASE_URL, parakeet_ultra, "encoder.int8.onnx")
                    .unwrap();
            assert_eq!(
                ultra_url.as_str(),
                "https://github.com/KLIEBHAN/pulsescribe/releases/download/model-parakeet-ultra-pr4179/encoder.int8.onnx"
            );
            // Custom base URLs (e.g. loopback mirrors in tests) still override direct release URLs.
            let mirror_url =
                build_model_file_url("http://127.0.0.1:8080", parakeet_ultra, "encoder.int8.onnx")
                    .unwrap();
            assert_eq!(
                mirror_url.as_str(),
                "http://127.0.0.1:8080/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-ultra-int8/resolve/model-parakeet-ultra-pr4179/encoder.int8.onnx"
            );
        }

        // Direct release mirror override always works regardless of active platform features.
        let mut direct_spec = *parakeet_ultra;
        direct_spec.direct_base_url =
            Some("https://github.com/KLIEBHAN/pulsescribe/releases/download/example-mirror");
        assert_eq!(
            build_model_file_url(DEFAULT_HF_BASE_URL, &direct_spec, "weights.bin")
                .unwrap()
                .as_str(),
            "https://github.com/KLIEBHAN/pulsescribe/releases/download/example-mirror/weights.bin"
        );
        assert!(
            build_model_file_url("http://127.0.0.1:8080", &direct_spec, "weights.bin")
                .unwrap()
                .as_str()
                .starts_with("http://127.0.0.1:8080/")
        );

        assert!(build_model_file_url(DEFAULT_HF_BASE_URL, parakeet_ultra, "../evil").is_err());
        let qwen3 = resolve_model_spec(Some("qwen3-asr-0.6b")).unwrap();
        let qwen3_url =
            build_hf_resolve_url(DEFAULT_HF_BASE_URL, qwen3, "tokenizer/vocab.json").unwrap();
        assert_eq!(
            qwen3_url.as_str(),
            "https://huggingface.co/csukuangfj2/sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25/resolve/68818b2313fe77bd06f6a7c5068ff3ef59d02b8a/tokenizer/vocab.json"
        );
        assert!(build_hf_resolve_url(DEFAULT_HF_BASE_URL, qwen3, "tokenizer/../evil").is_err());
        assert!(build_hf_resolve_url(DEFAULT_HF_BASE_URL, qwen3, "/leading/slash").is_err());
        assert!(build_hf_resolve_url(DEFAULT_HF_BASE_URL, qwen3, "trailing/slash/").is_err());
        assert!(build_hf_resolve_url(DEFAULT_HF_BASE_URL, qwen3, "empty//segment").is_err());
        assert!(build_hf_resolve_url(DEFAULT_HF_BASE_URL, parakeet, "../secret").is_err());
        assert!(build_hf_resolve_url(DEFAULT_HF_BASE_URL, parakeet, "C:evil.bin").is_err());
        assert!(build_hf_resolve_url("file:///etc/passwd", parakeet, "tokens.txt").is_err());

        let mut bad_rev = *parakeet;
        bad_rev.hf_revision = "..";
        assert!(build_hf_resolve_url(DEFAULT_HF_BASE_URL, &bad_rev, "tokens.txt").is_err());
    }

    #[test]
    fn canary_and_cohere_language_detection_handles_regional_tags_and_locale() {
        assert!(is_canary_language("de"));
        assert!(is_canary_language("de-DE"));
        assert!(is_canary_language("EN_us"));
        assert!(!is_canary_language("ja"));

        assert_eq!(
            resolve_supported_language_with_system(Some("de-DE"), Some("en"), &CANARY_LANGUAGES),
            "de"
        );
        assert_eq!(
            resolve_supported_language_with_system(Some("auto"), Some("fr_FR"), &CANARY_LANGUAGES),
            "fr"
        );
        assert_eq!(
            resolve_supported_language_with_system(None, Some("ja_JP"), &CANARY_LANGUAGES),
            "en"
        );
        assert_eq!(
            resolve_supported_language_with_system(Some("ja"), None, &CANARY_LANGUAGES),
            "en"
        );

        assert!(is_cohere_language("de-DE"));
        assert!(is_cohere_language("ja_JP"));
        assert!(!is_cohere_language("sv"));
        assert_eq!(
            resolve_supported_language_with_system(Some("ja-JP"), Some("de"), &COHERE_LANGUAGES),
            "ja"
        );
        assert_eq!(
            resolve_supported_language_with_system(Some("auto"), Some("de_DE"), &COHERE_LANGUAGES),
            "de"
        );
        assert_eq!(
            resolve_supported_language_with_system(Some("sv"), Some("ru"), &COHERE_LANGUAGES),
            "en"
        );

        assert_eq!(resolve_qwen3_language(Some("de-DE")), Some("German"));
        assert_eq!(resolve_qwen3_language(Some("en_US")), Some("English"));
        assert_eq!(resolve_qwen3_language(Some("auto")), None);
        assert_eq!(resolve_qwen3_language(Some("xx")), None);
    }
}
