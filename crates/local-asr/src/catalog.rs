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

/// Returns `true` when `lang` is one of the 25 languages trained into Canary 1B v2.
pub fn is_canary_language(lang: &str) -> bool {
    let base = lang
        .trim()
        .split(['-', '_'])
        .next()
        .unwrap_or("")
        .to_ascii_lowercase();
    CANARY_LANGUAGES.contains(&base.as_str())
}

/// Resolve the effective 2-letter language code for NVIDIA Canary 1B v2, matching
/// `providers/nemo_model.py::canary_language()`.
///
/// Uses `language` if it maps to one of [`CANARY_LANGUAGES`]; otherwise checks the system locale
/// environment (`LC_ALL`, `LC_MESSAGES`, `LANG`) and finally falls back to `"en"`.
pub fn resolve_canary_language(language: Option<&str>) -> String {
    resolve_canary_language_with_system(language, detect_system_locale_language().as_deref())
}

fn resolve_canary_language_with_system(
    language: Option<&str>,
    system_language: Option<&str>,
) -> String {
    if let Some(code) = crate::normalize_language(language).filter(|l| is_canary_language(l)) {
        return code;
    }
    if let Some(sys) = crate::normalize_language(system_language).filter(|l| is_canary_language(l))
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
    /// Offline NeMo RNN-T / TDT model (e.g. Parakeet v3 int8) executed via `sherpa-onnx` CPU.
    SherpaOfflineTransducer,
    /// Offline NeMo Canary encoder-decoder model executed via `sherpa-onnx` CPU.
    SherpaOfflineCanary,
    /// Online streaming NeMo transducer (Nemotron 3.5) executed via `sherpa-onnx` CPU.
    SherpaOnlineTransducer,
}

impl LocalEngineKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Whisper => "whisper",
            Self::SherpaOfflineTransducer => "sherpa_offline_transducer",
            Self::SherpaOfflineCanary => "sherpa_offline_canary",
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
    pub files: &'static [&'static str],
    pub approx_size_mb: u32,
    pub aliases: &'static [&'static str],
    /// Whether this Whisper model uses fixed 30s positional embeddings and MUST NOT shorten `audio_ctx`
    /// (ADR-003 Finding #2).
    pub requires_full_audio_ctx: bool,
}

const WHISPER_GGML_REPO: &str = "ggerganov/whisper.cpp";

const CATALOG: &[ModelSpec] = &[
    ModelSpec {
        id: "large-v3-turbo",
        display_name: "Whisper Large v3 Turbo (Q8_0)",
        engine: LocalEngineKind::Whisper,
        hf_repo: WHISPER_GGML_REPO,
        hf_revision: "main",
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
        files: &["ggml-large-v3-turbo-q5_0.bin"],
        approx_size_mb: 574,
        aliases: &["turbo-q5", "turbo-q5_0"],
        requires_full_audio_ctx: true,
    },
    ModelSpec {
        id: "large-v3",
        display_name: "Whisper Large v3 (Q5_0)",
        engine: LocalEngineKind::Whisper,
        hf_repo: WHISPER_GGML_REPO,
        hf_revision: "main",
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
        files: &["ggml-tiny.bin"],
        approx_size_mb: 75,
        aliases: &["whisper-tiny"],
        requires_full_audio_ctx: false,
    },
    ModelSpec {
        id: "parakeet-v3",
        display_name: "Parakeet TDT 0.6B v3 (int8, CPU)",
        engine: LocalEngineKind::SherpaOfflineTransducer,
        hf_repo: "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
        hf_revision: "ed5d745623472f97cfc2e6c2916ee248798fc5a8",
        files: &[
            "encoder.int8.onnx",
            "decoder.int8.onnx",
            "joiner.int8.onnx",
            "tokens.txt",
        ],
        approx_size_mb: 670,
        aliases: &["parakeet", "parakeet-tdt-0.6b-v3"],
        requires_full_audio_ctx: false,
    },
    ModelSpec {
        id: "canary-1b-v2",
        display_name: "Canary 1B v2 (int8, 25 EU-Sprachen, CPU)",
        engine: LocalEngineKind::SherpaOfflineCanary,
        hf_repo: "YaroslavGor/sherpa-onnx-nemo-canary-1b-v2-int",
        hf_revision: "8e135081466c7cfe955d1b858544e4bdeeffeb9e",
        files: &["encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt"],
        approx_size_mb: 1020,
        aliases: &["canary", "canary-1b"],
        requires_full_audio_ctx: false,
    },
    ModelSpec {
        id: STREAMING_PREVIEW_MODEL_ID,
        display_name: "Nemotron 3.5 Streaming Preview (560 ms, int8, CPU)",
        engine: LocalEngineKind::SherpaOnlineTransducer,
        hf_repo: "csukuangfj2/sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11",
        hf_revision: "ab43d895f5985b1bbab8b6eac8607fcdc05343f3",
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
    validate_safe_filename(filename)?;
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
        segments.push(filename);
    }
    Ok(url)
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
        assert_eq!(
            resolve_model_spec(Some("  PARAKEET ")).unwrap().id,
            "parakeet-v3"
        );
        assert_eq!(
            resolve_model_spec(Some("canary")).unwrap().id,
            "canary-1b-v2"
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
        let url = build_hf_resolve_url(DEFAULT_HF_BASE_URL, parakeet, "encoder.int8.onnx").unwrap();
        assert_eq!(
            url.as_str(),
            "https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/resolve/ed5d745623472f97cfc2e6c2916ee248798fc5a8/encoder.int8.onnx"
        );
        assert!(build_hf_resolve_url(DEFAULT_HF_BASE_URL, parakeet, "../secret").is_err());
        assert!(build_hf_resolve_url(DEFAULT_HF_BASE_URL, parakeet, "C:evil.bin").is_err());
        assert!(build_hf_resolve_url("file:///etc/passwd", parakeet, "tokens.txt").is_err());

        let mut bad_rev = *parakeet;
        bad_rev.hf_revision = "..";
        assert!(build_hf_resolve_url(DEFAULT_HF_BASE_URL, &bad_rev, "tokens.txt").is_err());
    }

    #[test]
    fn canary_language_detection_and_fallback_handles_regional_tags_and_locale() {
        assert!(is_canary_language("de"));
        assert!(is_canary_language("de-DE"));
        assert!(is_canary_language("EN_us"));
        assert!(!is_canary_language("ja"));

        assert_eq!(
            resolve_canary_language_with_system(Some("de-DE"), Some("en")),
            "de"
        );
        assert_eq!(
            resolve_canary_language_with_system(Some("auto"), Some("fr_FR")),
            "fr"
        );
        assert_eq!(
            resolve_canary_language_with_system(None, Some("ja_JP")),
            "en"
        );
        assert_eq!(resolve_canary_language_with_system(Some("ja"), None), "en");
    }
}
