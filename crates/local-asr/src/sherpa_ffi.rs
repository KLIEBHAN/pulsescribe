//! Isolated dynamic FFI bindings to `sherpa-onnx` C-API (`2.0.x`) via `libloading`.
//!
//! Every `unsafe` call in `crates/local-asr` is confined strictly to this module.
//! All C structs mirror `k2-fsa/sherpa-onnx` `2.0.x` `sherpa-onnx/c-api/c-api.h` verbatim, and
//! [`SherpaApi::load`] verifies `SherpaOnnxGetVersionStr()` before invoking any recognizer entry
//! point so an incompatible shared library never receives a misaligned struct layout.

#![allow(unsafe_code)]

use std::ffi::{c_char, c_float, c_void, CStr, CString};
use std::path::Path;
use std::ptr;
use std::sync::Arc;

use libloading::{Library, Symbol};

use crate::LocalAsrError;

/// Required `SherpaOnnxGetVersionStr()` prefix verified before calling C-API recognizer constructors.
pub const SHERPA_SUPPORTED_ABI_PREFIX: &str = "2.0.";

#[repr(C)]
pub(crate) struct SherpaOnnxOfflineRecognizer {
    _private: [u8; 0],
}

#[repr(C)]
pub(crate) struct SherpaOnnxOfflineStream {
    _private: [u8; 0],
}

#[repr(C)]
pub(crate) struct SherpaOnnxOnlineRecognizer {
    _private: [u8; 0],
}

