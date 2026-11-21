//! Platform-independent hotkey parsing, normalization, conflict detection, and validation.
//!
//! Mirrors `utils/hotkey.py` (macOS Carbon key-code parsing and modifier-subset
//! conflicts), `utils/hotkey_windows.py` (canonical Windows hotkey normalization
//! and token-subset conflicts), and `utils/hotkey_validation.py` (UI-facing hotkey
//! change validation with injected OS probes).

use std::collections::HashSet;

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::preferences::HotkeyKind;

/// Carbon modifier mask bits matching `utils/hotkey.py:MODIFIER_MAP`.
pub const CARBON_CMD_KEY: u32 = 256;
pub const CARBON_SHIFT_KEY: u32 = 512;
pub const CARBON_OPTION_KEY: u32 = 2048;
pub const CARBON_CONTROL_KEY: u32 = 4096;

const WINDOWS_MODIFIER_ORDER: [&str; 4] = ["ctrl", "alt", "shift", "win"];

/// Parsed macOS Carbon hotkey `(virtual_key, modifier_mask)`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct CarbonHotkey {
    pub virtual_key: u16,
    pub modifier_mask: u32,
}

/// Parse error produced by `parse_macos_hotkey`, matching Python's `ValueError` wording.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum HotkeyParseError {
    #[error("Unknown key: {0}")]
    UnknownKey(String),
    #[error("Unknown modifier: {0}")]
    UnknownModifier(String),
}

/// Severity level returned by `validate_macos_hotkey_change`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HotkeyValidationLevel {
    Ok,
    Warning,
    Error,
}

impl HotkeyValidationLevel {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Ok => "ok",
            Self::Warning => "warning",
            Self::Error => "error",
        }
    }
}

/// Result of validating a proposed hotkey change for Settings or Onboarding.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct HotkeyValidationResult {
    pub normalized: String,
    pub level: HotkeyValidationLevel,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
}

impl HotkeyValidationResult {
    fn ok(normalized: String) -> Self {
        Self {
            normalized,
            level: HotkeyValidationLevel::Ok,
            message: None,
        }
    }

    fn warning(normalized: String, message: impl Into<String>) -> Self {
        Self {
            normalized,
            level: HotkeyValidationLevel::Warning,
            message: Some(message.into()),
        }
    }

    fn error(normalized: String, message: impl Into<String>) -> Self {
        Self {
            normalized,
            level: HotkeyValidationLevel::Error,
            message: Some(message.into()),
        }
    }
}

/// Injected state and permissions required to validate a macOS hotkey change
/// without binding `pulsescribe-core` directly to OS frameworks.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct HotkeyValidationContext {
    pub toggle_current: String,
    pub hold_current: String,
    /// Skip conflict check against the opposite slot (used by the setup wizard
    /// when replacing the other slot in the same save).
    pub replace_other: bool,
    pub input_monitoring_allowed: bool,
}

