//! Safe `sherpa-onnx` CPU engines (Parakeet v3, Canary 1B v2, Nemotron 3.5 Streaming Preview).
//!
//! Enforces ADR-003 Finding #3: always runs with `provider = "cpu"` (never CoreML EP, which was
//! 2.4x slower, used 3.1x RAM, and leaked CoreAnalytics contexts in Phase 0 benchmarks).

use std::collections::HashSet;
use std::ffi::CString;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::Ordering;
use std::sync::{Arc, Mutex};

use crate::catalog::{
    resolve_canary_language, resolve_cohere_language, resolve_qwen3_language, LocalEngineKind,
    ModelSpec,
};
use crate::sherpa_ffi::{
    path_to_cstring, OfflineRecognizerHandle, OnlineRecognizerHandle, OnlineStreamHandle,
    SherpaApi, SherpaOnnxOfflineRecognizerConfig, SherpaOnnxOnlineRecognizerConfig,
};
use crate::{
    format_qwen3_hotwords, normalize_language, split_audio_segments, trim_leading_trailing_silence,
    InstalledModel, LocalAsrEngine, LocalAsrError, LocalAsrRequest, LocalStreamingSession,
    LOCAL_ASR_SAMPLE_RATE, NEMO_SEGMENT_SECONDS, TRANSDUCER_SEGMENT_SECONDS,
};

pub use crate::sherpa_ffi::SHERPA_SUPPORTED_ABI_PREFIX;

/// Optional environment variable pointing to the directory containing `libsherpa-onnx-c-api`.
pub const SHERPA_LIB_DIR_ENV_VAR: &str = "PULSESCRIBE_SHERPA_LIB_DIR";

const DEFAULT_OFFLINE_CPU_THREADS: usize = 4;
const MAX_OFFLINE_CPU_THREADS: usize = 8;
const ONLINE_CPU_THREADS: i32 = 2;

/// Select the intra-op CPU thread count for offline `sherpa-onnx` recognizers.
///
/// On Apple Silicon this targets all fast (non-Efficiency) physical cores capped at 8 (e.g. 4 on
/// M1–M4 base, 6 on M6's 2 Super + 4 Performance cores, 6–8 on Pro/Max), avoiding E-core tail
/// jitter while speeding up Parakeet inference by ~10–16%. On other platforms it targets half of
/// logical cores (approximating physical cores) with a floor of `min(4, available)` and cap of 8.
fn recommended_offline_cpu_threads() -> i32 {
    let available = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(DEFAULT_OFFLINE_CPU_THREADS);
    #[cfg(target_os = "macos")]
    let fast_cores = crate::sherpa_ffi::macos_fast_physical_cores();
    #[cfg(not(target_os = "macos"))]
    let fast_cores = None;
    resolve_offline_cpu_threads(fast_cores, available)
}

fn resolve_offline_cpu_threads(fast_cores: Option<usize>, available: usize) -> i32 {
    let available = available.max(1);
    let target = match fast_cores.filter(|&c| c > 0) {
        Some(fast) => fast.min(available),
        None => (available / 2).max(DEFAULT_OFFLINE_CPU_THREADS.min(available)),
    };
    target.clamp(1, MAX_OFFLINE_CPU_THREADS) as i32
}

const fn segment_limit_seconds(engine_kind: LocalEngineKind) -> usize {
    match engine_kind {
        LocalEngineKind::SherpaOfflineTransducer => TRANSDUCER_SEGMENT_SECONDS,
        _ => NEMO_SEGMENT_SECONDS,
    }
}

/// Platform-native shared library filename for the `sherpa-onnx` C-API.
pub const fn sherpa_c_api_library_name() -> &'static str {
    if cfg!(target_os = "macos") {
        "libsherpa-onnx-c-api.dylib"
    } else if cfg!(target_os = "windows") {
        "sherpa-onnx-c-api.dll"
    } else {
        "libsherpa-onnx-c-api.so"
    }
}

