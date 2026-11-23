//! Per-user Launch-at-Login / Autostart management for macOS and Windows.
//!
//! - **macOS:** Manages `~/Library/LaunchAgents/com.kliebhan.pulsescribe.plist` atomically and
//!   refuses executables located inside macOS TCC-protected folders (`Documents`, `Desktop`,
//!   `Downloads`) so launchd never stalls TCC prompts across the machine.
//! - **Windows:** Manages `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value
//!   `"PulseScribe"` (identical key/name to legacy Inno Setup so upgrading replaces stale entries
//!   instead of duplicating them).

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::PlatformError;

/// LaunchAgent bundle label on macOS.
pub const MACOS_LAUNCH_AGENT_LABEL: &str = "com.kliebhan.pulsescribe";

/// Per-user Windows Run registry key (matches legacy `installer_windows.iss`).
pub const WINDOWS_RUN_KEY: &str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run";

/// Per-user Windows Run registry value name (matches legacy `installer_windows.iss`).
pub const WINDOWS_RUN_VALUE: &str = "PulseScribe";

/// Current autostart availability and toggle state exposed to the desktop UI.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AutostartStatus {
    pub supported: bool,
    pub enabled: bool,
    pub detail: Option<String>,
}

/// Escape XML special characters for safe LaunchAgent plist generation.
pub fn escape_xml(raw: &str) -> String {
    let mut out = String::with_capacity(raw.len());
    for ch in raw.chars() {
        match ch {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&apos;"),
            _ => out.push(ch),
        }
    }
    out
}

/// Render a per-user macOS LaunchAgent `.plist` launching `exe_path` at login.
pub fn render_macos_launch_agent_plist(label: &str, exe_path: &Path) -> String {
    let escaped_label = escape_xml(label);
    let escaped_exe = escape_xml(&exe_path.to_string_lossy());
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{escaped_label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{escaped_exe}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
  <key>ProcessType</key>
  <string>Interactive</string>
</dict>
</plist>
"#
    )
}

/// Canonicalize the deepest existing ancestor of `path` and re-append any non-existent suffix.
///
/// Prevents symlink mismatches (such as macOS `/var/folders` -> `/private/var/folders`) when
/// checking whether a not-yet-created executable path falls under a canonicalized `home_dir`.
fn normalize_path_for_prefix(path: &Path) -> PathBuf {
    let mut existing = path;
    let mut tail = Vec::new();
    while !existing.exists() {
        let Some(name) = existing.file_name() else {
            return path.to_path_buf();
        };
        tail.push(name.to_os_string());
        match existing.parent() {
            Some(parent) if !parent.as_os_str().is_empty() => existing = parent,
            _ => return path.to_path_buf(),
        }
    }
    let mut resolved = std::fs::canonicalize(existing).unwrap_or_else(|_| existing.to_path_buf());
    for part in tail.into_iter().rev() {
        resolved.push(part);
    }
    resolved
}

/// Return `true` when `exe_path` resides inside a macOS TCC-protected user directory
/// (`~/Documents`, `~/Desktop`, or `~/Downloads`), where launchd services can stall TCC prompts.
pub fn is_macos_tcc_protected_dir(exe_path: &Path, home_dir: &Path) -> bool {
    let resolved_exe = normalize_path_for_prefix(exe_path);
    let resolved_home = normalize_path_for_prefix(home_dir);
    ["Documents", "Desktop", "Downloads"]
        .iter()
        .any(|folder| resolved_exe.starts_with(resolved_home.join(folder)))
}

/// Compute `~/Library/LaunchAgents/com.kliebhan.pulsescribe.plist` beneath `home_dir`.
pub fn macos_launch_agent_path(home_dir: &Path) -> PathBuf {
    home_dir
        .join("Library")
        .join("LaunchAgents")
        .join(format!("{MACOS_LAUNCH_AGENT_LABEL}.plist"))
}

/// Pure testable macOS LaunchAgent query against an explicit `home_dir`.
pub fn get_macos_autostart_in(home_dir: &Path) -> AutostartStatus {
    let plist_path = macos_launch_agent_path(home_dir);
    let enabled = plist_path.is_file();
    AutostartStatus {
        supported: true,
        enabled,
        detail: Some(plist_path.display().to_string()),
    }
}