/// Map a lowercased macOS key token to its Carbon virtual key code.
pub fn macos_virtual_key_code(key: &str) -> Option<u16> {
    let code = match key {
        // Function keys
        "f1" => 122,
        "f2" => 120,
        "f3" => 99,
        "f4" => 118,
        "f5" => 96,
        "f6" => 97,
        "f7" => 98,
        "f8" => 100,
        "f9" => 101,
        "f10" => 109,
        "f11" => 103,
        "f12" => 111,
        "f13" => 105,
        "f14" => 107,
        "f15" => 113,
        "f16" => 106,
        "f17" => 64,
        "f18" => 79,
        "f19" => 80,
        "f20" => 90,
        // Fn / Globe key (macOS)
        "fn" => 63,
        // Letters (ANSI)
        "a" => 0,
        "b" => 11,
        "c" => 8,
        "d" => 2,
        "e" => 14,
        "f" => 3,
        "g" => 5,
        "h" => 4,
        "i" => 34,
        "j" => 38,
        "k" => 40,
        "l" => 37,
        "m" => 46,
        "n" => 45,
        "o" => 31,
        "p" => 35,
        "q" => 12,
        "r" => 15,
        "s" => 1,
        "t" => 17,
        "u" => 32,
        "v" => 9,
        "w" => 13,
        "x" => 7,
        "y" => 16,
        "z" => 6,
        // Digits (ANSI)
        "0" => 29,
        "1" => 18,
        "2" => 19,
        "3" => 20,
        "4" => 21,
        "5" => 23,
        "6" => 22,
        "7" => 26,
        "8" => 28,
        "9" => 25,
        // Punctuation / symbols (ANSI)
        "." => 47,
        "," => 43,
        "/" => 44,
        "\\" => 42,
        ";" => 41,
        "'" => 39,
        "`" => 50,
        "-" => 27,
        "=" => 24,
        "[" => 33,
        "]" => 30,
        // Special keys
        "space" => 49,
        "return" | "enter" => 36,
        "tab" => 48,
        "escape" | "esc" => 53,
        "delete" | "backspace" => 51,
        "forwarddelete" => 117,
        // Arrow keys
        "up" => 126,
        "down" => 125,
        "left" => 123,
        "right" => 124,
        // Navigation
        "home" => 115,
        "end" => 119,
        "pageup" => 116,
        "pagedown" => 121,
        // CapsLock
        "capslock" | "caps_lock" => 57,
        _ => return None,
    };
    Some(code)
}

/// Map a lowercased macOS modifier token to its Carbon modifier mask bit.
pub fn macos_modifier_mask(modifier: &str) -> Option<u32> {
    let mask = match modifier {
        "cmd" | "command" => CARBON_CMD_KEY,
        "shift" => CARBON_SHIFT_KEY,
        "alt" | "option" => CARBON_OPTION_KEY,
        "ctrl" | "control" => CARBON_CONTROL_KEY,
        _ => return None,
    };
    Some(mask)
}

/// Parse a macOS hotkey string (e.g. `"f19"`, `"cmd+shift+r"`) into `(virtual_key, modifier_mask)`.
pub fn parse_macos_hotkey(hotkey_str: &str) -> Result<CarbonHotkey, HotkeyParseError> {
    let normalized = hotkey_str.trim().to_lowercase();
    let parts: Vec<&str> = normalized.split('+').map(str::trim).collect();

    if parts.len() == 1 {
        let key = parts[0];
        let virtual_key = macos_virtual_key_code(key)
            .ok_or_else(|| HotkeyParseError::UnknownKey(key.to_string()))?;
        return Ok(CarbonHotkey {
            virtual_key,
            modifier_mask: 0,
        });
    }

    let (&key, modifiers) = parts
        .split_last()
        .expect("split('+') always yields at least one part");

    // Check key before modifiers to match `utils/hotkey.py:parse_hotkey` error precedence.
    let virtual_key =
        macos_virtual_key_code(key).ok_or_else(|| HotkeyParseError::UnknownKey(key.to_string()))?;

    let mut modifier_mask = 0u32;
    for &modifier in modifiers {
        let bit = macos_modifier_mask(modifier)
            .ok_or_else(|| HotkeyParseError::UnknownModifier(modifier.to_string()))?;
        modifier_mask |= bit;
    }

    Ok(CarbonHotkey {
        virtual_key,
        modifier_mask,
    })
}

/// Return `true` when two macOS hotkeys share a physical key and one modifier set
/// is a subset of the other (e.g. `"space"` and `"cmd+space"`).
pub fn macos_hotkeys_conflict(hotkey_a: Option<&str>, hotkey_b: Option<&str>) -> bool {
    let parse_opt = |raw: Option<&str>| -> Option<CarbonHotkey> {
        let normalized = raw.unwrap_or("").trim().to_lowercase();
        if normalized.is_empty() {
            return None;
        }
        parse_macos_hotkey(&normalized).ok()
    };

    let (Some(a), Some(b)) = (parse_opt(hotkey_a), parse_opt(hotkey_b)) else {
        return false;
    };

    if a.virtual_key != b.virtual_key {
        return false;
    }

    let common = a.modifier_mask & b.modifier_mask;
    common == a.modifier_mask || common == b.modifier_mask
}