/// Candidate shared library paths checked when loading `sherpa-onnx` dynamically:
/// 1. `$PULSESCRIBE_SHERPA_LIB_DIR/<lib>`
/// 2. `<current_exe_dir>/<lib>` and macOS bundle `<current_exe_dir>/../Frameworks/<lib>`
/// 3. `<config_dir>/libs/<lib>`
/// 4. Bare `<lib>` (OS dynamic loader search path)
pub fn default_sherpa_library_candidates(config_dir: Option<&Path>) -> Vec<PathBuf> {
    let lib_name = sherpa_c_api_library_name();
    let mut candidates = Vec::with_capacity(5);

    if let Some(env_dir) = std::env::var_os(SHERPA_LIB_DIR_ENV_VAR).filter(|v| !v.is_empty()) {
        let dir = PathBuf::from(env_dir);
        candidates.push(dir.join(lib_name));
    }

    if let Ok(exe) = std::env::current_exe() {
        if let Some(exe_dir) = exe.parent() {
            candidates.push(exe_dir.join(lib_name));
            if cfg!(target_os = "macos") {
                if let Some(contents_dir) = exe_dir.parent() {
                    candidates.push(contents_dir.join("Frameworks").join(lib_name));
                }
            }
        }
    }

    if let Some(cfg) = config_dir {
        candidates.push(cfg.join("libs").join(lib_name));
    }

    candidates.push(PathBuf::from(lib_name));
    candidates
}

fn load_sherpa_api(config_dir: Option<&Path>) -> Result<Arc<SherpaApi>, LocalAsrError> {
    let candidates = default_sherpa_library_candidates(config_dir);
    let mut explicit_err = None;
    let mut last_err = None;
    for path in &candidates {
        let is_explicit = path.components().count() > 1;
        // For absolute/relative explicit paths, skip non-existent files to preserve meaningful
        // error reporting when a real candidate fails ABI/symbol verification.
        if is_explicit && !path.exists() {
            continue;
        }
        match SherpaApi::load(path) {
            Ok(api) => {
                tracing::info!(
                    path = %path.display(),
                    version = api.version(),
                    "loaded sherpa-onnx C-API library"
                );
                return Ok(api);
            }
            Err(err) => {
                if is_explicit && explicit_err.is_none() {
                    explicit_err = Some(err);
                } else {
                    last_err = Some(err);
                }
            }
        }
    }

    Err(explicit_err.or(last_err).unwrap_or_else(|| {
        LocalAsrError::MissingRuntime(format!(
            "{} not found (set {} or install under ~/.pulsescribe/libs)",
            sherpa_c_api_library_name(),
            SHERPA_LIB_DIR_ENV_VAR
        ))
    }))
}

/// Offline `sherpa-onnx` CPU engine supporting Parakeet, Canary 1B v2, Cohere Transcribe, and
/// Qwen3-ASR.
pub struct SherpaOfflineEngine {
    model_id: String,
    engine_kind: LocalEngineKind,
    default_language: Option<String>,
    recognizer: Mutex<OfflineRecognizerHandle>,
}

