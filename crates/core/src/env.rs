//! `.env` parsing, formatting, precedence resolution, and line-preserving updates.
//!
//! Mirrors `utils/env.py` and the `.env` mutation helpers in `utils/preferences.py`.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs;
use std::path::Path;

use thiserror::Error;

/// Errors returned when validating or formatting `.env` assignments.
#[derive(Debug, Error, PartialEq, Eq)]
pub enum EnvError {
    #[error("Settings values must not contain line breaks")]
    ValueContainsLineBreak,
    #[error("Environment variable key must not be empty or contain '=' or line breaks")]
    InvalidKey,
}

/// Parse common boolean string representations (`1/true/yes/on`, `0/false/no/off`).
///
/// Mirrors `utils.env.parse_bool`.
pub fn parse_bool(value: Option<&str>) -> Option<bool> {
    let normalized = value?.trim().to_ascii_lowercase();
    match normalized.as_str() {
        "1" | "true" | "yes" | "on" => Some(true),
        "0" | "false" | "no" | "off" => Some(false),
        _ => None,
    }
}

/// Parse a trimmed base-10 integer from an optional environment value.
///
/// Mirrors `utils.env.get_env_int`.
pub fn parse_int(value: Option<&str>) -> Option<i64> {
    value?.trim().parse::<i64>().ok()
}

/// Parse one `.env` line with `export`, quote, and inline comment handling.
///
/// Mirrors `utils.env.parse_env_line`.
pub fn parse_env_line(raw_line: &str) -> Option<(String, String)> {
    let trimmed = raw_line.trim();
    let line = match trimmed.strip_prefix("export ") {
        Some(rest) => rest.trim_start(),
        None => trimmed,
    };
    if line.is_empty() || line.starts_with('#') {
        return None;
    }
    let (raw_key, raw_value) = line.split_once('=')?;
    let key = raw_key.trim();
    if key.is_empty() {
        return None;
    }
    Some((key.to_string(), parse_env_value(raw_value.trim())))
}

fn parse_env_value(value: &str) -> String {
    if value.is_empty() {
        return String::new();
    }

    if let Some(parsed) = parse_single_quoted_value(value) {
        return parsed;
    }

    let mut out = String::with_capacity(value.len());
    let mut in_single = false;
    let mut in_double = false;
    let mut prev: Option<char> = None;

    for ch in value.chars() {
        if ch == '\'' && !in_double {
            in_single = !in_single;
            prev = Some(ch);
            continue;
        }
        if ch == '"' && !in_single {
            in_double = !in_double;
            prev = Some(ch);
            continue;
        }
        if ch == '#' && !in_single && !in_double && prev.is_none_or(char::is_whitespace) {
            break;
        }
        out.push(ch);
        prev = Some(ch);
    }

    out.trim().to_string()
}

/// Matches Python's `re.fullmatch(r"'((?:\\.|[^'\\])*)'(?:\s*#.*)?", value)`
/// and unescapes `\\` and `\'`.
fn parse_single_quoted_value(value: &str) -> Option<String> {
    let rest = value.strip_prefix('\'')?;
    let mut chars = rest.chars();
    let mut unescaped = String::with_capacity(rest.len());

    while let Some(ch) = chars.next() {
        match ch {
            '\\' => {
                let escaped = chars.next()?;
                if escaped == '\\' || escaped == '\'' {
                    unescaped.push(escaped);
                } else {
                    unescaped.push('\\');
                    unescaped.push(escaped);
                }
            }
            '\'' => {
                let trailing = chars.as_str().trim_start();
                if trailing.is_empty() || trailing.starts_with('#') {
                    return Some(unescaped.trim().to_string());
                }
                return None;
            }
            other => unescaped.push(other),
        }
    }
    None
}

/// Format one canonical `KEY=value` line, single-quoting values with dotenv syntax.
///
/// Mirrors `utils.preferences._format_env_assignment`.
pub fn format_env_assignment(key: &str, value: &str) -> Result<String, EnvError> {
    if key.trim().is_empty() || key.contains('=') || key.contains('\n') || key.contains('\r') {
        return Err(EnvError::InvalidKey);
    }
    if value.contains('\n') || value.contains('\r') {
        return Err(EnvError::ValueContainsLineBreak);
    }
    let needs_quotes = value.chars().any(|c| matches!(c, '\'' | '"' | '#' | '\\'));
    if needs_quotes {
        let escaped = value.replace('\\', "\\\\").replace('\'', "\\'");
        Ok(format!("{key}='{escaped}'"))
    } else {
        Ok(format!("{key}={value}"))
    }
}