#[repr(C)]
pub(crate) struct SherpaOnnxOnlineStream {
    _private: [u8; 0],
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct SherpaOnnxFeatureConfig {
    pub sample_rate: i32,
    pub feature_dim: i32,
}

impl Default for SherpaOnnxFeatureConfig {
    fn default() -> Self {
        Self {
            sample_rate: 16_000,
            feature_dim: 80,
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct SherpaOnnxOfflineTransducerModelConfig {
    pub encoder: *const c_char,
    pub decoder: *const c_char,
    pub joiner: *const c_char,
}

impl Default for SherpaOnnxOfflineTransducerModelConfig {
    fn default() -> Self {
        Self {
            encoder: ptr::null(),
            decoder: ptr::null(),
            joiner: ptr::null(),
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct SherpaOnnxOfflineSingleModelConfig {
    pub model: *const c_char,
}

impl Default for SherpaOnnxOfflineSingleModelConfig {
    fn default() -> Self {
        Self { model: ptr::null() }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct SherpaOnnxOfflineWhisperModelConfig {
    pub encoder: *const c_char,
    pub decoder: *const c_char,
    pub language: *const c_char,
    pub task: *const c_char,
    pub tail_paddings: i32,
    pub enable_token_timestamps: i32,
    pub enable_segment_timestamps: i32,
}

impl Default for SherpaOnnxOfflineWhisperModelConfig {
    fn default() -> Self {
        Self {
            encoder: ptr::null(),
            decoder: ptr::null(),
            language: ptr::null(),
            task: ptr::null(),
            tail_paddings: 0,
            enable_token_timestamps: 0,
            enable_segment_timestamps: 0,
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct SherpaOnnxOfflineSenseVoiceModelConfig {
    pub model: *const c_char,
    pub language: *const c_char,
    pub use_itn: i32,
}

impl Default for SherpaOnnxOfflineSenseVoiceModelConfig {
    fn default() -> Self {
        Self {
            model: ptr::null(),
            language: ptr::null(),
            use_itn: 0,
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct SherpaOnnxOfflineMoonshineModelConfig {
    pub preprocessor: *const c_char,
    pub encoder: *const c_char,
    pub uncached_decoder: *const c_char,
    pub cached_decoder: *const c_char,
    pub merged_decoder: *const c_char,
}

impl Default for SherpaOnnxOfflineMoonshineModelConfig {
    fn default() -> Self {
        Self {
            preprocessor: ptr::null(),
            encoder: ptr::null(),
            uncached_decoder: ptr::null(),
            cached_decoder: ptr::null(),
            merged_decoder: ptr::null(),
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct SherpaOnnxOfflineFireRedAsrModelConfig {
    pub encoder: *const c_char,
    pub decoder: *const c_char,
}

impl Default for SherpaOnnxOfflineFireRedAsrModelConfig {
    fn default() -> Self {
        Self {
            encoder: ptr::null(),
            decoder: ptr::null(),
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct SherpaOnnxOfflineCanaryModelConfig {
    pub encoder: *const c_char,
    pub decoder: *const c_char,
    pub src_lang: *const c_char,
    pub tgt_lang: *const c_char,
    pub use_pnc: i32,
}

impl Default for SherpaOnnxOfflineCanaryModelConfig {
    fn default() -> Self {
        Self {
            encoder: ptr::null(),
            decoder: ptr::null(),
            src_lang: ptr::null(),
            tgt_lang: ptr::null(),
            use_pnc: 0,
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct SherpaOnnxOfflineFunASRNanoModelConfig {
    pub encoder_adaptor: *const c_char,
    pub llm: *const c_char,
    pub embedding: *const c_char,
    pub tokenizer: *const c_char,
    pub system_prompt: *const c_char,
    pub user_prompt: *const c_char,
    pub max_new_tokens: i32,
    pub temperature: c_float,
    pub top_p: c_float,
    pub seed: i32,
    pub language: *const c_char,
    pub itn: i32,
    pub hotwords: *const c_char,
}

impl Default for SherpaOnnxOfflineFunASRNanoModelConfig {
    fn default() -> Self {
        Self {
            encoder_adaptor: ptr::null(),
            llm: ptr::null(),
            embedding: ptr::null(),
            tokenizer: ptr::null(),
            system_prompt: ptr::null(),
            user_prompt: ptr::null(),
            max_new_tokens: 0,
            temperature: 0.0,
            top_p: 0.0,
            seed: 0,
            language: ptr::null(),
            itn: 0,
            hotwords: ptr::null(),
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct SherpaOnnxOfflineQwen3ASRModelConfig {
    pub conv_frontend: *const c_char,
    pub encoder: *const c_char,
    pub decoder: *const c_char,
    pub tokenizer: *const c_char,
    pub max_total_len: i32,
    pub max_new_tokens: i32,
    pub temperature: c_float,
    pub top_p: c_float,
    pub seed: i32,
    pub hotwords: *const c_char,
}

impl Default for SherpaOnnxOfflineQwen3ASRModelConfig {
    fn default() -> Self {
        Self {
            conv_frontend: ptr::null(),
            encoder: ptr::null(),
            decoder: ptr::null(),
            tokenizer: ptr::null(),
            max_total_len: 0,
            max_new_tokens: 0,
            temperature: 0.0,
            top_p: 0.0,
            seed: 0,
            hotwords: ptr::null(),
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct SherpaOnnxOfflineCohereTranscribeModelConfig {
    pub encoder: *const c_char,
    pub decoder: *const c_char,
    pub language: *const c_char,
    pub use_punct: i32,
    pub use_itn: i32,
}

impl Default for SherpaOnnxOfflineCohereTranscribeModelConfig {
    fn default() -> Self {
        Self {
            encoder: ptr::null(),
            decoder: ptr::null(),
            language: ptr::null(),
            use_punct: 0,
            use_itn: 0,
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct SherpaOnnxOfflineModelConfig {
    pub transducer: SherpaOnnxOfflineTransducerModelConfig,
    pub paraformer: SherpaOnnxOfflineSingleModelConfig,
    pub nemo_ctc: SherpaOnnxOfflineSingleModelConfig,
    pub whisper: SherpaOnnxOfflineWhisperModelConfig,
    pub tdnn: SherpaOnnxOfflineSingleModelConfig,
    pub tokens: *const c_char,
    pub num_threads: i32,
    pub debug: i32,
    pub provider: *const c_char,
    pub model_type: *const c_char,
    pub modeling_unit: *const c_char,
    pub bpe_vocab: *const c_char,
    pub telespeech_ctc: *const c_char,
    pub sense_voice: SherpaOnnxOfflineSenseVoiceModelConfig,
    pub moonshine: SherpaOnnxOfflineMoonshineModelConfig,
    pub fire_red_asr: SherpaOnnxOfflineFireRedAsrModelConfig,
    pub dolphin: SherpaOnnxOfflineSingleModelConfig,
    pub zipformer_ctc: SherpaOnnxOfflineSingleModelConfig,
    pub canary: SherpaOnnxOfflineCanaryModelConfig,
    pub wenet_ctc: SherpaOnnxOfflineSingleModelConfig,
    pub omnilingual: SherpaOnnxOfflineSingleModelConfig,
    pub medasr: SherpaOnnxOfflineSingleModelConfig,
    pub funasr_nano: SherpaOnnxOfflineFunASRNanoModelConfig,
    pub fire_red_asr_ctc: SherpaOnnxOfflineSingleModelConfig,
    pub qwen3_asr: SherpaOnnxOfflineQwen3ASRModelConfig,
    pub cohere_transcribe: SherpaOnnxOfflineCohereTranscribeModelConfig,
}

impl Default for SherpaOnnxOfflineModelConfig {
    fn default() -> Self {
        Self {
            transducer: Default::default(),
            paraformer: Default::default(),
            nemo_ctc: Default::default(),
            whisper: Default::default(),
            tdnn: Default::default(),
            tokens: ptr::null(),
            num_threads: 4,
            debug: 0,
            provider: ptr::null(),
            model_type: ptr::null(),
            modeling_unit: ptr::null(),
            bpe_vocab: ptr::null(),
            telespeech_ctc: ptr::null(),
            sense_voice: Default::default(),
            moonshine: Default::default(),
            fire_red_asr: Default::default(),
            dolphin: Default::default(),
            zipformer_ctc: Default::default(),
            canary: Default::default(),
            wenet_ctc: Default::default(),
            omnilingual: Default::default(),
            medasr: Default::default(),
            funasr_nano: Default::default(),
            fire_red_asr_ctc: Default::default(),
            qwen3_asr: Default::default(),
            cohere_transcribe: Default::default(),
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct SherpaOnnxOfflineLMConfig {
    pub model: *const c_char,
    pub scale: c_float,
}

impl Default for SherpaOnnxOfflineLMConfig {
    fn default() -> Self {
        Self {
            model: ptr::null(),
            scale: 0.0,
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct SherpaOnnxHomophoneReplacerConfig {
    pub dict_dir: *const c_char,
    pub lexicon: *const c_char,
    pub rule_fsts: *const c_char,
}

impl Default for SherpaOnnxHomophoneReplacerConfig {
    fn default() -> Self {
        Self {
            dict_dir: ptr::null(),
            lexicon: ptr::null(),
            rule_fsts: ptr::null(),
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct SherpaOnnxCtcFstDecoderConfig {
    pub graph: *const c_char,
    pub max_active: i32,
}

impl Default for SherpaOnnxCtcFstDecoderConfig {
    fn default() -> Self {
        Self {
            graph: ptr::null(),
            max_active: 0,
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct SherpaOnnxOfflineRecognizerConfig {
    pub feat_config: SherpaOnnxFeatureConfig,
    pub model_config: SherpaOnnxOfflineModelConfig,
    pub lm_config: SherpaOnnxOfflineLMConfig,
    pub decoding_method: *const c_char,
    pub max_active_paths: i32,
    pub hotwords_file: *const c_char,
    pub hotwords_score: c_float,
    pub rule_fsts: *const c_char,
    pub rule_fars: *const c_char,
    pub blank_penalty: c_float,
    pub hr: SherpaOnnxHomophoneReplacerConfig,
    pub ctc_fst_decoder_config: SherpaOnnxCtcFstDecoderConfig,
}

impl Default for SherpaOnnxOfflineRecognizerConfig {
    fn default() -> Self {
        Self {
            feat_config: Default::default(),
            model_config: Default::default(),
            lm_config: Default::default(),
            decoding_method: ptr::null(),
            max_active_paths: 4,
            hotwords_file: ptr::null(),
            hotwords_score: 1.5,
            rule_fsts: ptr::null(),
            rule_fars: ptr::null(),
            blank_penalty: 0.0,
            hr: Default::default(),
            ctc_fst_decoder_config: Default::default(),
        }
    }
}

#[repr(C)]
pub(crate) struct SherpaOnnxOfflineRecognizerResult {
    pub text: *const c_char,
    pub timestamps: *mut c_float,
    pub count: i32,
    pub tokens: *const c_char,
    pub tokens_arr: *const *const c_char,
    pub json: *const c_char,
    pub lang: *const c_char,
    pub emotion: *const c_char,
    pub event: *const c_char,
    pub durations: *mut c_float,
    pub ys_log_probs: *mut c_float,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct SherpaOnnxOnlineTransducerModelConfig {
    pub encoder: *const c_char,
    pub decoder: *const c_char,
    pub joiner: *const c_char,
}

impl Default for SherpaOnnxOnlineTransducerModelConfig {
    fn default() -> Self {
        Self {
            encoder: ptr::null(),
            decoder: ptr::null(),
            joiner: ptr::null(),
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct SherpaOnnxOnlineParaformerModelConfig {
    pub encoder: *const c_char,
    pub decoder: *const c_char,
}

impl Default for SherpaOnnxOnlineParaformerModelConfig {
    fn default() -> Self {
        Self {
            encoder: ptr::null(),
            decoder: ptr::null(),
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct SherpaOnnxOnlineModelConfig {
    pub transducer: SherpaOnnxOnlineTransducerModelConfig,
    pub paraformer: SherpaOnnxOnlineParaformerModelConfig,
    pub zipformer2_ctc: SherpaOnnxOfflineSingleModelConfig,
    pub tokens: *const c_char,
    pub num_threads: i32,
    pub provider: *const c_char,
    pub debug: i32,
    pub model_type: *const c_char,
    pub modeling_unit: *const c_char,
    pub bpe_vocab: *const c_char,
    pub tokens_buf: *const c_char,
    pub tokens_buf_size: i32,
    pub nemo_ctc: SherpaOnnxOfflineSingleModelConfig,
    pub t_one_ctc: SherpaOnnxOfflineSingleModelConfig,
}

impl Default for SherpaOnnxOnlineModelConfig {
    fn default() -> Self {
        Self {
            transducer: Default::default(),
            paraformer: Default::default(),
            zipformer2_ctc: Default::default(),
            tokens: ptr::null(),
            num_threads: 2,
            provider: ptr::null(),
            debug: 0,
            model_type: ptr::null(),
            modeling_unit: ptr::null(),
            bpe_vocab: ptr::null(),
            tokens_buf: ptr::null(),
            tokens_buf_size: 0,
            nemo_ctc: Default::default(),
            t_one_ctc: Default::default(),
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct SherpaOnnxOnlineRecognizerConfig {
    pub feat_config: SherpaOnnxFeatureConfig,
    pub model_config: SherpaOnnxOnlineModelConfig,
    pub decoding_method: *const c_char,
    pub max_active_paths: i32,
    pub enable_endpoint: i32,
    pub rule1_min_trailing_silence: c_float,
    pub rule2_min_trailing_silence: c_float,
    pub rule3_min_utterance_length: c_float,
    pub hotwords_file: *const c_char,
    pub hotwords_score: c_float,
    pub ctc_fst_decoder_config: SherpaOnnxCtcFstDecoderConfig,
    pub rule_fsts: *const c_char,
    pub rule_fars: *const c_char,
    pub blank_penalty: c_float,
    pub hotwords_buf: *const c_char,
    pub hotwords_buf_size: i32,
    pub hr: SherpaOnnxHomophoneReplacerConfig,
}

impl Default for SherpaOnnxOnlineRecognizerConfig {
    fn default() -> Self {
        Self {
            feat_config: Default::default(),
            model_config: Default::default(),
            decoding_method: ptr::null(),
            max_active_paths: 4,
            enable_endpoint: 0,
            rule1_min_trailing_silence: 2.4,
            rule2_min_trailing_silence: 1.2,
            rule3_min_utterance_length: 20.0,
            hotwords_file: ptr::null(),
            hotwords_score: 1.5,
            ctc_fst_decoder_config: Default::default(),
            rule_fsts: ptr::null(),
            rule_fars: ptr::null(),
            blank_penalty: 0.0,
            hotwords_buf: ptr::null(),
            hotwords_buf_size: 0,
            hr: Default::default(),
        }
    }
}

#[repr(C)]
pub(crate) struct SherpaOnnxOnlineRecognizerResult {
    pub text: *const c_char,
    pub tokens: *const c_char,
    pub tokens_arr: *const *const c_char,
    pub timestamps: *mut c_float,
    pub count: i32,
    pub json: *const c_char,
}

type FnGetVersionStr = unsafe extern "C" fn() -> *const c_char;
type FnCreateOfflineRecognizer = unsafe extern "C" fn(
    *const SherpaOnnxOfflineRecognizerConfig,
) -> *const SherpaOnnxOfflineRecognizer;
type FnDestroyOfflineRecognizer = unsafe extern "C" fn(*const SherpaOnnxOfflineRecognizer);
type FnCreateOfflineStream =
    unsafe extern "C" fn(*const SherpaOnnxOfflineRecognizer) -> *const SherpaOnnxOfflineStream;
type FnDestroyOfflineStream = unsafe extern "C" fn(*const SherpaOnnxOfflineStream);
type FnOfflineStreamSetOption =
    unsafe extern "C" fn(*const SherpaOnnxOfflineStream, *const c_char, *const c_char);
type FnAcceptWaveformOffline =
    unsafe extern "C" fn(*const SherpaOnnxOfflineStream, i32, *const c_float, i32);
type FnDecodeOfflineStream =
    unsafe extern "C" fn(*const SherpaOnnxOfflineRecognizer, *const SherpaOnnxOfflineStream);
type FnGetOfflineStreamResult = unsafe extern "C" fn(
    *const SherpaOnnxOfflineStream,
) -> *const SherpaOnnxOfflineRecognizerResult;
type FnDestroyOfflineRecognizerResult =
    unsafe extern "C" fn(*const SherpaOnnxOfflineRecognizerResult);

type FnCreateOnlineRecognizer = unsafe extern "C" fn(
    *const SherpaOnnxOnlineRecognizerConfig,
) -> *const SherpaOnnxOnlineRecognizer;
type FnDestroyOnlineRecognizer = unsafe extern "C" fn(*const SherpaOnnxOnlineRecognizer);
type FnCreateOnlineStream =
    unsafe extern "C" fn(*const SherpaOnnxOnlineRecognizer) -> *const SherpaOnnxOnlineStream;
type FnDestroyOnlineStream = unsafe extern "C" fn(*const SherpaOnnxOnlineStream);
type FnOnlineStreamSetOption =
    unsafe extern "C" fn(*const SherpaOnnxOnlineStream, *const c_char, *const c_char);
type FnOnlineStreamAcceptWaveform =
    unsafe extern "C" fn(*const SherpaOnnxOnlineStream, i32, *const c_float, i32);
type FnIsOnlineStreamReady =
    unsafe extern "C" fn(*const SherpaOnnxOnlineRecognizer, *const SherpaOnnxOnlineStream) -> i32;
type FnDecodeOnlineStream =
    unsafe extern "C" fn(*const SherpaOnnxOnlineRecognizer, *const SherpaOnnxOnlineStream);
type FnGetOnlineStreamResult = unsafe extern "C" fn(
    *const SherpaOnnxOnlineRecognizer,
    *const SherpaOnnxOnlineStream,
) -> *const SherpaOnnxOnlineRecognizerResult;
type FnDestroyOnlineRecognizerResult =
    unsafe extern "C" fn(*const SherpaOnnxOnlineRecognizerResult);

/// Dynamically loaded `sherpa-onnx` C-API function table bound to the lifetime of `_lib`.
pub(crate) struct SherpaApi {
    _lib: Library,
    version: String,
    create_offline_recognizer: FnCreateOfflineRecognizer,
    destroy_offline_recognizer: FnDestroyOfflineRecognizer,
    create_offline_stream: FnCreateOfflineStream,
    destroy_offline_stream: FnDestroyOfflineStream,
    offline_stream_set_option: Option<FnOfflineStreamSetOption>,
    accept_waveform_offline: FnAcceptWaveformOffline,
    decode_offline_stream: FnDecodeOfflineStream,
    get_offline_stream_result: FnGetOfflineStreamResult,
    destroy_offline_recognizer_result: FnDestroyOfflineRecognizerResult,
    create_online_recognizer: FnCreateOnlineRecognizer,
    destroy_online_recognizer: FnDestroyOnlineRecognizer,
    create_online_stream: FnCreateOnlineStream,
    destroy_online_stream: FnDestroyOnlineStream,
    online_stream_set_option: Option<FnOnlineStreamSetOption>,
    online_stream_accept_waveform: FnOnlineStreamAcceptWaveform,
    is_online_stream_ready: FnIsOnlineStreamReady,
    decode_online_stream: FnDecodeOnlineStream,
    get_online_stream_result: FnGetOnlineStreamResult,
    destroy_online_recognizer_result: FnDestroyOnlineRecognizerResult,
}

unsafe fn load_sym<T: Copy>(lib: &Library, name: &[u8]) -> Result<T, LocalAsrError> {
    let sym: Symbol<'_, T> = lib.get(name).map_err(|err| {
        LocalAsrError::MissingRuntime(format!(
            "missing sherpa-onnx C-API symbol '{}': {err}",
            String::from_utf8_lossy(name).trim_end_matches('\0')
        ))
    })?;
    Ok(*sym)
}

pub(crate) fn is_supported_abi_version(version: &str) -> bool {
    version.trim().starts_with(SHERPA_SUPPORTED_ABI_PREFIX)
}

#[cfg(windows)]
unsafe fn open_library(lib_path: &Path) -> Result<Library, libloading::Error> {
    use libloading::os::windows::{
        Library as WinLibrary, LOAD_LIBRARY_SEARCH_DEFAULT_DIRS, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR,
    };
    // Search sibling DLLs (e.g. `onnxruntime.dll` next to `sherpa-onnx-c-api.dll` in
    // `~/.pulsescribe/libs` or `PULSESCRIBE_SHERPA_LIB_DIR`) without enabling unsafe CWD lookup.
    if lib_path.components().count() > 1 {
        let resolved = if lib_path.is_absolute() {
            std::borrow::Cow::Borrowed(lib_path)
        } else {
            match std::path::absolute(lib_path) {
                Ok(abs) => std::borrow::Cow::Owned(abs),
                Err(_) => std::borrow::Cow::Borrowed(lib_path),
            }
        };
        if resolved.is_absolute() {
            return WinLibrary::load_with_flags(
                resolved.as_ref(),
                LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS,
            )
            .map(Library::from);
        }
    }
    Library::new(lib_path)
}

#[cfg(not(windows))]
unsafe fn open_library(lib_path: &Path) -> Result<Library, libloading::Error> {
    Library::new(lib_path)
}

impl SherpaApi {
    /// Load `libsherpa-onnx-c-api` from `lib_path` and verify its C-API ABI version (`2.0.x`).
    pub(crate) fn load(lib_path: &Path) -> Result<Arc<Self>, LocalAsrError> {
        let lib = unsafe { open_library(lib_path) }.map_err(|err| {
            LocalAsrError::MissingRuntime(format!(
                "failed to load sherpa-onnx library '{}': {err}",
                lib_path.display()
            ))
        })?;

        let version = unsafe {
            let get_ver: FnGetVersionStr = load_sym(&lib, b"SherpaOnnxGetVersionStr\0")?;
            let raw = get_ver();
            if raw.is_null() {
                String::new()
            } else {
                CStr::from_ptr(raw).to_string_lossy().into_owned()
            }
        };

        if !is_supported_abi_version(&version) {
            return Err(LocalAsrError::MissingRuntime(format!(
                "unsupported sherpa-onnx C-API version '{version}' (expected '{SHERPA_SUPPORTED_ABI_PREFIX}x')"
            )));
        }

        let api = unsafe {
            Self {
                create_offline_recognizer: load_sym(&lib, b"SherpaOnnxCreateOfflineRecognizer\0")?,
                destroy_offline_recognizer: load_sym(
                    &lib,
                    b"SherpaOnnxDestroyOfflineRecognizer\0",
                )?,
                create_offline_stream: load_sym(&lib, b"SherpaOnnxCreateOfflineStream\0")?,
                destroy_offline_stream: load_sym(&lib, b"SherpaOnnxDestroyOfflineStream\0")?,
                offline_stream_set_option: load_sym(&lib, b"SherpaOnnxOfflineStreamSetOption\0")
                    .ok(),
                accept_waveform_offline: load_sym(&lib, b"SherpaOnnxAcceptWaveformOffline\0")?,
                decode_offline_stream: load_sym(&lib, b"SherpaOnnxDecodeOfflineStream\0")?,
                get_offline_stream_result: load_sym(&lib, b"SherpaOnnxGetOfflineStreamResult\0")?,
                destroy_offline_recognizer_result: load_sym(
                    &lib,
                    b"SherpaOnnxDestroyOfflineRecognizerResult\0",
                )?,
                create_online_recognizer: load_sym(&lib, b"SherpaOnnxCreateOnlineRecognizer\0")?,
                destroy_online_recognizer: load_sym(&lib, b"SherpaOnnxDestroyOnlineRecognizer\0")?,
                create_online_stream: load_sym(&lib, b"SherpaOnnxCreateOnlineStream\0")?,
                destroy_online_stream: load_sym(&lib, b"SherpaOnnxDestroyOnlineStream\0")?,
                online_stream_set_option: load_sym(&lib, b"SherpaOnnxOnlineStreamSetOption\0").ok(),
                online_stream_accept_waveform: load_sym(
                    &lib,
                    b"SherpaOnnxOnlineStreamAcceptWaveform\0",
                )?,
                is_online_stream_ready: load_sym(&lib, b"SherpaOnnxIsOnlineStreamReady\0")?,
                decode_online_stream: load_sym(&lib, b"SherpaOnnxDecodeOnlineStream\0")?,
                get_online_stream_result: load_sym(&lib, b"SherpaOnnxGetOnlineStreamResult\0")?,
                destroy_online_recognizer_result: load_sym(
                    &lib,
                    b"SherpaOnnxDestroyOnlineRecognizerResult\0",
                )?,
                version,
                _lib: lib,
            }
        };

        Ok(Arc::new(api))
    }

    pub(crate) fn version(&self) -> &str {
        &self.version
    }

    pub(crate) fn create_offline_recognizer(
        self: &Arc<Self>,
        cfg: &SherpaOnnxOfflineRecognizerConfig,
    ) -> Result<OfflineRecognizerHandle, LocalAsrError> {
        let ptr = unsafe { (self.create_offline_recognizer)(cfg) };
        if ptr.is_null() {
            return Err(LocalAsrError::Inference(
                "SherpaOnnxCreateOfflineRecognizer returned null".to_owned(),
            ));
        }
        Ok(OfflineRecognizerHandle {
            raw: ptr as *mut c_void,
            api: Arc::clone(self),
        })
    }

    pub(crate) fn create_online_recognizer(
        self: &Arc<Self>,
        cfg: &SherpaOnnxOnlineRecognizerConfig,
    ) -> Result<OnlineRecognizerHandle, LocalAsrError> {
        let ptr = unsafe { (self.create_online_recognizer)(cfg) };
        if ptr.is_null() {
            return Err(LocalAsrError::Inference(
                "SherpaOnnxCreateOnlineRecognizer returned null".to_owned(),
            ));
        }
        Ok(OnlineRecognizerHandle {
            raw: ptr as *mut c_void,
            api: Arc::clone(self),
        })
    }
}

/// Safe RAII wrapper over `SherpaOnnxOfflineRecognizer` keeping `SherpaApi` alive.
pub(crate) struct OfflineRecognizerHandle {
    raw: *mut c_void,
    api: Arc<SherpaApi>,
}

// `SherpaOnnxOfflineRecognizer` is immutable after creation; `SherpaOfflineEngine` serializes
// decode calls behind a `Mutex` for conservative thread-safety across ONNX Runtime sessions.
unsafe impl Send for OfflineRecognizerHandle {}

impl OfflineRecognizerHandle {
    pub(crate) fn transcribe(
        &self,
        sample_rate: i32,
        samples: &[f32],
        options: &[(&str, &str)],
    ) -> Result<String, LocalAsrError> {
        let n = i32::try_from(samples.len()).map_err(|_| {
            LocalAsrError::Inference("audio buffer exceeds i32 sample limit".to_owned())
        })?;
        let recognizer = self.raw as *const SherpaOnnxOfflineRecognizer;
        let stream = unsafe { (self.api.create_offline_stream)(recognizer) };
        if stream.is_null() {
            return Err(LocalAsrError::Inference(
                "SherpaOnnxCreateOfflineStream returned null".to_owned(),
            ));
        }
        let _stream_guard = OfflineStreamGuard {
            raw: stream,
            api: &self.api,
        };

        if !options.is_empty() {
            let set_opt = self.api.offline_stream_set_option.ok_or_else(|| {
                LocalAsrError::MissingRuntime(
                    "sherpa-onnx library missing SherpaOnnxOfflineStreamSetOption".to_owned(),
                )
            })?;
            for &(key, value) in options {
                if let (Ok(k), Ok(v)) = (CString::new(key), CString::new(value)) {
                    unsafe { set_opt(stream, k.as_ptr(), v.as_ptr()) };
                }
            }
        }

        unsafe {
            (self.api.accept_waveform_offline)(stream, sample_rate, samples.as_ptr(), n);
            (self.api.decode_offline_stream)(recognizer, stream);
        }

        let result_ptr = unsafe { (self.api.get_offline_stream_result)(stream) };
        if result_ptr.is_null() {
            return Ok(String::new());
        }
        let _result_guard = OfflineResultGuard {
            raw: result_ptr,
            api: &self.api,
        };

        let text = unsafe {
            let text_ptr = (*result_ptr).text;
            if text_ptr.is_null() {
                String::new()
            } else {
                CStr::from_ptr(text_ptr).to_string_lossy().into_owned()
            }
        };
        Ok(text.trim().to_owned())
    }
}

impl Drop for OfflineRecognizerHandle {
    fn drop(&mut self) {
        unsafe {
            (self.api.destroy_offline_recognizer)(self.raw as *const SherpaOnnxOfflineRecognizer);
        }
    }
}

struct OfflineStreamGuard<'a> {
    raw: *const SherpaOnnxOfflineStream,
    api: &'a SherpaApi,
}

impl Drop for OfflineStreamGuard<'_> {
    fn drop(&mut self) {
        unsafe { (self.api.destroy_offline_stream)(self.raw) };
    }
}

struct OfflineResultGuard<'a> {
    raw: *const SherpaOnnxOfflineRecognizerResult,
    api: &'a SherpaApi,
}

impl Drop for OfflineResultGuard<'_> {
    fn drop(&mut self) {
        unsafe { (self.api.destroy_offline_recognizer_result)(self.raw) };
    }
}

/// Safe RAII wrapper over `SherpaOnnxOnlineRecognizer` keeping `SherpaApi` alive.
pub(crate) struct OnlineRecognizerHandle {
    raw: *mut c_void,
    api: Arc<SherpaApi>,
}

unsafe impl Send for OnlineRecognizerHandle {}
unsafe impl Sync for OnlineRecognizerHandle {}

impl OnlineRecognizerHandle {
    pub(crate) fn create_stream(
        self: &Arc<Self>,
        language: Option<&str>,
    ) -> Result<OnlineStreamHandle, LocalAsrError> {
        let recognizer = self.raw as *const SherpaOnnxOnlineRecognizer;
        let stream = unsafe { (self.api.create_online_stream)(recognizer) };
        if stream.is_null() {
            return Err(LocalAsrError::Inference(
                "SherpaOnnxCreateOnlineStream returned null".to_owned(),
            ));
        }

        if let (Some(lang), Some(set_opt)) = (language, self.api.online_stream_set_option) {
            if let (Ok(k), Ok(v)) = (CString::new("language"), CString::new(lang)) {
                unsafe { set_opt(stream, k.as_ptr(), v.as_ptr()) };
            }
        }

        Ok(OnlineStreamHandle {
            raw: stream as *mut c_void,
            recognizer: Arc::clone(self),
        })
    }
}

impl Drop for OnlineRecognizerHandle {
    fn drop(&mut self) {
        unsafe {
            (self.api.destroy_online_recognizer)(self.raw as *const SherpaOnnxOnlineRecognizer);
        }
    }
}

/// Safe RAII wrapper over `SherpaOnnxOnlineStream` keeping `OnlineRecognizerHandle` alive.
pub(crate) struct OnlineStreamHandle {
    raw: *mut c_void,
    recognizer: Arc<OnlineRecognizerHandle>,
}

unsafe impl Send for OnlineStreamHandle {}

impl OnlineStreamHandle {
    pub(crate) fn feed(&mut self, sample_rate: i32, samples: &[f32]) -> Result<(), LocalAsrError> {
        if samples.is_empty() {
            return Ok(());
        }
        let n = i32::try_from(samples.len()).map_err(|_| {
            LocalAsrError::Inference("audio chunk exceeds i32 sample limit".to_owned())
        })?;
        let recognizer = self.recognizer.raw as *const SherpaOnnxOnlineRecognizer;
        let stream = self.raw as *const SherpaOnnxOnlineStream;
        unsafe {
            (self.recognizer.api.online_stream_accept_waveform)(
                stream,
                sample_rate,
                samples.as_ptr(),
                n,
            );
            while (self.recognizer.api.is_online_stream_ready)(recognizer, stream) != 0 {
                (self.recognizer.api.decode_online_stream)(recognizer, stream);
            }
        }
        Ok(())
    }

    pub(crate) fn text(&self) -> Result<String, LocalAsrError> {
        let recognizer = self.recognizer.raw as *const SherpaOnnxOnlineRecognizer;
        let stream = self.raw as *const SherpaOnnxOnlineStream;
        let result_ptr =
            unsafe { (self.recognizer.api.get_online_stream_result)(recognizer, stream) };
        if result_ptr.is_null() {
            return Ok(String::new());
        }
        let _guard = OnlineResultGuard {
            raw: result_ptr,
            api: &self.recognizer.api,
        };
        let text = unsafe {
            let text_ptr = (*result_ptr).text;
            if text_ptr.is_null() {
                String::new()
            } else {
                CStr::from_ptr(text_ptr).to_string_lossy().into_owned()
            }
        };
        Ok(text.trim().to_owned())
    }
}

impl Drop for OnlineStreamHandle {
    fn drop(&mut self) {
        unsafe {
            (self.recognizer.api.destroy_online_stream)(self.raw as *const SherpaOnnxOnlineStream);
        }
    }
}

struct OnlineResultGuard<'a> {
    raw: *const SherpaOnnxOnlineRecognizerResult,
    api: &'a SherpaApi,
}

impl Drop for OnlineResultGuard<'_> {
    fn drop(&mut self) {
        unsafe { (self.api.destroy_online_recognizer_result)(self.raw) };
    }
}

pub(crate) fn path_to_cstring(path: &Path) -> Result<CString, LocalAsrError> {
    let text = path.to_str().ok_or_else(|| {
        LocalAsrError::InvalidModel(format!(
            "non-UTF-8 model path cannot be passed to sherpa-onnx: {}",
            path.display()
        ))
    })?;
    CString::new(text).map_err(|_| {
        LocalAsrError::InvalidModel("model path contains interior NUL byte".to_owned())
    })
}