impl SherpaOfflineEngine {
    /// Load a cached offline `sherpa-onnx` model using the CPU runtime.
    pub fn load(
        spec: &ModelSpec,
        installed: &InstalledModel,
        config_dir: Option<&Path>,
        language_hint: Option<&str>,
    ) -> Result<Self, LocalAsrError> {
        if !matches!(
            spec.engine,
            LocalEngineKind::SherpaOfflineTransducer
                | LocalEngineKind::SherpaOfflineCanary
                | LocalEngineKind::SherpaOfflineCohere
                | LocalEngineKind::SherpaOfflineQwen3Asr
        ) {
            return Err(LocalAsrError::InvalidModel(format!(
                "model '{}' is not a sherpa-onnx offline model",
                spec.id
            )));
        }

        let api = load_sherpa_api(config_dir)?;
        if matches!(
            spec.engine,
            LocalEngineKind::SherpaOfflineCohere | LocalEngineKind::SherpaOfflineQwen3Asr
        ) && !api.has_offline_stream_set_option()
        {
            return Err(LocalAsrError::MissingRuntime(
                "sherpa-onnx library missing SherpaOnnxOfflineStreamSetOption".to_owned(),
            ));
        }
        let provider = CString::new("cpu").expect("static CString");
        let decoding = CString::new("greedy_search").expect("static CString");

        let num_threads = recommended_offline_cpu_threads();
        let mut cfg = SherpaOnnxOfflineRecognizerConfig::default();
        cfg.feat_config.sample_rate = LOCAL_ASR_SAMPLE_RATE as i32;
        cfg.feat_config.feature_dim = 80;
        cfg.decoding_method = decoding.as_ptr();
        cfg.model_config.num_threads = num_threads;
        cfg.model_config.provider = provider.as_ptr();

        // Keep CStrings alive for the duration of `create_offline_recognizer`.
        let encoder = path_to_cstring(&installed.file_path("encoder.int8.onnx"))?;
        let decoder = path_to_cstring(&installed.file_path("decoder.int8.onnx"))?;
        let mut _tokens_keep: Option<CString> = None;
        let mut _joiner_keep: Option<CString> = None;
        let mut _model_type_keep: Option<CString> = None;
        let mut _lang_keep: Option<CString> = None;
        let mut _conv_frontend_keep: Option<CString> = None;
        let mut _tokenizer_dir_keep: Option<CString> = None;
        let mut default_language = None;

        if spec.engine != LocalEngineKind::SherpaOfflineQwen3Asr {
            let tokens = path_to_cstring(&installed.file_path("tokens.txt"))?;
            cfg.model_config.tokens = tokens.as_ptr();
            _tokens_keep = Some(tokens);
        }

        match spec.engine {
            LocalEngineKind::SherpaOfflineTransducer => {
                let joiner = path_to_cstring(&installed.file_path("joiner.int8.onnx"))?;
                let model_type = CString::new("nemo_transducer").expect("static CString");
                cfg.model_config.transducer.encoder = encoder.as_ptr();
                cfg.model_config.transducer.decoder = decoder.as_ptr();
                cfg.model_config.transducer.joiner = joiner.as_ptr();
                cfg.model_config.model_type = model_type.as_ptr();
                _joiner_keep = Some(joiner);
                _model_type_keep = Some(model_type);
            }
            LocalEngineKind::SherpaOfflineCanary => {
                let lang = resolve_canary_language(language_hint);
                let lang_c = CString::new(lang).map_err(|_| {
                    LocalAsrError::InvalidModel("invalid Canary language code".to_owned())
                })?;
                cfg.model_config.canary.encoder = encoder.as_ptr();
                cfg.model_config.canary.decoder = decoder.as_ptr();
                cfg.model_config.canary.src_lang = lang_c.as_ptr();
                cfg.model_config.canary.tgt_lang = lang_c.as_ptr();
                cfg.model_config.canary.use_pnc = 1;
                _lang_keep = Some(lang_c);
            }
            LocalEngineKind::SherpaOfflineCohere => {
                let lang = resolve_cohere_language(language_hint);
                let lang_c = CString::new(lang.as_str()).map_err(|_| {
                    LocalAsrError::InvalidModel("invalid Cohere language code".to_owned())
                })?;
                cfg.model_config.cohere_transcribe.encoder = encoder.as_ptr();
                cfg.model_config.cohere_transcribe.decoder = decoder.as_ptr();
                cfg.model_config.cohere_transcribe.language = lang_c.as_ptr();
                cfg.model_config.cohere_transcribe.use_punct = 1;
                cfg.model_config.cohere_transcribe.use_itn = 1;
                default_language = Some(lang);
                _lang_keep = Some(lang_c);
            }
            LocalEngineKind::SherpaOfflineQwen3Asr => {
                let conv_frontend = path_to_cstring(&installed.file_path("conv_frontend.onnx"))?;
                let tokenizer_dir = path_to_cstring(&installed.file_path("tokenizer"))?;
                cfg.model_config.qwen3_asr.conv_frontend = conv_frontend.as_ptr();
                cfg.model_config.qwen3_asr.encoder = encoder.as_ptr();
                cfg.model_config.qwen3_asr.decoder = decoder.as_ptr();
                cfg.model_config.qwen3_asr.tokenizer = tokenizer_dir.as_ptr();
                cfg.model_config.qwen3_asr.max_total_len = 512;
                cfg.model_config.qwen3_asr.max_new_tokens = 256;
                cfg.model_config.qwen3_asr.temperature = 1e-6;
                cfg.model_config.qwen3_asr.top_p = 0.8;
                cfg.model_config.qwen3_asr.seed = 42;
                _conv_frontend_keep = Some(conv_frontend);
                _tokenizer_dir_keep = Some(tokenizer_dir);
            }
            _ => unreachable!("guarded above"),
        }

        let recognizer = api.create_offline_recognizer(&cfg)?;
        tracing::info!(
            model = spec.id,
            engine = spec.engine.as_str(),
            threads = num_threads,
            "loaded local sherpa-onnx offline model"
        );
        Ok(Self {
            model_id: spec.id.to_owned(),
            engine_kind: spec.engine,
            default_language,
            recognizer: Mutex::new(recognizer),
        })
    }