/// Read one `.env` file via the lightweight line parser (lossy UTF-8 tolerant).
///
/// Mirrors `utils.env.read_env_file_values`. When `first_wins` is `true`, duplicate
/// keys keep their first definition (matching `preferences.read_env_file`).
pub fn read_env_file_values(path: &Path, first_wins: bool) -> BTreeMap<String, String> {
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(_) => return BTreeMap::new(),
    };
    let text = String::from_utf8_lossy(&bytes);
    parse_env_str(&text, first_wins)
}

/// Parse `.env` text into a sorted map.
pub fn parse_env_str(content: &str, first_wins: bool) -> BTreeMap<String, String> {
    let mut values = BTreeMap::new();
    for line in content.lines() {
        let Some((key, value)) = parse_env_line(line) else {
            continue;
        };
        if first_wins && values.contains_key(&key) {
            continue;
        }
        values.insert(key, value);
    }
    values
}

/// Collect `.env` values with precedence `local < user`.
///
/// Mirrors `utils.env.collect_env_values`.
pub fn collect_env_values(
    user_config_dir: &Path,
    local_env_path: Option<&Path>,
) -> BTreeMap<String, String> {
    let mut merged = BTreeMap::new();
    if let Some(local_path) = local_env_path {
        if local_path.exists() {
            merged.extend(read_env_file_values(local_path, false));
        }
    }
    let user_env = user_config_dir.join(".env");
    if user_env.exists() {
        merged.extend(read_env_file_values(&user_env, false));
    }
    merged
}

/// Resolve effective environment values without mutating global process state.
///
/// Precedence:
/// - `override_existing == false` (startup): `process_env > user .env > local .env`
/// - `override_existing == true` (reload): `user .env > local .env > process_env`
///   for keys present in `.env` files, while unrelated process variables stay intact.
pub fn resolve_environment(
    process_env: &BTreeMap<String, String>,
    user_config_dir: &Path,
    local_env_path: Option<&Path>,
    override_existing: bool,
) -> BTreeMap<String, String> {
    let file_values = collect_env_values(user_config_dir, local_env_path);
    let mut resolved = process_env.clone();
    for (key, value) in file_values {
        if !override_existing && resolved.contains_key(&key) {
            continue;
        }
        resolved.insert(key, value);
    }
    resolved
}

/// Snapshot the current OS process environment (valid UTF-8 pairs) into a `BTreeMap`.
pub fn current_process_env() -> BTreeMap<String, String> {
    std::env::vars().collect()
}

/// Apply `.env` updates in file order, then append new keys in update order.
///
/// Mirrors `utils.preferences._apply_env_updates_to_lines`:
/// - Batch updates (`preserve_following_duplicates == false`) canonicalize assignments
///   and collapse matching duplicates.
/// - Single-setting saves (`preserve_following_duplicates == true`) preserve later
///   duplicates and leave unchanged formatting intact.
/// - Deletions (`None`) always remove every matching assignment.
pub fn apply_env_updates_to_lines(
    lines: &[String],
    updates: &[(String, Option<String>)],
    preserve_following_duplicates: bool,
) -> Result<(Vec<String>, bool), EnvError> {
    let update_map: HashMap<&str, Option<&str>> = updates
        .iter()
        .map(|(k, v)| (k.as_str(), v.as_deref()))
        .collect();

    let mut handled_keys: HashSet<String> = HashSet::new();
    let mut new_lines: Vec<String> = Vec::with_capacity(lines.len() + updates.len());
    let mut changed = false;

    for line in lines {
        let parsed = parse_env_line(line);
        let Some((key, existing_value)) = parsed else {
            new_lines.push(line.clone());
            continue;
        };
        let Some(&target_value) = update_map.get(key.as_str()) else {
            new_lines.push(line.clone());
            continue;
        };

        let is_duplicate = !handled_keys.insert(key.clone());
        let Some(value) = target_value else {
            changed = true;
            continue;
        };
        if is_duplicate && !preserve_following_duplicates {
            changed = true;
            continue;
        }

        if preserve_following_duplicates && (is_duplicate || existing_value == value) {
            new_lines.push(line.clone());
        } else {
            let canonical_line = format_env_assignment(&key, value)?;
            if line != &canonical_line {
                changed = true;
            }
            new_lines.push(canonical_line);
        }
    }

    let mut appended_keys: HashSet<&str> = HashSet::new();
    for (key, _) in updates {
        let Some(&Some(value)) = update_map.get(key.as_str()) else {
            continue;
        };
        if !handled_keys.contains(key.as_str()) && appended_keys.insert(key.as_str()) {
            new_lines.push(format_env_assignment(key, value)?);
            changed = true;
        }
    }

    Ok((new_lines, changed))
}

