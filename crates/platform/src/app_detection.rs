//! Frontmost application detection trait and Windows executable name helpers.
//!
//! Mirrors `whisper_platform/app_detection.py`.

/// Fallback en-US `StringFileInfo` translation codes when a Windows executable's
/// version resource lists no explicit `\VarFileInfo\Translation` table.
pub const VERSION_FALLBACK_CODES: [&str; 2] = ["040904b0", "040904e4"];

/// Detects the currently focused application for LLM context routing and DONE labels.
pub trait AppDetector: Send + Sync {
    /// Context-matching identifier of the active application (e.g. `"Slack"`, `"WINWORD"`).
    ///
    /// On Windows, implementations strip the `.exe` suffix via [`strip_exe_suffix`].
    fn frontmost_app(&self) -> Option<String>;

    /// Human-readable display label of the active application (e.g. `"Microsoft Word"`).
    ///
    /// Defaults to [`Self::frontmost_app`], which is already readable on macOS.
    fn frontmost_app_label(&self) -> Option<String> {
        self.frontmost_app()
    }
}

/// No-op detector used when foreground inspection is unavailable.
#[derive(Debug, Default, Clone, Copy)]
pub struct NoopAppDetector;

impl AppDetector for NoopAppDetector {
    fn frontmost_app(&self) -> Option<String> {
        None
    }
}

/// Strips a trailing ASCII `.exe` suffix (case-insensitive) from a process name.
///
/// Mirrors `whisper_platform/app_detection.py::_strip_exe`.
pub fn strip_exe_suffix(name: &str) -> &str {
    let suffix_len = 4;
    if name.len() >= suffix_len {
        let split = name.len() - suffix_len;
        if name.is_char_boundary(split) && name[split..].eq_ignore_ascii_case(".exe") {
            return &name[..split];
        }
    }
    name
}

/// Formats a Windows `LANGANDCODEPAGE` pair as an 8-hex-digit `StringFileInfo` table key.
pub fn format_translation_code(lang_id: u16, codepage: u16) -> String {
    format!("{lang_id:04x}{codepage:04x}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strip_exe_suffix_handles_case_and_unicode() {
        assert_eq!(strip_exe_suffix("WINWORD.EXE"), "WINWORD");
        assert_eq!(strip_exe_suffix("slack.exe"), "slack");
        assert_eq!(strip_exe_suffix("Code.Exe"), "Code");
        assert_eq!(strip_exe_suffix("Slack"), "Slack");
        assert_eq!(strip_exe_suffix(".exe"), "");
        assert_eq!(strip_exe_suffix("exe"), "exe");
        assert_eq!(strip_exe_suffix("Büro.EXE"), "Büro");
        assert_eq!(strip_exe_suffix("äöü"), "äöü");
    }

    #[test]
    fn translation_code_formats_zero_padded_hex() {
        assert_eq!(format_translation_code(0x0409, 0x04b0), "040904b0");
        assert_eq!(format_translation_code(0x0407, 0x04e4), "040704e4");
        assert_eq!(VERSION_FALLBACK_CODES, ["040904b0", "040904e4"]);
    }
}