/// Return whether a macOS hotkey requires Quartz Input Monitoring permission.
///
/// Hold hotkeys always require Input Monitoring; special toggle keys (`fn`, `capslock`,
/// `caps_lock`) do as well because Carbon cannot register them globally.
pub fn requires_input_monitoring(kind: HotkeyKind, normalized_hotkey: &str) -> bool {
    kind == HotkeyKind::Hold || matches!(normalized_hotkey, "fn" | "capslock" | "caps_lock")
}

/// Validate a proposed macOS hotkey change using injected current state and a Carbon
/// global-registration probe closure (mirrors `utils/hotkey_validation.py`).
pub fn validate_macos_hotkey_change<F>(
    kind: HotkeyKind,
    hotkey_str: Option<&str>,
    ctx: &HotkeyValidationContext,
    probe_carbon_registration: F,
) -> HotkeyValidationResult
where
    F: FnOnce(CarbonHotkey) -> bool,
{
    let normalized = normalize_hotkey_input(hotkey_str);
    if normalized.is_empty() {
        return HotkeyValidationResult::error(String::new(), "The hotkey is empty.");
    }

    let mut toggle_current = normalize_hotkey_input(Some(&ctx.toggle_current));
    let mut hold_current = normalize_hotkey_input(Some(&ctx.hold_current));
    if ctx.replace_other {
        match kind {
            HotkeyKind::Toggle => hold_current.clear(),
            HotkeyKind::Hold => toggle_current.clear(),
        }
    }

    let (current, other) = match kind {
        HotkeyKind::Toggle => (&toggle_current, &hold_current),
        HotkeyKind::Hold => (&hold_current, &toggle_current),
    };

    if !other.is_empty() && normalized == *other {
        return HotkeyValidationResult::error(
            normalized,
            "Toggle and Hold can't use the same hotkey.",
        );
    }

    if !other.is_empty() && macos_hotkeys_conflict(Some(&normalized), Some(other)) {
        return HotkeyValidationResult::error(normalized, "Toggle and Hold can't overlap.");
    }

    if !current.is_empty() && normalized == *current {
        return HotkeyValidationResult::ok(normalized);
    }

    let needs_input_monitoring = requires_input_monitoring(kind, &normalized);
    if needs_input_monitoring && !ctx.input_monitoring_allowed {
        return HotkeyValidationResult::error(
            normalized,
            "This hotkey needs Input Monitoring (System Settings → Privacy & Security).",
        );
    }

    let parsed = match parse_macos_hotkey(&normalized) {
        Ok(parsed) => parsed,
        Err(err) => return HotkeyValidationResult::error(normalized, err.to_string()),
    };

    if kind == HotkeyKind::Toggle && !needs_input_monitoring {
        if probe_carbon_registration(parsed) {
            return HotkeyValidationResult::ok(normalized);
        }
        if ctx.input_monitoring_allowed {
            return HotkeyValidationResult::warning(
                normalized,
                "macOS blocks this combination as a global hotkey. PulseScribe uses Input Monitoring instead.",
            );
        }
        return HotkeyValidationResult::error(
            normalized,
            "macOS blocks this combination as a global hotkey. Allow Input Monitoring or choose another hotkey.",
        );
    }

    HotkeyValidationResult::ok(normalized)
}

fn normalize_hotkey_input(raw: Option<&str>) -> String {
    raw.unwrap_or("").trim().to_lowercase()
}