/// Pure testable macOS LaunchAgent toggle against explicit `home_dir` and `exe_path`.
pub fn set_macos_autostart_in(
    home_dir: &Path,
    exe_path: &Path,
    enabled: bool,
) -> Result<AutostartStatus, PlatformError> {
    let plist_path = macos_launch_agent_path(home_dir);
    if enabled {
        if is_macos_tcc_protected_dir(exe_path, home_dir) {
            return Err(PlatformError::Autostart(
                "Move PulseScribe.app to /Applications before enabling Launch at Login (macOS TCC restricts LaunchAgents inside Documents, Desktop, or Downloads).".to_owned(),
            ));
        }
        if let Some(parent) = plist_path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let plist = render_macos_launch_agent_plist(MACOS_LAUNCH_AGENT_LABEL, exe_path);
        pulsescribe_core::atomic_io::write_text_atomic_restricted(&plist_path, &plist)?;
    } else if let Err(err) = std::fs::remove_file(&plist_path) {
        if err.kind() != std::io::ErrorKind::NotFound {
            return Err(PlatformError::Io(err));
        }
    }
    Ok(get_macos_autostart_in(home_dir))
}

/// Query the current OS autostart state for PulseScribe.
pub fn get_autostart_status() -> AutostartStatus {
    #[cfg(target_os = "macos")]
    {
        match std::env::var_os("HOME").map(PathBuf::from) {
            Some(home) => get_macos_autostart_in(&home),
            None => AutostartStatus {
                supported: false,
                enabled: false,
                detail: Some("HOME directory unavailable".to_owned()),
            },
        }
    }
    #[cfg(target_os = "windows")]
    {
        windows_imp::get_status()
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        AutostartStatus {
            supported: false,
            enabled: false,
            detail: Some("Launch at Login is currently supported on macOS and Windows.".to_owned()),
        }
    }
}

/// Enable or disable OS autostart for the currently running PulseScribe executable.
pub fn set_autostart_enabled(enabled: bool) -> Result<AutostartStatus, PlatformError> {
    #[cfg(target_os = "macos")]
    {
        let home = std::env::var_os("HOME")
            .map(PathBuf::from)
            .ok_or_else(|| PlatformError::Autostart("HOME directory unavailable".to_owned()))?;
        let exe = std::env::current_exe()?;
        set_macos_autostart_in(&home, &exe, enabled)
    }
    #[cfg(target_os = "windows")]
    {
        let exe = std::env::current_exe()?;
        windows_imp::set_enabled(&exe, enabled)
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = enabled;
        Err(PlatformError::Unavailable(
            "Launch at Login is only supported on macOS and Windows",
        ))
    }
}

#[cfg(target_os = "windows")]
mod windows_imp {
    use std::os::windows::process::CommandExt;
    use std::path::Path;
    use std::process::Command;

    use super::{AutostartStatus, WINDOWS_RUN_KEY, WINDOWS_RUN_VALUE};
    use crate::PlatformError;

    const CREATE_NO_WINDOW: u32 = 0x0800_0000;

    fn reg_command() -> Command {
        let mut cmd = Command::new("reg.exe");
        cmd.creation_flags(CREATE_NO_WINDOW);
        cmd
    }

    pub fn get_status() -> AutostartStatus {
        let output = reg_command()
            .args(["query", WINDOWS_RUN_KEY, "/v", WINDOWS_RUN_VALUE])
            .output();
        match output {
            Ok(out) => AutostartStatus {
                supported: true,
                enabled: out.status.success(),
                detail: Some(format!(r"{WINDOWS_RUN_KEY}\{WINDOWS_RUN_VALUE}")),
            },
            Err(err) => AutostartStatus {
                supported: false,
                enabled: false,
                detail: Some(format!("Registry query failed: {err}")),
            },
        }
    }

