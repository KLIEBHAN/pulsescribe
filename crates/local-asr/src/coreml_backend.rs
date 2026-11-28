//! Native Apple Silicon CoreML / Neural Engine (ANE) Parakeet backend via FluidAudio.
//!
//! Bridges Rust to a minimal statically linked Swift wrapper (`PulseScribeFluidBridge`) around
//! `FluidInference/FluidAudio` using `@_cdecl` C FFI. Model weights are downloaded and cached by
//! PulseScribe's own [`crate::ModelDownloader`] and [`crate::ModelCache`]; Swift only loads
//! verified `.mlmodelc` components from disk (`AsrModels.loadLocal`) with zero network access.

#[allow(unsafe_code)]
mod ffi {
    use std::ffi::{c_char, c_void};

    extern "C" {
        pub(super) fn ps_fluid_init_parakeet(
            model_dir: *const c_char,
            version_code: i32,
            err_out: *mut *mut c_char,
        ) -> *mut c_void;

        pub(super) fn ps_fluid_transcribe(
            handle: *mut c_void,
            samples: *const f32,
            sample_count: i32,
            text_out: *mut *mut c_char,
            err_out: *mut *mut c_char,
        ) -> i32;

        pub(super) fn ps_fluid_destroy(handle: *mut c_void);

        pub(super) fn ps_fluid_free_string(ptr: *mut c_char);
    }
}

use std::ffi::{c_char, c_void, CStr, CString};
use std::ptr::{self, NonNull};
use std::sync::atomic::Ordering;
use std::sync::Mutex;

use crate::cache::InstalledModel;
use crate::catalog::{LocalEngineKind, ModelSpec};
use crate::{
    trim_leading_trailing_silence, LocalAsrEngine, LocalAsrError, LocalAsrRequest,
    LOCAL_ASR_SAMPLE_RATE,
};

/// Version code matching `ps_fluid_init_parakeet` in `FluidBridge.swift`.
const VERSION_CODE_V3: i32 = 0;
const VERSION_CODE_ULTRA: i32 = 1;

/// Owned opaque pointer to `FluidParakeetBox` on the Swift side.
struct FluidHandle(NonNull<c_void>);

// SAFETY: `FluidParakeetBox` is `@unchecked Sendable` and every call into `ps_fluid_transcribe`
// is serialized by `CoreMlParakeetEngine`'s `Mutex`, while `ps_fluid_destroy` runs only on Drop.
#[allow(unsafe_code)]
unsafe impl Send for FluidHandle {}

#[allow(unsafe_code)]
impl Drop for FluidHandle {
    fn drop(&mut self) {
        unsafe { ffi::ps_fluid_destroy(self.0.as_ptr()) };
    }
}

#[allow(unsafe_code)]
fn take_bridge_string(ptr: *mut c_char) -> Option<String> {
    if ptr.is_null() {
        return None;
    }
    let text = unsafe { CStr::from_ptr(ptr) }
        .to_string_lossy()
        .into_owned();
    unsafe { ffi::ps_fluid_free_string(ptr) };
    Some(text)
}

/// CoreML / Apple Neural Engine Parakeet TDT offline engine (`parakeet-ultra` and `parakeet-v3`).
pub struct CoreMlParakeetEngine {
    model_id: String,
    handle: Mutex<FluidHandle>,
}