fn normalize_windows_part(raw_part: &str) -> Option<String> {
    let part = raw_part.trim().to_lowercase();
    if part.is_empty() {
        return None;
    }

    let mapped = match part.as_str() {
        // Modifier aliases
        "ctrl" | "control" => Some("ctrl"),
        "alt" | "option" => Some("alt"),
        "shift" => Some("shift"),
        "win" | "cmd" | "command" => Some("win"),
        // Special key aliases
        "space" => Some("space"),
        "tab" => Some("tab"),
        "enter" | "return" => Some("enter"),
        "esc" | "escape" => Some("esc"),
        "backspace" => Some("backspace"),
        "delete" => Some("delete"),
        "home" => Some("home"),
        "end" => Some("end"),
        "pageup" => Some("pageup"),
        "pagedown" => Some("pagedown"),
        "up" => Some("up"),
        "down" => Some("down"),
        "left" => Some("left"),
        "right" => Some("right"),
        "capslock" | "caps_lock" => Some("capslock"),
        _ => None,
    };
    if let Some(canonical) = mapped {
        return Some(canonical.to_string());
    }

    if let Some(digits) = part.strip_prefix('f') {
        if !digits.is_empty() && digits.bytes().all(|b| b.is_ascii_digit()) {
            let num = digits.parse::<u32>().ok()?;
            return (1..=24).contains(&num).then(|| format!("f{num}"));
        }
    }

    if part.chars().count() == 1 {
        return Some(part);
    }

    None
}

/// Normalize a Windows hotkey string into canonical modifier order (`ctrl+alt+shift+win+<key>`).
///
/// Mirrors `utils/hotkey_windows.py:normalize_windows_hotkey`:
/// - `None` or whitespace-only returns `Ok("")`.
/// - Deduplicates repeated tokens and canonicalizes aliases (`option` → `alt`, `cmd` → `win`, etc.).
/// - Rejects empty `+`-delimited segments, unknown keys, and more than one non-modifier key.
pub fn normalize_windows_hotkey(hotkey_str: Option<&str>) -> Result<String, String> {
    let Some(raw) = hotkey_str else {
        return Ok(String::new());
    };
    let stripped = raw.trim();
    if stripped.is_empty() {
        return Ok(String::new());
    }

    let raw_parts: Vec<&str> = stripped.split('+').collect();
    if raw_parts.iter().any(|part| part.trim().is_empty()) {
        return Err("Invalid format.".to_string());
    }

    let mut normalized_parts = Vec::with_capacity(raw_parts.len());
    let mut seen = HashSet::with_capacity(raw_parts.len());
    for raw_part in raw_parts {
        let Some(part) = normalize_windows_part(raw_part) else {
            return Err(format!("Unknown key: {}", raw_part.trim().to_lowercase()));
        };
        if seen.insert(part.clone()) {
            normalized_parts.push(part);
        }
    }

    let mut ordered: Vec<&str> = WINDOWS_MODIFIER_ORDER
        .iter()
        .copied()
        .filter(|&modifier| seen.contains(modifier))
        .collect();

    let keys: Vec<&str> = normalized_parts
        .iter()
        .map(String::as_str)
        .filter(|part| !WINDOWS_MODIFIER_ORDER.contains(part))
        .collect();
    if keys.len() > 1 {
        return Err("Use exactly one non-modifier key.".to_string());
    }

    ordered.extend(keys);
    Ok(ordered.join("+"))
}