    pub fn set_enabled(exe_path: &Path, enabled: bool) -> Result<AutostartStatus, PlatformError> {
        if enabled {
            let exe_str = exe_path.to_str().ok_or_else(|| {
                PlatformError::Autostart("Executable path is not valid UTF-8".to_owned())
            })?;
            if exe_str.contains('"') || exe_str.chars().any(|c| c.is_control()) {
                return Err(PlatformError::Autostart(
                    "Executable path contains invalid characters".to_owned(),
                ));
            }
            let quoted = format!("\"{exe_str}\"");
            let out = reg_command()
                .args([
                    "add",
                    WINDOWS_RUN_KEY,
                    "/v",
                    WINDOWS_RUN_VALUE,
                    "/t",
                    "REG_SZ",
                    "/d",
                    &quoted,
                    "/f",
                ])
                .output()?;
            if !out.status.success() {
                let stderr = String::from_utf8_lossy(&out.stderr);
                return Err(PlatformError::Autostart(format!(
                    "Failed to write autostart registry value: {}",
                    stderr.trim()
                )));
            }
        } else {
            let out = reg_command()
                .args(["delete", WINDOWS_RUN_KEY, "/v", WINDOWS_RUN_VALUE, "/f"])
                .output()?;
            // Exit code 1 occurs when the value is already absent; verify final state below.
            if !out.status.success() && get_status().enabled {
                let stderr = String::from_utf8_lossy(&out.stderr);
                return Err(PlatformError::Autostart(format!(
                    "Failed to remove autostart registry value: {}",
                    stderr.trim()
                )));
            }
        }
        Ok(get_status())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_home(tag: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir =
            std::env::temp_dir().join(format!("ps-autostart-{tag}-{}-{nanos}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn plist_escapes_xml_special_characters() {
        let plist = render_macos_launch_agent_plist(
            MACOS_LAUNCH_AGENT_LABEL,
            Path::new("/Applications/Pulse & <Scribe>.app/Contents/MacOS/pulsescribe-desktop"),
        );
        assert!(plist.contains("<string>com.kliebhan.pulsescribe</string>"));
        assert!(plist.contains("Pulse &amp; &lt;Scribe&gt;.app"));
        assert!(plist.contains("<key>RunAtLoad</key>\n  <true/>"));
        assert!(plist.contains("<key>KeepAlive</key>\n  <false/>"));
    }

    #[test]
    fn macos_tcc_guard_rejects_documents_desktop_downloads_and_allows_applications() {
        let home = temp_home("tcc");
        let docs_exe = home.join("Documents/PulseScribe.app/Contents/MacOS/pulsescribe-desktop");
        let desktop_exe = home.join("Desktop/pulsescribe-desktop");
        let downloads_exe = home.join("Downloads/pulsescribe-desktop");
        let app_exe =
            PathBuf::from("/Applications/PulseScribe.app/Contents/MacOS/pulsescribe-desktop");
        let workspace_exe = home.join("workspace/pulsescribe-desktop");

        assert!(is_macos_tcc_protected_dir(&docs_exe, &home));
        assert!(is_macos_tcc_protected_dir(&desktop_exe, &home));
        assert!(is_macos_tcc_protected_dir(&downloads_exe, &home));
        assert!(!is_macos_tcc_protected_dir(&app_exe, &home));
        assert!(!is_macos_tcc_protected_dir(&workspace_exe, &home));

        let err = set_macos_autostart_in(&home, &downloads_exe, true).unwrap_err();
        assert!(err.to_string().contains("/Applications"));

        let _ = fs::remove_dir_all(&home);
    }

    #[test]
    fn macos_launch_agent_toggle_writes_and_removes_plist_cleanly() {
        let home = temp_home("toggle");
        let exe = Path::new("/Applications/PulseScribe.app/Contents/MacOS/pulsescribe-desktop");

        assert!(!get_macos_autostart_in(&home).enabled);

        let enabled = set_macos_autostart_in(&home, exe, true).unwrap();
        assert!(enabled.supported);
        assert!(enabled.enabled);
        let plist_path = macos_launch_agent_path(&home);
        let body = fs::read_to_string(&plist_path).unwrap();
        assert!(body.contains("/Applications/PulseScribe.app/Contents/MacOS/pulsescribe-desktop"));

        let disabled = set_macos_autostart_in(&home, exe, false).unwrap();
        assert!(!disabled.enabled);
        assert!(!plist_path.exists());

        // Disabling again when already absent stays idempotent.
        assert!(!set_macos_autostart_in(&home, exe, false).unwrap().enabled);

        let _ = fs::remove_dir_all(&home);
    }
}