/// Serialize `.env` lines with trailing newline when non-empty.
pub fn render_env_lines(lines: &[String]) -> String {
    if lines.is_empty() {
        String::new()
    } else {
        let mut out = lines.join("\n");
        out.push('\n');
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn unique_dir(tag: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "pulsescribe-core-env-{tag}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn precedence_is_process_gt_user_gt_local_on_startup_and_files_win_on_reload() {
        let root = unique_dir("precedence");
        let user_dir = root.join("user");
        fs::create_dir_all(&user_dir).unwrap();
        let local_env = root.join("local.env");

        fs::write(&local_env, "A=local\nB=local\nC=local\n").unwrap();
        fs::write(user_dir.join(".env"), "B=user\nC=user\n").unwrap();

        let mut process = BTreeMap::new();
        process.insert("C".to_string(), "process".to_string());
        process.insert("UNRELATED".to_string(), "keep".to_string());

        let startup = resolve_environment(&process, &user_dir, Some(&local_env), false);
        assert_eq!(startup.get("A").map(String::as_str), Some("local"));
        assert_eq!(startup.get("B").map(String::as_str), Some("user"));
        assert_eq!(startup.get("C").map(String::as_str), Some("process"));
        assert_eq!(startup.get("UNRELATED").map(String::as_str), Some("keep"));

        let reload = resolve_environment(&process, &user_dir, Some(&local_env), true);
        assert_eq!(reload.get("A").map(String::as_str), Some("local"));
        assert_eq!(reload.get("B").map(String::as_str), Some("user"));
        assert_eq!(reload.get("C").map(String::as_str), Some("user"));
        assert_eq!(reload.get("UNRELATED").map(String::as_str), Some("keep"));

        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn parse_int_handles_whitespace_and_invalid_values() {
        assert_eq!(parse_int(Some(" 42 ")), Some(42));
        assert_eq!(parse_int(Some("-7")), Some(-7));
        assert_eq!(parse_int(Some("abc")), None);
        assert_eq!(parse_int(None), None);
    }

    #[test]
    fn format_env_assignment_rejects_line_breaks_and_quotes_specials() {
        assert_eq!(
            format_env_assignment("PULSESCRIBE_MODE", "local").unwrap(),
            "PULSESCRIBE_MODE=local"
        );
        assert_eq!(
            format_env_assignment("MIC", "Bob's #1 \"USB\" \\Mic").unwrap(),
            "MIC='Bob\\'s #1 \"USB\" \\\\Mic'"
        );
        assert_eq!(
            format_env_assignment("MIC", "line1\nline2"),
            Err(EnvError::ValueContainsLineBreak)
        );
        assert_eq!(
            format_env_assignment("MIC", "line1\rline2"),
            Err(EnvError::ValueContainsLineBreak)
        );
        assert_eq!(
            format_env_assignment("BAD=KEY", "val"),
            Err(EnvError::InvalidKey)
        );
        assert_eq!(
            format_env_assignment("   ", "val"),
            Err(EnvError::InvalidKey)
        );
    }

    #[test]
    fn duplicate_keys_in_updates_use_last_effective_value() {
        let lines = vec!["EXISTING=old".to_string()];
        let updates = vec![
            ("NEW_A".to_string(), Some("first".to_string())),
            ("NEW_A".to_string(), Some("second".to_string())),
            ("NEW_B".to_string(), Some("temp".to_string())),
            ("NEW_B".to_string(), None),
        ];
        let (out, changed) = apply_env_updates_to_lines(&lines, &updates, false).unwrap();
        assert!(changed);
        assert_eq!(out, vec!["EXISTING=old", "NEW_A=second"]);
    }
}