    pub fn engine_kind(&self) -> LocalEngineKind {
        self.engine_kind
    }
}

impl LocalAsrEngine for SherpaOfflineEngine {
    fn model_id(&self) -> &str {
        &self.model_id
    }

    fn backend_name(&self) -> &'static str {
        "sherpa-onnx"
    }

    fn transcribe(&self, request: &LocalAsrRequest) -> Result<String, LocalAsrError> {
        if request.cancel_flag.load(Ordering::Acquire) {
            return Err(LocalAsrError::Cancelled);
        }
        if request.samples_16k.is_empty() {
            return Ok(String::new());
        }
        let cohere_lang = if self.engine_kind == LocalEngineKind::SherpaOfflineCohere {
            if normalize_language(request.language.as_deref()).is_some() {
                Some(resolve_cohere_language(request.language.as_deref()))
            } else {
                Some(
                    self.default_language
                        .clone()
                        .unwrap_or_else(|| resolve_cohere_language(None)),
                )
            }
        } else {
            None
        };
        let qwen3_lang = if self.engine_kind == LocalEngineKind::SherpaOfflineQwen3Asr {
            resolve_qwen3_language(request.language.as_deref())
        } else {
            None
        };
        let qwen3_hotwords = if self.engine_kind == LocalEngineKind::SherpaOfflineQwen3Asr {
            format_qwen3_hotwords(&request.keywords)
        } else {
            None
        };

        let mut stream_options: Vec<(&str, &str)> = Vec::new();
        if let Some(ref lang) = cohere_lang {
            stream_options.push(("language", lang.as_str()));
        }
        if let Some(lang) = qwen3_lang {
            stream_options.push(("language", lang));
        }
        if let Some(ref hotwords) = qwen3_hotwords {
            stream_options.push(("hotwords", hotwords.as_str()));
        }

        let trimmed_audio =
            trim_leading_trailing_silence(&request.samples_16k, LOCAL_ASR_SAMPLE_RATE);
        let pieces = split_audio_segments(
            trimmed_audio,
            LOCAL_ASR_SAMPLE_RATE,
            segment_limit_seconds(self.engine_kind),
        );
        let guard = self.recognizer.lock().unwrap_or_else(|e| e.into_inner());
        let mut parts = Vec::with_capacity(pieces.len());
        for piece in pieces {
            if request.cancel_flag.load(Ordering::Acquire) {
                return Err(LocalAsrError::Cancelled);
            }
            let text = guard.transcribe(LOCAL_ASR_SAMPLE_RATE as i32, piece, &stream_options)?;
            if request.cancel_flag.load(Ordering::Acquire) {
                return Err(LocalAsrError::Cancelled);
            }
            let trimmed = text.trim();
            if !trimmed.is_empty() {
                parts.push(trimmed.to_owned());
            }
        }
        Ok(parts.join(" "))
    }
}