/// Return `true` when two Windows hotkeys can trigger ambiguously because one
/// normalized token set is a subset of the other (e.g. `"ctrl+win"` and `"ctrl+win+r"`).
pub fn windows_hotkeys_conflict(hotkey_a: Option<&str>, hotkey_b: Option<&str>) -> bool {
    let Ok(norm_a) = normalize_windows_hotkey(hotkey_a) else {
        return false;
    };
    let Ok(norm_b) = normalize_windows_hotkey(hotkey_b) else {
        return false;
    };
    if norm_a.is_empty() || norm_b.is_empty() {
        return false;
    }

    let parts_a: HashSet<&str> = norm_a.split('+').collect();
    let parts_b: HashSet<&str> = norm_b.split('+').collect();
    parts_a.is_subset(&parts_b) || parts_b.is_subset(&parts_a)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn macos_parse_and_conflict_cover_aliases_and_errors() {
        assert_eq!(
            parse_macos_hotkey("  CMD + Shift + Space  ").unwrap(),
            CarbonHotkey {
                virtual_key: 49,
                modifier_mask: CARBON_CMD_KEY | CARBON_SHIFT_KEY,
            }
        );
        assert_eq!(
            parse_macos_hotkey("option+control+f19").unwrap(),
            CarbonHotkey {
                virtual_key: 80,
                modifier_mask: CARBON_OPTION_KEY | CARBON_CONTROL_KEY,
            }
        );
        assert_eq!(
            parse_macos_hotkey("cmd+bogus").unwrap_err().to_string(),
            "Unknown key: bogus"
        );
        assert_eq!(
            parse_macos_hotkey("super+r").unwrap_err().to_string(),
            "Unknown modifier: super"
        );
        assert!(macos_hotkeys_conflict(Some("space"), Some("cmd+space")));
        assert!(macos_hotkeys_conflict(
            Some("command+r"),
            Some("cmd+shift+r")
        ));
        assert!(!macos_hotkeys_conflict(Some("cmd+r"), Some("alt+r")));
        assert!(!macos_hotkeys_conflict(Some("invalid"), Some("cmd+r")));
    }

    #[test]
    fn windows_normalize_and_conflict_cover_aliases_unicode_and_errors() {
        assert_eq!(normalize_windows_hotkey(None).unwrap(), "");
        assert_eq!(normalize_windows_hotkey(Some("   ")).unwrap(), "");
        assert_eq!(
            normalize_windows_hotkey(Some("Option + Control + Command + Shift + R")).unwrap(),
            "ctrl+alt+shift+win+r"
        );
        assert_eq!(
            normalize_windows_hotkey(Some("ctrl+ctrl+f01")).unwrap(),
            "ctrl+f1"
        );
        assert_eq!(normalize_windows_hotkey(Some("ctrl+ä")).unwrap(), "ctrl+ä");
        assert_eq!(
            normalize_windows_hotkey(Some("ctrl++r")).unwrap_err(),
            "Invalid format."
        );
        assert_eq!(
            normalize_windows_hotkey(Some("ctrl+f25")).unwrap_err(),
            "Unknown key: f25"
        );
        assert_eq!(
            normalize_windows_hotkey(Some("ctrl+a+b")).unwrap_err(),
            "Use exactly one non-modifier key."
        );
        assert!(windows_hotkeys_conflict(
            Some("ctrl+win"),
            Some("ctrl+win+r")
        ));
        assert!(!windows_hotkeys_conflict(Some("ctrl+r"), Some("alt+r")));
    }

    #[test]
    fn macos_validation_short_circuits_unchanged_and_handles_carbon_fallback() {
        let mut probed = false;
        let unchanged = validate_macos_hotkey_change(
            HotkeyKind::Toggle,
            Some("F19"),
            &HotkeyValidationContext {
                toggle_current: "f19".to_string(),
                hold_current: "fn".to_string(),
                replace_other: false,
                input_monitoring_allowed: false,
            },
            |_| {
                probed = true;
                false
            },
        );
        assert_eq!(unchanged.level, HotkeyValidationLevel::Ok);
        assert!(!probed, "unchanged hotkey must not invoke Carbon probe");

        let blocked_with_im = validate_macos_hotkey_change(
            HotkeyKind::Toggle,
            Some("cmd+space"),
            &HotkeyValidationContext {
                input_monitoring_allowed: true,
                ..Default::default()
            },
            |_| false,
        );
        assert_eq!(blocked_with_im.level, HotkeyValidationLevel::Warning);

        let blocked_without_im = validate_macos_hotkey_change(
            HotkeyKind::Toggle,
            Some("cmd+space"),
            &HotkeyValidationContext {
                input_monitoring_allowed: false,
                ..Default::default()
            },
            |_| false,
        );
        assert_eq!(blocked_without_im.level, HotkeyValidationLevel::Error);

        // replace_other clears the opposite slot conflict.
        let replaced = validate_macos_hotkey_change(
            HotkeyKind::Toggle,
            Some("cmd+r"),
            &HotkeyValidationContext {
                toggle_current: String::new(),
                hold_current: "cmd+shift+r".to_string(),
                replace_other: true,
                input_monitoring_allowed: false,
            },
            |_| true,
        );
        assert_eq!(replaced.level, HotkeyValidationLevel::Ok);
    }
}
