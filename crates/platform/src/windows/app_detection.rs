//! Native Windows frontmost application detector via Win32 (`GetForegroundWindow`,
//! `QueryFullProcessImageNameW`, and `version.dll`).
//!
//! Mirrors `whisper_platform/app_detection.py::WindowsAppDetector`.

use std::collections::HashMap;
use std::ffi::{c_void, OsStr, OsString};
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::path::Path;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use pulsescribe_core::paths::{build_file_signature, FileSignature};
use windows::core::{PCWSTR, PWSTR};
use windows::Win32::Foundation::{CloseHandle, ERROR_INSUFFICIENT_BUFFER, HANDLE};
use windows::Win32::Storage::FileSystem::{
    GetFileVersionInfoSizeW, GetFileVersionInfoW, VerQueryValueW,
};
use windows::Win32::System::Threading::{
    OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_WIN32, PROCESS_QUERY_LIMITED_INFORMATION,
};
use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, GetWindowThreadProcessId};

use crate::{
    format_translation_code, lock_unpoisoned, strip_exe_suffix, AppDetector, VERSION_FALLBACK_CODES,
};

const MAX_DISPLAY_NAME_CACHE_ENTRIES: usize = 64;
const FAILED_DISPLAY_NAME_TTL: Duration = Duration::from_secs(10);
const INITIAL_EXE_PATH_CAP: usize = 512;
const MAX_EXE_PATH_CAP: usize = 32_768;

struct OwnedHandle(HANDLE);

impl Drop for OwnedHandle {
    fn drop(&mut self) {
        if !self.0.is_invalid() {
            #[allow(unsafe_code)]
            // SAFETY: `self.0` was opened via `OpenProcess` and is closed exactly once.
            unsafe {
                let _ = CloseHandle(self.0);
            }
        }
    }
}

#[derive(Debug, Clone)]
struct CachedDisplayName {
    label: Option<String>,
    signature: Option<FileSignature>,
    cached_at: Instant,
}

/// Detects the active Windows application and caches executable version display labels.
#[derive(Debug, Default)]
pub struct WindowsAppDetector {
    display_name_cache: Mutex<HashMap<OsString, CachedDisplayName>>,
}

impl WindowsAppDetector {
    pub fn new() -> Self {
        Self::default()
    }

    fn foreground_exe_path() -> Option<OsString> {
        #[allow(unsafe_code)]
        // SAFETY: `GetForegroundWindow` and `GetWindowThreadProcessId` take valid pointers.
        let pid = unsafe {
            let hwnd = GetForegroundWindow();
            if hwnd.0.is_null() {
                return None;
            }
            let mut pid = 0u32;
            GetWindowThreadProcessId(hwnd, Some(&mut pid));
            pid
        };
        if pid == 0 {
            return None;
        }
        Self::process_exe_path(pid)
    }

    fn process_exe_path(pid: u32) -> Option<OsString> {
        #[allow(unsafe_code)]
        // SAFETY: `OpenProcess` returns an owned `HANDLE` closed via `OwnedHandle::drop`.
        let handle = OwnedHandle(
            unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) }.ok()?,
        );

        let mut cap = INITIAL_EXE_PATH_CAP;
        loop {
            let mut buf = vec![0u16; cap];
            let mut len = buf.len() as u32;
            #[allow(unsafe_code)]
            // SAFETY: `buf` is writable for `len` UTF-16 code units; `handle.0` is open.
            let res = unsafe {
                QueryFullProcessImageNameW(
                    handle.0,
                    PROCESS_NAME_WIN32,
                    PWSTR(buf.as_mut_ptr()),
                    &mut len,
                )
            };
            match res {
                Ok(()) => {
                    let slice = &buf[..usize::min(len as usize, buf.len())];
                    return Some(OsString::from_wide(slice));
                }
                Err(err)
                    if err.code() == ERROR_INSUFFICIENT_BUFFER.to_hresult()
                        && cap < MAX_EXE_PATH_CAP =>
                {
                    cap = (cap * 2).min(MAX_EXE_PATH_CAP);
                }
                Err(_) => return None,
            }
        }
    }

    fn cached_display_name(&self, exe_path: &OsStr) -> Option<String> {
        let now = Instant::now();
        let current_sig = build_file_signature(Path::new(exe_path)).ok();
        if let Some(entry) = lock_unpoisoned(&self.display_name_cache).get(exe_path) {
            let sig_matches = entry.signature == current_sig;
            let valid = match &entry.label {
                Some(_) => entry.signature.is_some() && sig_matches,
                None => {
                    sig_matches
                        && now.saturating_duration_since(entry.cached_at) < FAILED_DISPLAY_NAME_TTL
                }
            };
            if valid {
                return entry.label.clone();
            }
        }
        let resolved = read_executable_display_name(exe_path);
        let mut cache = lock_unpoisoned(&self.display_name_cache);
        if cache.len() >= MAX_DISPLAY_NAME_CACHE_ENTRIES && !cache.contains_key(exe_path) {
            cache.clear();
        }
        cache.insert(
            exe_path.to_os_string(),
            CachedDisplayName {
                label: resolved.clone(),
                signature: current_sig,
                cached_at: now,
            },
        );
        resolved
    }
}

