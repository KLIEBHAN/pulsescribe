//! Native Whisper GGML backend via `whisper-rs` (`whisper.cpp`, Metal on macOS, CPU on Windows).
//!
//! Enforces ADR-003 Finding #2: never shortens `audio_ctx` on `large-v3-turbo` models (which use
//! fixed 30s positional embeddings and hallucinate if truncated), and caches a reusable
//! `WhisperState` behind a `Mutex` so decode buffers are not reallocated on every dictation.

use std::ffi::c_void;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, Once};

use whisper_rs::{
    FullParams, SamplingStrategy, WhisperContext, WhisperContextParameters, WhisperState,
};

use crate::{
    format_whisper_initial_prompt, normalize_language, LocalAsrEngine, LocalAsrError,
    LocalAsrRequest,
};

const MAX_WHISPER_THREADS: i32 = 8;
static LOGGING_HOOK_INIT: Once = Once::new();

/// Local Whisper GGML transcription engine (`whisper-rs 0.16`).
pub struct WhisperEngine {
    model_id: String,
    // Keep `WhisperContext` alive for as long as `WhisperState` exists (field drop order: state first).
    state: Mutex<WhisperState>,
    _context: WhisperContext,
    n_threads: i32,
}

impl WhisperEngine {
    /// Load a GGML Whisper model `.bin` file with Metal acceleration on macOS and CPU elsewhere.
    pub fn load(model_id: impl Into<String>, model_path: &Path) -> Result<Self, LocalAsrError> {
        let model_id = model_id.into();
        if !model_path.is_file() {
            return Err(LocalAsrError::ModelNotDownloaded(model_id));
        }

        // Route whisper.cpp/ggml C logs into `tracing` once so CLI stdout stays clean.
        LOGGING_HOOK_INIT.call_once(whisper_rs::install_logging_hooks);

        let mut ctx_params = WhisperContextParameters::default();
        ctx_params.use_gpu(cfg!(target_os = "macos"));

        let context = WhisperContext::new_with_params(model_path, ctx_params).map_err(|err| {
            LocalAsrError::Inference(format!("failed to load Whisper model '{model_id}': {err}"))
        })?;
        let state = context.create_state().map_err(|err| {
            LocalAsrError::Inference(format!(
                "failed to create Whisper state for '{model_id}': {err}"
            ))
        })?;

        let n_threads = recommended_whisper_threads();
        tracing::info!(
            model = %model_id,
            gpu = cfg!(target_os = "macos"),
            threads = n_threads,
            "loaded local Whisper model"
        );

        Ok(Self {
            model_id,
            state: Mutex::new(state),
            _context: context,
            n_threads,
        })
    }
}

impl LocalAsrEngine for WhisperEngine {
    fn model_id(&self) -> &str {
        &self.model_id
    }

    fn backend_name(&self) -> &'static str {
        "whisper-rs"
    }

    fn transcribe(&self, request: &LocalAsrRequest) -> Result<String, LocalAsrError> {
        if request.cancel_flag.load(Ordering::Acquire) {
            return Err(LocalAsrError::Cancelled);
        }
        if request.samples_16k.is_empty() {
            return Ok(String::new());
        }

        let language = normalize_language(request.language.as_deref());
        let initial_prompt = format_whisper_initial_prompt(&request.keywords);

        // ADR-003 Finding #2: leave `audio_ctx` at default full context (0 -> 1500) so
        // `large-v3-turbo` does not enter hallucination loops.
        let mut params = FullParams::new(SamplingStrategy::Greedy { best_of: 1 });
        params.set_n_threads(self.n_threads);
        params.set_language(language.as_deref());
        params.set_print_special(false);
        params.set_print_progress(false);
        params.set_print_realtime(false);
        params.set_print_timestamps(false);
        if let Some(ref prompt) = initial_prompt {
            params.set_initial_prompt(prompt);
        }

        // Note: `whisper-rs 0.16.0`'s `set_abort_callback_safe` casts a double-boxed fat pointer
        // as `*mut F` and leaks the allocation. Bind our stack-valid `&AtomicBool` directly via the
        // raw abort callback instead; `request.cancel_flag` strictly outlives `state.full`.
        attach_cancel_callback(&mut params, &request.cancel_flag);

        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        if request.cancel_flag.load(Ordering::Acquire) {
            return Err(LocalAsrError::Cancelled);
        }

        let run_result = state.full(params, &request.samples_16k);
        if request.cancel_flag.load(Ordering::Acquire) {
            return Err(LocalAsrError::Cancelled);
        }
        run_result
            .map_err(|err| LocalAsrError::Inference(format!("Whisper inference failed: {err}")))?;

        let n_segments = state.full_n_segments();
        let mut transcript = String::new();
        for idx in 0..n_segments {
            if let Some(segment) = state.get_segment(idx) {
                if let Ok(piece) = segment.to_str_lossy() {
                    transcript.push_str(&piece);
                }
            }
        }

        Ok(transcript.trim().to_owned())
    }
}

#[allow(unsafe_code)]
fn attach_cancel_callback(params: &mut FullParams<'_, '_>, cancel_flag: &AtomicBool) {
    unsafe extern "C" fn whisper_abort_cb(user_data: *mut c_void) -> bool {
        if user_data.is_null() {
            return false;
        }
        // SAFETY: `user_data` points to `request.cancel_flag` (`AtomicBool`) which remains alive
        // and immutable in address for the entire synchronous duration of `WhisperState::full`.
        unsafe { (*(user_data as *const AtomicBool)).load(Ordering::Acquire) }
    }

    let flag_ptr = std::ptr::from_ref(cancel_flag) as *mut c_void;
    // SAFETY: `params` is consumed synchronously by `WhisperState::full` while `cancel_flag` is
    // borrowed on the caller's stack, and `whisper_abort_cb` only performs an atomic read.
    unsafe {
        params.set_abort_callback(Some(whisper_abort_cb));
        params.set_abort_callback_user_data(flag_ptr);
    }
}

fn recommended_whisper_threads() -> i32 {
    let available = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(4);
    let capped = available.clamp(1, MAX_WHISPER_THREADS as usize);
    capped as i32
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recommended_threads_stays_within_bounds() {
        let threads = recommended_whisper_threads();
        assert!((1..=MAX_WHISPER_THREADS).contains(&threads));
    }

    #[test]
    fn load_returns_model_not_downloaded_for_missing_file() {
        let err = WhisperEngine::load(
            "large-v3-turbo",
            Path::new("/nonexistent/pulsescribe/ggml-missing.bin"),
        )
        .err()
        .expect("missing model file must fail");
        assert!(matches!(err, LocalAsrError::ModelNotDownloaded(_)));
    }
}