/// Online streaming `sherpa-onnx` CPU model (Nemotron 3.5 live preview).
pub struct SherpaOnlineModel {
    model_id: String,
    recognizer: Arc<OnlineRecognizerHandle>,
    supported_languages: HashSet<String>,
}

impl SherpaOnlineModel {
    pub fn load(
        spec: &ModelSpec,
        installed: &InstalledModel,
        config_dir: Option<&Path>,
    ) -> Result<Self, LocalAsrError> {
        if spec.engine != LocalEngineKind::SherpaOnlineTransducer {
            return Err(LocalAsrError::InvalidModel(format!(
                "model '{}' is not a sherpa-onnx streaming model",
                spec.id
            )));
        }
        let api = load_sherpa_api(config_dir)?;
        let provider = CString::new("cpu").expect("static CString");
        let decoding = CString::new("greedy_search").expect("static CString");
        let tokens_path = installed.file_path("tokens.txt");
        let tokens = path_to_cstring(&tokens_path)?;
        let encoder = path_to_cstring(&installed.file_path("encoder.int8.onnx"))?;
        let decoder = path_to_cstring(&installed.file_path("decoder.int8.onnx"))?;
        let joiner = path_to_cstring(&installed.file_path("joiner.int8.onnx"))?;

        let mut cfg = SherpaOnnxOnlineRecognizerConfig::default();
        cfg.feat_config.sample_rate = LOCAL_ASR_SAMPLE_RATE as i32;
        cfg.feat_config.feature_dim = 80;
        cfg.decoding_method = decoding.as_ptr();
        cfg.model_config.transducer.encoder = encoder.as_ptr();
        cfg.model_config.transducer.decoder = decoder.as_ptr();
        cfg.model_config.transducer.joiner = joiner.as_ptr();
        cfg.model_config.tokens = tokens.as_ptr();
        cfg.model_config.num_threads = ONLINE_CPU_THREADS;
        cfg.model_config.provider = provider.as_ptr();

        let recognizer = Arc::new(api.create_online_recognizer(&cfg)?);
        let supported_languages = fs::read_to_string(&tokens_path)
            .map(|text| parse_nemotron_token_languages(&text))
            .unwrap_or_default();
        Ok(Self {
            model_id: spec.id.to_owned(),
            recognizer,
            supported_languages,
        })
    }

    pub fn model_id(&self) -> &str {
        &self.model_id
    }

    /// Open a new streaming preview session for a single dictation run.
    pub fn open_session(
        &self,
        language: Option<&str>,
    ) -> Result<SherpaStreamingSession, LocalAsrError> {
        let code = normalize_language(language)
            .and_then(|l| l.get(..2).map(ToOwned::to_owned))
            .filter(|code| self.supported_languages.contains(code))
            .unwrap_or_else(|| "auto".to_owned());
        let stream = self.recognizer.create_stream(Some(&code))?;
        Ok(SherpaStreamingSession { stream })
    }
}

fn parse_nemotron_token_languages(tokens_txt: &str) -> HashSet<String> {
    let mut langs = HashSet::new();
    for line in tokens_txt.lines() {
        // Matches `^<([a-z]{2})-[A-Z]{2}> ` from `providers/streaming_model.py`.
        let bytes = line.as_bytes();
        if bytes.len() >= 8
            && bytes[0] == b'<'
            && bytes[1].is_ascii_lowercase()
            && bytes[2].is_ascii_lowercase()
            && bytes[3] == b'-'
            && bytes[4].is_ascii_uppercase()
            && bytes[5].is_ascii_uppercase()
            && bytes[6] == b'>'
            && bytes[7] == b' '
        {
            if let Ok(code) = std::str::from_utf8(&bytes[1..3]) {
                langs.insert(code.to_owned());
            }
        }
    }
    langs
}