impl AppDetector for WindowsAppDetector {
    fn frontmost_app(&self) -> Option<String> {
        let exe_path = Self::foreground_exe_path()?;
        process_name_from_path(&exe_path)
    }

    fn frontmost_app_label(&self) -> Option<String> {
        let exe_path = Self::foreground_exe_path()?;
        let process_name = process_name_from_path(&exe_path)?;
        self.cached_display_name(&exe_path).or(Some(process_name))
    }
}

fn process_name_from_path(exe_path: &OsStr) -> Option<String> {
    let file_name = Path::new(exe_path).file_name()?.to_string_lossy();
    let stripped = strip_exe_suffix(&file_name);
    (!stripped.is_empty()).then(|| stripped.to_owned())
}

fn os_str_to_wide_null(s: &OsStr) -> Vec<u16> {
    s.encode_wide().chain(std::iter::once(0)).collect()
}

fn str_to_wide_null(s: &str) -> Vec<u16> {
    os_str_to_wide_null(OsStr::new(s))
}

/// Validates that a pointer/length pair returned by `VerQueryValueW` lies completely within
/// `version_data` before any read occurs.
fn checked_version_span(
    version_data: &[u8],
    ptr: *const c_void,
    span_bytes: usize,
) -> Option<&[u8]> {
    if ptr.is_null() || span_bytes == 0 {
        return None;
    }
    let base = version_data.as_ptr() as usize;
    let offset = (ptr as usize).checked_sub(base)?;
    let end = offset.checked_add(span_bytes)?;
    version_data.get(offset..end)
}

/// Reads `FileDescription` (or `ProductName`) from a Windows executable's version resource.
pub(crate) fn read_executable_display_name(exe_path: &OsStr) -> Option<String> {
    if exe_path.is_empty() {
        return None;
    }
    let wide_path = os_str_to_wide_null(exe_path);
    #[allow(unsafe_code)]
    // SAFETY: `wide_path` is NUL-terminated UTF-16; `GetFileVersionInfoSizeW` only reads it.
    let size = unsafe { GetFileVersionInfoSizeW(PCWSTR(wide_path.as_ptr()), None) };
    if size < 2 {
        return None;
    }

    let mut data = vec![0u8; size as usize];
    #[allow(unsafe_code)]
    // SAFETY: `data` is writable for `size` bytes matching `GetFileVersionInfoSizeW`.
    if unsafe {
        GetFileVersionInfoW(
            PCWSTR(wide_path.as_ptr()),
            None,
            size,
            data.as_mut_ptr().cast::<c_void>(),
        )
    }
    .is_err()
    {
        return None;
    }

    // Validate the leading `VS_VERSIONINFO.wLength` header so truncated version blocks
    // (e.g. from an in-place file replacement between Size and Read) are rejected before
    // `VerQueryValueW` traverses internal offsets.
    let declared_len = usize::from(u16::from_le_bytes([data[0], data[1]]));
    if declared_len < 6 || declared_len > data.len() {
        return None;
    }

    let mut codes = query_translation_codes(&data);
    for fallback in VERSION_FALLBACK_CODES {
        if !codes.iter().any(|c| c == fallback) {
            codes.push(fallback.to_owned());
        }
    }

    for key in ["FileDescription", "ProductName"] {
        for code in &codes {
            let sub_block = format!("\\StringFileInfo\\{code}\\{key}");
            if let Some(value) = query_version_string(&data, &sub_block) {
                let trimmed = value.trim();
                if !trimmed.is_empty() {
                    return Some(trimmed.to_owned());
                }
            }
        }
    }
    None
}