impl CoreMlParakeetEngine {
    /// Load compiled CoreML `.mlmodelc` weights for `spec` from `installed.dir` onto the ANE.
    #[allow(unsafe_code)]
    pub fn load(spec: &ModelSpec, installed: &InstalledModel) -> Result<Self, LocalAsrError> {
        if spec.engine != LocalEngineKind::CoreMlParakeet {
            return Err(LocalAsrError::InvalidModel(format!(
                "model '{}' is not a CoreML Parakeet model (engine: {})",
                spec.id,
                spec.engine.as_str()
            )));
        }

        let version_code = match spec.id {
            "parakeet-ultra" => VERSION_CODE_ULTRA,
            "parakeet-v3" => VERSION_CODE_V3,
            other => {
                return Err(LocalAsrError::InvalidModel(format!(
                    "unsupported CoreML Parakeet model id '{other}'"
                )));
            }
        };

        let dir_str = installed.dir.to_str().ok_or_else(|| {
            LocalAsrError::InvalidModel(format!(
                "model directory path '{}' is not valid UTF-8",
                installed.dir.display()
            ))
        })?;
        let dir_c = CString::new(dir_str).map_err(|_| {
            LocalAsrError::InvalidModel("model directory path contains an interior NUL byte".into())
        })?;

        let mut err_ptr: *mut c_char = ptr::null_mut();
        let raw_handle =
            unsafe { ffi::ps_fluid_init_parakeet(dir_c.as_ptr(), version_code, &mut err_ptr) };
        let err_msg = take_bridge_string(err_ptr);

        let handle = NonNull::new(raw_handle).ok_or_else(|| {
            LocalAsrError::Inference(
                err_msg
                    .unwrap_or_else(|| format!("failed to initialize CoreML model '{}'", spec.id)),
            )
        })?;

        tracing::info!(
            model = spec.id,
            dir = %installed.dir.display(),
            "loaded FluidAudio CoreML/ANE Parakeet engine"
        );

        Ok(Self {
            model_id: spec.id.to_owned(),
            handle: Mutex::new(FluidHandle(handle)),
        })
    }
}

impl LocalAsrEngine for CoreMlParakeetEngine {
    fn model_id(&self) -> &str {
        &self.model_id
    }

    fn backend_name(&self) -> &'static str {
        "fluidaudio-coreml"
    }

    #[allow(unsafe_code)]
    fn transcribe(&self, request: &LocalAsrRequest) -> Result<String, LocalAsrError> {
        if request.cancel_flag.load(Ordering::Acquire) {
            return Err(LocalAsrError::Cancelled);
        }
        if request.samples_16k.is_empty() {
            return Ok(String::new());
        }

        let trimmed = trim_leading_trailing_silence(&request.samples_16k, LOCAL_ASR_SAMPLE_RATE);
        if trimmed.is_empty() {
            return Ok(String::new());
        }

        let sample_count = i32::try_from(trimmed.len()).map_err(|_| {
            LocalAsrError::Inference("audio buffer exceeds maximum CoreML sample count".to_owned())
        })?;

        let guard = self.handle.lock().unwrap_or_else(|e| e.into_inner());
        if request.cancel_flag.load(Ordering::Acquire) {
            return Err(LocalAsrError::Cancelled);
        }

        let mut text_ptr: *mut c_char = ptr::null_mut();
        let mut err_ptr: *mut c_char = ptr::null_mut();
        let rc = unsafe {
            ffi::ps_fluid_transcribe(
                guard.0.as_ptr(),
                trimmed.as_ptr(),
                sample_count,
                &mut text_ptr,
                &mut err_ptr,
            )
        };
        let text = take_bridge_string(text_ptr);
        let err_msg = take_bridge_string(err_ptr);

        if request.cancel_flag.load(Ordering::Acquire) {
            return Err(LocalAsrError::Cancelled);
        }
        if rc != 0 {
            return Err(LocalAsrError::Inference(err_msg.unwrap_or_else(|| {
                format!("FluidAudio transcribe failed (code {rc})")
            })));
        }

        Ok(text.unwrap_or_default().trim().to_owned())
    }
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use super::*;
    use crate::cache::ModelInstallSource;
    use crate::catalog::resolve_model_spec;

    #[test]
    fn load_rejects_missing_directory_cleanly_without_panicking() {
        let spec = resolve_model_spec(Some("parakeet-ultra")).unwrap();
        let installed = InstalledModel {
            model_id: spec.id.to_owned(),
            dir: PathBuf::from("/nonexistent/pulsescribe-coreml-test-dir"),
            source: ModelInstallSource::PulseScribeCache,
            total_bytes: 0,
        };
        let err = match CoreMlParakeetEngine::load(spec, &installed) {
            Ok(_) => panic!("expected load to fail for missing CoreML model dir"),
            Err(err) => err,
        };
        assert!(
            matches!(err, LocalAsrError::Inference(_)),
            "expected Inference error, got {err:?}"
        );
    }
}