/// Active Nemotron streaming preview session.
pub struct SherpaStreamingSession {
    stream: OnlineStreamHandle,
}

impl LocalStreamingSession for SherpaStreamingSession {
    fn feed(&mut self, samples_16k: &[f32]) -> Result<(), LocalAsrError> {
        self.stream.feed(LOCAL_ASR_SAMPLE_RATE as i32, samples_16k)
    }

    fn text(&self) -> Result<String, LocalAsrError> {
        self.stream.text()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sherpa_ffi::is_supported_abi_version;

    #[test]
    fn abi_version_guard_accepts_2_0_x_and_rejects_incompatible_versions() {
        assert!(is_supported_abi_version("2.0.2"));
        assert!(is_supported_abi_version("2.0.3"));
        assert!(!is_supported_abi_version("1.12.23"));
        assert!(!is_supported_abi_version("3.0.0"));
        assert!(!is_supported_abi_version(""));
    }

    #[test]
    fn library_candidates_include_config_libs_and_bare_system_name() {
        let cfg = Path::new("/tmp/pulsescribe-test-cfg");
        let list = default_sherpa_library_candidates(Some(cfg));
        assert!(list
            .iter()
            .any(|p| p == &cfg.join("libs").join(sherpa_c_api_library_name())));
        assert_eq!(list.last().unwrap(), Path::new(sherpa_c_api_library_name()));
    }

    #[test]
    fn parses_nemotron_language_tags_from_tokens() {
        let tokens = "<blk> 0\n<de-DE> 12\n<en-US> 13\n<fr-fr> 14\n<es-ES>15\n";
        let langs = parse_nemotron_token_languages(tokens);
        assert!(langs.contains("de"));
        assert!(langs.contains("en"));
        assert!(!langs.contains("fr"));
        assert!(!langs.contains("es"));
    }

    #[test]
    fn offline_cpu_threads_targets_fast_cores_and_clamps_safely() {
        // Apple M6: 2 Super + 4 Performance = 6 fast cores (out of 12 total).
        assert_eq!(resolve_offline_cpu_threads(Some(6), 12), 6);
        // Apple M1–M4 base: 4 Performance cores (out of 8–10 total).
        assert_eq!(resolve_offline_cpu_threads(Some(4), 10), 4);
        // Apple M4 Max: 12 Performance cores capped at 8 to avoid ORT sync overhead.
        assert_eq!(resolve_offline_cpu_threads(Some(12), 16), 8);
        // Non-macOS fallback: half logical cores with floor min(4, available) and cap 8.
        assert_eq!(resolve_offline_cpu_threads(None, 2), 2);
        assert_eq!(resolve_offline_cpu_threads(None, 8), 4);
        assert_eq!(resolve_offline_cpu_threads(None, 12), 6);
        assert_eq!(resolve_offline_cpu_threads(None, 24), 8);

        let detected = recommended_offline_cpu_threads();
        assert!((1..=MAX_OFFLINE_CPU_THREADS as i32).contains(&detected));
    }

    #[test]
    fn transducer_uses_25s_segment_limit_while_encoder_decoder_keeps_15s() {
        assert_eq!(
            segment_limit_seconds(LocalEngineKind::SherpaOfflineTransducer),
            TRANSDUCER_SEGMENT_SECONDS
        );
        assert_eq!(
            segment_limit_seconds(LocalEngineKind::SherpaOfflineCanary),
            NEMO_SEGMENT_SECONDS
        );
        assert_eq!(
            segment_limit_seconds(LocalEngineKind::SherpaOfflineCohere),
            NEMO_SEGMENT_SECONDS
        );
        assert_eq!(
            segment_limit_seconds(LocalEngineKind::SherpaOfflineQwen3Asr),
            NEMO_SEGMENT_SECONDS
        );
    }
}