fn query_translation_codes(version_data: &[u8]) -> Vec<String> {
    let sub_block = str_to_wide_null("\\VarFileInfo\\Translation");
    let mut ptr: *mut c_void = std::ptr::null_mut();
    let mut byte_len = 0u32;
    #[allow(unsafe_code)]
    // SAFETY: `version_data` holds a validated version info block; `VerQueryValueW` returns a
    // pointer into `version_data` whose bounds are verified below via `checked_version_span`.
    let ok = unsafe {
        VerQueryValueW(
            version_data.as_ptr().cast::<c_void>(),
            PCWSTR(sub_block.as_ptr()),
            &mut ptr,
            &mut byte_len,
        )
    }
    .as_bool();
    if !ok || byte_len < 4 {
        return Vec::new();
    }

    let Some(span) = checked_version_span(version_data, ptr.cast_const(), byte_len as usize) else {
        return Vec::new();
    };
    span.as_chunks::<4>()
        .0
        .iter()
        .map(|&[b0, b1, b2, b3]| {
            let lang_id = u16::from_le_bytes([b0, b1]);
            let codepage = u16::from_le_bytes([b2, b3]);
            format_translation_code(lang_id, codepage)
        })
        .collect()
}

fn query_version_string(version_data: &[u8], sub_block: &str) -> Option<String> {
    let wide_sub_block = str_to_wide_null(sub_block);
    let mut ptr: *mut c_void = std::ptr::null_mut();
    let mut char_len = 0u32;
    #[allow(unsafe_code)]
    // SAFETY: `version_data` holds a validated version info block; `VerQueryValueW` returns a
    // pointer into `version_data` whose UTF-16 byte span is verified below.
    let ok = unsafe {
        VerQueryValueW(
            version_data.as_ptr().cast::<c_void>(),
            PCWSTR(wide_sub_block.as_ptr()),
            &mut ptr,
            &mut char_len,
        )
    }
    .as_bool();
    if !ok || char_len == 0 {
        return None;
    }

    let byte_span = (char_len as usize).checked_mul(2)?;
    let span = checked_version_span(version_data, ptr.cast_const(), byte_span)?;
    let units: Vec<u16> = span
        .as_chunks::<2>()
        .0
        .iter()
        .map(|&pair| u16::from_le_bytes(pair))
        .take_while(|&unit| unit != 0)
        .collect();
    Some(String::from_utf16_lossy(&units))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn process_name_from_path_strips_exe_suffix() {
        assert_eq!(
            process_name_from_path(OsStr::new(r"C:\Program Files\Microsoft Office\WINWORD.EXE"))
                .as_deref(),
            Some("WINWORD")
        );
        assert_eq!(
            process_name_from_path(OsStr::new(r"C:\Tools\Code.exe")).as_deref(),
            Some("Code")
        );
    }

    #[test]
    fn checked_version_span_rejects_out_of_bounds_and_offset_overflows() {
        let buf = [1u8, 2, 3, 4, 5, 6, 7, 8];
        let start = buf.as_ptr().cast::<c_void>();
        assert_eq!(checked_version_span(&buf, start, 4), Some(&buf[0..4]));
        let mid = buf[4..].as_ptr().cast::<c_void>();
        assert_eq!(checked_version_span(&buf, mid, 4), Some(&buf[4..8]));
        assert_eq!(checked_version_span(&buf, mid, 5), None);
        assert_eq!(checked_version_span(&buf, std::ptr::null(), 2), None);
    }

    #[test]
    fn current_process_exe_path_and_detector_run_safely() {
        let exe = WindowsAppDetector::process_exe_path(std::process::id())
            .expect("current test process image path should resolve");
        assert!(!exe.is_empty());

        let detector = WindowsAppDetector::new();
        let _ = detector.frontmost_app();
        let _ = detector.frontmost_app_label();

        let cmd_exe = OsStr::new(r"C:\Windows\System32\cmd.exe");
        if Path::new(cmd_exe).exists() {
            let label = read_executable_display_name(cmd_exe)
                .expect("cmd.exe version resource should expose a display label");
            assert!(!label.is_empty());
        }
    }
}
