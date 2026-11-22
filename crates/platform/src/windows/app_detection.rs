//! Native Windows frontmost application detector via Win32 (`GetForegroundWindow`,
//! `QueryFullProcessImageNameW`, and `version.dll`).
//!
//! Mirrors `whisper_platform/app_detection.py::WindowsAppDetector`.

use std::collections::HashMap;
use std::ffi::{c_void, OsString};
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::path::Path;
use std::sync::Mutex;

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

/// Detects the active Windows application and caches executable version display labels.
#[derive(Debug, Default)]
pub struct WindowsAppDetector {
    display_name_cache: Mutex<HashMap<String, Option<String>>>,
}

impl WindowsAppDetector {
    pub fn new() -> Self {
        Self::default()
    }

    fn foreground_exe_path() -> Option<String> {
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

    fn process_exe_path(pid: u32) -> Option<String> {
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
                    return Some(OsString::from_wide(slice).to_string_lossy().into_owned());
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

    fn cached_display_name(&self, exe_path: &str) -> Option<String> {
        if let Some(cached) = lock_unpoisoned(&self.display_name_cache).get(exe_path) {
            return cached.clone();
        }
        let resolved = read_executable_display_name(exe_path);
        let mut cache = lock_unpoisoned(&self.display_name_cache);
        if cache.len() >= MAX_DISPLAY_NAME_CACHE_ENTRIES {
            cache.clear();
        }
        cache.insert(exe_path.to_owned(), resolved.clone());
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

fn process_name_from_path(exe_path: &str) -> Option<String> {
    let file_name = Path::new(exe_path).file_name()?.to_str()?;
    let stripped = strip_exe_suffix(file_name);
    (!stripped.is_empty()).then(|| stripped.to_owned())
}

fn to_wide_null(s: &str) -> Vec<u16> {
    std::ffi::OsStr::new(s)
        .encode_wide()
        .chain(std::iter::once(0))
        .collect()
}

/// Reads `FileDescription` (or `ProductName`) from a Windows executable's version resource.
pub(crate) fn read_executable_display_name(exe_path: &str) -> Option<String> {
    if exe_path.is_empty() {
        return None;
    }
    let wide_path = to_wide_null(exe_path);
    #[allow(unsafe_code)]
    // SAFETY: `wide_path` is NUL-terminated UTF-16; `GetFileVersionInfoSizeW` only reads it.
    let size = unsafe { GetFileVersionInfoSizeW(PCWSTR(wide_path.as_ptr()), None) };
    if size == 0 {
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
    let sub_block = to_wide_null("\\VarFileInfo\\Translation");
    let mut ptr: *mut c_void = std::ptr::null_mut();
    let mut byte_len = 0u32;
    #[allow(unsafe_code)]
    // SAFETY: `version_data` holds a valid version info block; `VerQueryValueW` returns a pointer
    // into `version_data` valid for `byte_len` bytes.
    let ok = unsafe {
        VerQueryValueW(
            version_data.as_ptr().cast::<c_void>(),
            PCWSTR(sub_block.as_ptr()),
            &mut ptr,
            &mut byte_len,
        )
    }
    .as_bool();
    if !ok || ptr.is_null() || byte_len < 4 || (byte_len as usize) > version_data.len() {
        return Vec::new();
    }

    let pair_count = (byte_len as usize) / 4;
    let mut codes = Vec::with_capacity(pair_count);
    let words = ptr.cast::<u16>();
    for idx in 0..pair_count {
        #[allow(unsafe_code)]
        // SAFETY: `idx * 2 + 1 < byte_len / 2`; `read_unaligned` handles arbitrary alignment.
        let (lang_id, codepage) = unsafe {
            (
                std::ptr::read_unaligned(words.add(idx * 2)),
                std::ptr::read_unaligned(words.add(idx * 2 + 1)),
            )
        };
        codes.push(format_translation_code(lang_id, codepage));
    }
    codes
}

fn query_version_string(version_data: &[u8], sub_block: &str) -> Option<String> {
    let wide_sub_block = to_wide_null(sub_block);
    let mut ptr: *mut c_void = std::ptr::null_mut();
    let mut char_len = 0u32;
    #[allow(unsafe_code)]
    // SAFETY: `version_data` holds a valid version info block; for string values `VerQueryValueW`
    // writes a pointer to `char_len` UTF-16 code units inside `version_data`.
    let ok = unsafe {
        VerQueryValueW(
            version_data.as_ptr().cast::<c_void>(),
            PCWSTR(wide_sub_block.as_ptr()),
            &mut ptr,
            &mut char_len,
        )
    }
    .as_bool();
    if !ok
        || ptr.is_null()
        || char_len == 0
        || (char_len as usize).saturating_mul(2) > version_data.len()
    {
        return None;
    }

    let words = ptr.cast::<u16>();
    let mut units = Vec::with_capacity(char_len as usize);
    for idx in 0..(char_len as usize) {
        #[allow(unsafe_code)]
        // SAFETY: `idx < char_len`, staying inside `version_data`'s string table buffer.
        let unit = unsafe { std::ptr::read_unaligned(words.add(idx)) };
        if unit == 0 {
            break;
        }
        units.push(unit);
    }
    Some(String::from_utf16_lossy(&units))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn process_name_from_path_strips_exe_suffix() {
        assert_eq!(
            process_name_from_path(r"C:\Program Files\Microsoft Office\WINWORD.EXE").as_deref(),
            Some("WINWORD")
        );
        assert_eq!(
            process_name_from_path(r"C:\Tools\Code.exe").as_deref(),
            Some("Code")
        );
    }

    #[test]
    fn current_process_exe_path_and_detector_run_safely() {
        let exe = WindowsAppDetector::process_exe_path(std::process::id())
            .expect("current test process image path should resolve");
        assert!(!exe.is_empty());

        let detector = WindowsAppDetector::new();
        let _ = detector.frontmost_app();
        let _ = detector.frontmost_app_label();

        let cmd_exe = r"C:\Windows\System32\cmd.exe";
        if Path::new(cmd_exe).exists() {
            let label = read_executable_display_name(cmd_exe)
                .expect("cmd.exe version resource should expose a display label");
            assert!(!label.is_empty());
        }
    }
}
