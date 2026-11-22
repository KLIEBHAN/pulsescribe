//! Transcript history storage (`~/.pulsescribe/history.jsonl`), rotation, and viewers.
//!
//! Mirrors `utils/history.py` and the tail/offset readers in `utils/log_tail.py`:
//! serialized rotation + append under a per-store write mutex, bounded tail-first
//! reads with full-read fallback, incremental offset reads, and display/welcome
//! formatting matching both Windows and macOS history views.

use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use chrono::Local;
use serde_json::{Map, Value};
use thiserror::Error;

use crate::atomic_io::write_text_atomic;
use crate::paths::{build_file_signature, AppPaths, FileSignature};

/// Default history file rotation threshold (10 MiB, matching `MAX_HISTORY_SIZE_MB = 10`).
pub const MAX_HISTORY_SIZE_MB: usize = 10;
pub const MAX_HISTORY_SIZE_BYTES: usize = MAX_HISTORY_SIZE_MB * 1024 * 1024;

const TAIL_CHUNK_SIZE: usize = 8192;
const RECENT_SCAN_BYTES_MIN: usize = 128_000;
const RECENT_SCAN_BYTES_MAX: usize = 4_000_000;
const RECENT_LINES_FACTOR: usize = 3;

/// Empty-state text for the Windows transcripts viewer.
pub const DISPLAY_TRANSCRIPTS_EMPTY_MESSAGE: &str =
    "No transcripts yet.\n\nYour recent dictations will appear here after the first transcription.";

/// Empty-state text for the macOS welcome/history viewer.
pub const WELCOME_TRANSCRIPTS_EMPTY_MESSAGE: &str =
    "No transcriptions yet.\n\nYour recent dictations will appear here after the first transcription.";

/// Errors produced by `HistoryStore` write/clear operations.
#[derive(Debug, Error)]
pub enum HistoryError {
    #[error("I/O error: {0}")]
    Io(#[from] io::Error),
    #[error("JSON serialization error: {0}")]
    Json(#[from] serde_json::Error),
}

/// Outcome of a transcript save operation, separating durability of the appended
/// record from any non-fatal rotation maintenance failure.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HistorySaveOutcome {
    /// `true` once the transcript record has been written to `history.jsonl`;
    /// `false` if the input text was empty and nothing was written.
    pub saved: bool,
    /// Optional maintenance warning if size-based rotation could not complete.
    /// Callers can retry maintenance via `HistoryStore::rotate_if_needed()` without
    /// appending the transcript a second time.
    pub rotation_warning: Option<String>,
}

/// One persisted JSONL transcript record in `~/.pulsescribe/history.jsonl`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct TranscriptEntry {
    pub timestamp: String,
    pub text: String,
    pub mode: Option<String>,
    pub language: Option<String>,
    pub app: Option<String>,
    pub refined: bool,
    pub extra: Map<String, Value>,
}

impl TranscriptEntry {
    /// Build a new transcript entry stamped with the current local ISO-8601 time.
    pub fn new(
        text: impl Into<String>,
        mode: Option<String>,
        language: Option<String>,
        refined: bool,
        app_context: Option<String>,
    ) -> Self {
        let timestamp = Local::now().format("%Y-%m-%dT%H:%M:%S%.6f").to_string();
        Self::with_timestamp(timestamp, text, mode, language, refined, app_context)
    }

    /// Build a transcript entry with an explicit timestamp string.
    pub fn with_timestamp(
        timestamp: impl Into<String>,
        text: impl Into<String>,
        mode: Option<String>,
        language: Option<String>,
        refined: bool,
        app_context: Option<String>,
    ) -> Self {
        Self {
            timestamp: timestamp.into(),
            text: text.into(),
            mode: non_empty_opt(mode),
            language: non_empty_opt(language),
            app: non_empty_opt(app_context),
            refined,
            extra: Map::new(),
        }
    }

    /// Parse any JSON object into a `TranscriptEntry`, ignoring non-object payloads
    /// and tolerating missing/legacy fields just like Python's `_coerce_transcript_entry`.
    pub fn from_json_value(value: Value) -> Option<Self> {
        let Value::Object(mut obj) = value else {
            return None;
        };

        let timestamp = obj
            .remove("timestamp")
            .map(value_to_string_or_empty)
            .unwrap_or_default();
        let text = obj
            .remove("text")
            .map(value_to_string_or_empty)
            .unwrap_or_default();
        let mode = obj
            .remove("mode")
            .map(value_to_string_or_empty)
            .filter(|s| !s.is_empty());
        let language = obj
            .remove("language")
            .map(value_to_string_or_empty)
            .filter(|s| !s.is_empty());
        let app = obj
            .remove("app")
            .map(value_to_string_or_empty)
            .filter(|s| !s.is_empty());
        let refined = obj.remove("refined").is_some_and(value_is_truthy);

        Some(Self {
            timestamp,
            text,
            mode,
            language,
            app,
            refined,
            extra: obj,
        })
    }

    /// Parse a single JSONL line into a `TranscriptEntry`, returning `None` for
    /// blank lines, malformed JSON, or non-object JSON values.
    pub fn from_json_line(line: &str) -> Option<Self> {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            return None;
        }
        let value: Value = serde_json::from_str(trimmed).ok()?;
        Self::from_json_value(value)
    }

    /// Convert to a JSON object omitting empty optional fields and `refined == false`.
    pub fn to_json_value(&self) -> Value {
        let mut obj = Map::new();
        obj.insert(
            "timestamp".to_string(),
            Value::String(self.timestamp.clone()),
        );
        obj.insert("text".to_string(), Value::String(self.text.clone()));
        if let Some(mode) = self.mode.as_deref().filter(|s| !s.is_empty()) {
            obj.insert("mode".to_string(), Value::String(mode.to_string()));
        }
        if let Some(language) = self.language.as_deref().filter(|s| !s.is_empty()) {
            obj.insert("language".to_string(), Value::String(language.to_string()));
        }
        if let Some(app) = self.app.as_deref().filter(|s| !s.is_empty()) {
            obj.insert("app".to_string(), Value::String(app.to_string()));
        }
        if self.refined {
            obj.insert("refined".to_string(), Value::Bool(true));
        }
        for (key, value) in &self.extra {
            if !matches!(
                key.as_str(),
                "timestamp" | "text" | "mode" | "language" | "app" | "refined"
            ) {
                obj.insert(key.clone(), value.clone());
            }
        }
        Value::Object(obj)
    }

    /// Serialize as a single compact UTF-8 JSON line (without trailing newline).
    pub fn to_json_line(&self) -> Result<String, HistoryError> {
        Ok(serde_json::to_string(&self.to_json_value())?)
    }
}

fn non_empty_opt(value: Option<String>) -> Option<String> {
    value.filter(|s| !s.is_empty())
}

fn value_to_string_or_empty(value: Value) -> String {
    match value {
        Value::Null => String::new(),
        Value::String(s) => s,
        Value::Bool(b) => if b { "True" } else { "False" }.to_string(),
        Value::Number(n) => n.to_string(),
        other => other.to_string(),
    }
}

fn value_is_truthy(value: Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => b,
        Value::Number(n) => n.as_f64().is_some_and(|v| v != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

fn split_lines(text: &str) -> Vec<&str> {
    let bytes = text.as_bytes();
    let mut lines = Vec::new();
    let mut start = 0;
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'\n' {
            lines.push(&text[start..i]);
            i += 1;
            start = i;
        } else if bytes[i] == b'\r' {
            lines.push(&text[start..i]);
            i += 1;
            if i < bytes.len() && bytes[i] == b'\n' {
                i += 1;
            }
            start = i;
        } else {
            i += 1;
        }
    }
    if start < bytes.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// Keep the newest JSONL lines that still fit within `max_size_bytes`.
///
/// The newest line is always preserved even when it alone exceeds `max_size_bytes`.
pub fn select_recent_lines_within_bytes<S: AsRef<str>>(
    lines: &[S],
    max_size_bytes: usize,
) -> Vec<String> {
    if lines.is_empty() {
        return Vec::new();
    }

    let mut kept_reversed = Vec::new();
    let mut total_bytes = 0usize;

    for line in lines.iter().rev() {
        let line_str = line.as_ref();
        let line_size = line_str.len().saturating_add(1); // UTF-8 bytes + JSONL '\n'
        if !kept_reversed.is_empty() && total_bytes.saturating_add(line_size) > max_size_bytes {
            break;
        }
        kept_reversed.push(line_str.to_string());
        total_bytes = total_bytes.saturating_add(line_size);
    }

    kept_reversed.reverse();
    kept_reversed
}

/// Merge append-only entries into an oldest-first visible window of at most `max_entries`.
pub fn merge_recent_transcript_entries(
    previous_entries: &[TranscriptEntry],
    appended_entries: &[TranscriptEntry],
    max_entries: usize,
) -> Vec<TranscriptEntry> {
    if max_entries == 0 {
        return Vec::new();
    }
    let total = previous_entries.len() + appended_entries.len();
    let skip = total.saturating_sub(max_entries);
    previous_entries
        .iter()
        .chain(appended_entries.iter())
        .skip(skip)
        .cloned()
        .collect()
}

fn format_entry_header(
    entry: &TranscriptEntry,
    include_language: bool,
    strip_metadata: bool,
) -> String {
    let ts: String = entry
        .timestamp
        .chars()
        .take(19)
        .map(|c| if c == 'T' { ' ' } else { c })
        .collect();
    let mut header = format!("[{ts}]");

    let mut metadata_values = Vec::new();
    if let Some(mode) = entry.mode.as_deref() {
        let v = if strip_metadata { mode.trim() } else { mode };
        if !v.is_empty() {
            metadata_values.push(v);
        }
    }
    if include_language {
        if let Some(lang) = entry.language.as_deref() {
            let v = if strip_metadata { lang.trim() } else { lang };
            if !v.is_empty() {
                metadata_values.push(v);
            }
        }
    }

    if !metadata_values.is_empty() {
        header.push_str(" (");
        header.push_str(&metadata_values.join(" "));
        header.push(')');
    }
    header
}

fn format_display_text(text: &str) -> String {
    let raw_lines = split_lines(text);
    if raw_lines.is_empty() {
        return String::new();
    }

    let mut start = 0usize;
    let mut end = raw_lines.len();
    while start < end && raw_lines[start].trim().is_empty() {
        start += 1;
    }
    while end > start && raw_lines[end - 1].trim().is_empty() {
        end -= 1;
    }

    let lines: Vec<&str> = raw_lines[start..end]
        .iter()
        .map(|line| line.trim_end())
        .collect();
    if lines.is_empty() {
        return String::new();
    }
    if lines.len() == 1 {
        return lines[0].to_string();
    }

    let mut out = String::from(lines[0]);
    for line in &lines[1..] {
        out.push_str("\n    ");
        out.push_str(line);
    }
    out
}

/// Format a single transcript entry for the Windows transcripts viewer.
pub fn format_transcript_entry_for_display(entry: &TranscriptEntry) -> String {
    let header = format_entry_header(entry, false, false);
    let text = format_display_text(&entry.text);
    let refined = if entry.refined { "✨" } else { "" };
    format!("{header} {refined}{text}")
}

/// Format transcript entries into oldest-first display blocks (`newest_first` describes input order).
pub fn format_transcript_entries_for_display(
    entries: &[TranscriptEntry],
    newest_first: bool,
) -> Vec<String> {
    if newest_first {
        entries
            .iter()
            .rev()
            .map(format_transcript_entry_for_display)
            .collect()
    } else {
        entries
            .iter()
            .map(format_transcript_entry_for_display)
            .collect()
    }
}

/// Format transcript entries for the Windows transcripts viewer.
pub fn format_transcripts_for_display(entries: &[TranscriptEntry], newest_first: bool) -> String {
    let blocks = format_transcript_entries_for_display(entries, newest_first);
    if blocks.is_empty() {
        DISPLAY_TRANSCRIPTS_EMPTY_MESSAGE.to_string()
    } else {
        blocks.join("\n\n")
    }
}

/// Format a single transcript entry for the macOS welcome/history viewer.
pub fn format_transcript_entry_for_welcome(entry: &TranscriptEntry) -> String {
    let header = format_entry_header(entry, true, true);
    let text = entry.text.trim();
    if text.is_empty() {
        header
    } else {
        format!("{header}\n{text}")
    }
}

/// Format transcript entries into oldest-first welcome blocks (`newest_first` describes input order).
pub fn format_transcript_entries_for_welcome(
    entries: &[TranscriptEntry],
    newest_first: bool,
) -> Vec<String> {
    if newest_first {
        entries
            .iter()
            .rev()
            .map(format_transcript_entry_for_welcome)
            .collect()
    } else {
        entries
            .iter()
            .map(format_transcript_entry_for_welcome)
            .collect()
    }
}

/// Format transcript entries for the macOS welcome/history viewer.
pub fn format_transcripts_for_welcome(entries: &[TranscriptEntry], newest_first: bool) -> String {
    let blocks = format_transcript_entries_for_welcome(entries, newest_first);
    if blocks.is_empty() {
        WELCOME_TRANSCRIPTS_EMPTY_MESSAGE.to_string()
    } else {
        blocks.join("\n\n")
    }
}

fn recent_history_read_limits(count: usize) -> (usize, usize) {
    let tail_max_lines = count
        .saturating_mul(RECENT_LINES_FACTOR)
        .max(count.saturating_add(10));
    let tail_max_scan_bytes = count
        .saturating_mul(4096)
        .clamp(RECENT_SCAN_BYTES_MIN, RECENT_SCAN_BYTES_MAX);
    (tail_max_lines, tail_max_scan_bytes)
}

fn read_tail_lines(
    path: &Path,
    max_lines: usize,
    max_scan_bytes: usize,
) -> io::Result<(Vec<String>, u64)> {
    if max_lines == 0 || max_scan_bytes == 0 {
        return Ok((Vec::new(), 0));
    }

    let mut file = File::open(path)?;
    let file_size = file.seek(SeekFrom::End(0))?;
    if file_size == 0 {
        return Ok((Vec::new(), 0));
    }

    let mut position = file_size;
    let mut chunks: Vec<Vec<u8>> = Vec::new();
    let mut collected_bytes = 0usize;
    let mut collected_newlines = 0usize;
    let mut truncated_from_start = false;

    while position > 0 {
        let read_size = (TAIL_CHUNK_SIZE as u64).min(position) as usize;
        position -= read_size as u64;
        file.seek(SeekFrom::Start(position))?;
        let mut buf = Vec::with_capacity(read_size);
        (&file).take(read_size as u64).read_to_end(&mut buf)?;
        if buf.is_empty() {
            break;
        }

        collected_bytes = collected_bytes.saturating_add(buf.len());
        collected_newlines =
            collected_newlines.saturating_add(buf.iter().filter(|&&b| b == b'\n').count());
        chunks.push(buf);

        if collected_newlines > max_lines.saturating_add(1) {
            truncated_from_start = position > 0;
            break;
        }
        if collected_bytes >= max_scan_bytes {
            truncated_from_start = position > 0;
            break;
        }
    }

    let mut data = Vec::with_capacity(collected_bytes);
    for chunk in chunks.into_iter().rev() {
        data.extend_from_slice(&chunk);
    }
    if data.len() > max_scan_bytes {
        let trim_start = data.len() - max_scan_bytes;
        data.drain(..trim_start);
        truncated_from_start = true;
    }

    let text = String::from_utf8_lossy(&data);
    let mut lines: Vec<String> = split_lines(&text)
        .into_iter()
        .map(ToOwned::to_owned)
        .collect();
    if truncated_from_start && !matches!(data.first(), Some(b'\n' | b'\r')) && !lines.is_empty() {
        lines.remove(0);
    }
    if lines.len() > max_lines {
        let skip = lines.len() - max_lines;
        lines.drain(..skip);
    }

    Ok((lines, file_size))
}

fn parse_recent_entries<S: AsRef<str>>(lines: &[S], count: usize) -> Vec<TranscriptEntry> {
    let mut entries = Vec::with_capacity(count.min(lines.len()));
    for line in lines.iter().rev() {
        if let Some(entry) = TranscriptEntry::from_json_line(line.as_ref()) {
            entries.push(entry);
            if entries.len() >= count {
                break;
            }
        }
    }
    entries
}

/// Reads, appends, rotates, and clears `~/.pulsescribe/history.jsonl`.
#[derive(Debug)]
pub struct HistoryStore {
    paths: AppPaths,
    max_size_bytes: usize,
    write_lock: Mutex<()>,
}

impl HistoryStore {
    /// Create a store rooted at an explicit configuration directory using the 10 MiB limit.
    pub fn new(config_dir: impl Into<PathBuf>) -> Self {
        Self::with_max_size_bytes(config_dir, MAX_HISTORY_SIZE_BYTES)
    }

    /// Create a store with a custom rotation threshold in bytes (clamped to at least 1 byte).
    pub fn with_max_size_bytes(config_dir: impl Into<PathBuf>, max_size_bytes: usize) -> Self {
        Self {
            paths: AppPaths::new(config_dir),
            max_size_bytes: max_size_bytes.max(1),
            write_lock: Mutex::new(()),
        }
    }

    /// Create a store pointing at the current user's `~/.pulsescribe/`.
    pub fn default_user() -> Option<Self> {
        AppPaths::default_user().map(|paths| Self {
            paths,
            max_size_bytes: MAX_HISTORY_SIZE_BYTES,
            write_lock: Mutex::new(()),
        })
    }

    pub fn paths(&self) -> &AppPaths {
        &self.paths
    }

    /// Trim `text`, stamp the current local time, and append to `history.jsonl` with rotation.
    /// Returns `saved: false` without touching disk when `text` is empty or whitespace-only.
    pub fn save_transcript(
        &self,
        text: &str,
        mode: Option<&str>,
        language: Option<&str>,
        refined: bool,
        app_context: Option<&str>,
    ) -> Result<HistorySaveOutcome, HistoryError> {
        let clean = text.trim();
        if clean.is_empty() {
            return Ok(HistorySaveOutcome {
                saved: false,
                rotation_warning: None,
            });
        }
        let entry = TranscriptEntry::new(
            clean,
            mode.map(ToOwned::to_owned),
            language.map(ToOwned::to_owned),
            refined,
            app_context.map(ToOwned::to_owned),
        );
        self.save_entry(&entry)
    }

    /// Append a pre-built `TranscriptEntry` to `history.jsonl` under the serialized write lock.
    ///
    /// Recovers any unterminated trailing line before appending and separates rotation
    /// maintenance warnings from commit failure so callers never duplicate a saved record.
    pub fn save_entry(&self, entry: &TranscriptEntry) -> Result<HistorySaveOutcome, HistoryError> {
        if entry.text.is_empty() {
            return Ok(HistorySaveOutcome {
                saved: false,
                rotation_warning: None,
            });
        }

        let mut record = entry.to_json_line()?;
        record.push('\n');

        let _write_guard = self.write_lock.lock().unwrap_or_else(|e| e.into_inner());
        let history_file = self.paths.history_file();

        if let Some(parent) = history_file.parent() {
            fs::create_dir_all(parent)?;
        }

        let mut rotation_warning = match self.rotate_if_needed_locked(&history_file) {
            Ok(_) => None,
            Err(err) => Some(err.to_string()),
        };

        ensure_clean_jsonl_tail_locked(&history_file)?;

        {
            let mut file = OpenOptions::new()
                .create(true)
                .append(true)
                .open(&history_file)?;
            file.write_all(record.as_bytes())?;
        }

        // Rotate again immediately if the newly appended line pushed size past the threshold.
        if let Err(err) = self.rotate_if_needed_locked(&history_file) {
            rotation_warning = Some(err.to_string());
        }

        Ok(HistorySaveOutcome {
            saved: true,
            rotation_warning,
        })
    }

    /// Run size-based history rotation under the write lock without appending a new entry.
    /// Returns `Ok(true)` if the file was rewritten to drop older entries, `Ok(false)` otherwise.
    pub fn rotate_if_needed(&self) -> Result<bool, HistoryError> {
        let _write_guard = self.write_lock.lock().unwrap_or_else(|e| e.into_inner());
        self.rotate_if_needed_locked(&self.paths.history_file())
    }

    fn rotate_if_needed_locked(&self, history_file: &Path) -> Result<bool, HistoryError> {
        let meta = match fs::metadata(history_file) {
            Ok(meta) => meta,
            Err(err) if err.kind() == io::ErrorKind::NotFound => return Ok(false),
            Err(err) => return Err(HistoryError::Io(err)),
        };

        if meta.len() < self.max_size_bytes as u64 {
            return Ok(false);
        }

        let raw_bytes = fs::read(history_file)?;
        let raw_text = String::from_utf8_lossy(&raw_bytes);
        let lines: Vec<&str> = split_lines(&raw_text);
        let kept = select_recent_lines_within_bytes(&lines, self.max_size_bytes);
        if !kept.is_empty() && kept.len() < lines.len() {
            let mut content = kept.join("\n");
            content.push('\n');
            write_text_atomic(history_file, &content)?;
            return Ok(true);
        }
        Ok(false)
    }

    /// Return up to `count` most recent valid transcript entries (newest first)
    /// together with the observed file signature.
    pub fn get_recent_transcripts_with_signature(
        &self,
        count: usize,
    ) -> (Vec<TranscriptEntry>, Option<FileSignature>) {
        if count == 0 {
            return (Vec::new(), None);
        }

        let history_file = self.paths.history_file();
        let Ok(signature) = build_file_signature(&history_file) else {
            return (Vec::new(), None);
        };

        let entries = self
            .load_recent_entries(&history_file, count)
            .unwrap_or_default();
        (entries, Some(signature))
    }

    /// Return up to `count` most recent valid transcript entries (newest first).
    pub fn get_recent_transcripts(&self, count: usize) -> Vec<TranscriptEntry> {
        self.get_recent_transcripts_with_signature(count).0
    }

    fn load_recent_entries(
        &self,
        history_file: &Path,
        count: usize,
    ) -> io::Result<Vec<TranscriptEntry>> {
        let (tail_max_lines, tail_max_scan_bytes) = recent_history_read_limits(count);
        let (tail_lines, file_size) =
            read_tail_lines(history_file, tail_max_lines, tail_max_scan_bytes)?;
        let entries = parse_recent_entries(&tail_lines, count);
        if entries.len() >= count {
            return Ok(entries);
        }

        if file_size <= tail_max_scan_bytes as u64 && tail_lines.len() < tail_max_lines {
            return Ok(entries);
        }

        let full_bytes = fs::read(history_file)?;
        let full_text = String::from_utf8_lossy(&full_bytes);
        let full_lines = split_lines(&full_text);
        Ok(parse_recent_entries(&full_lines, count))
    }

    /// Read newly appended transcript entries starting at `start_offset` in file order.
    ///
    /// Returns an empty list if the file is missing, unchanged, or if the appended
    /// delta exceeds `max_bytes` (signaling callers to fall back to a tail reload).
    pub fn read_transcripts_from_offset(
        &self,
        start_offset: u64,
        max_bytes: Option<usize>,
    ) -> Vec<TranscriptEntry> {
        if max_bytes == Some(0) {
            return Vec::new();
        }
        let history_file = self.paths.history_file();
        let Ok(mut file) = File::open(&history_file) else {
            return Vec::new();
        };
        let Ok(file_size) = file.seek(SeekFrom::End(0)) else {
            return Vec::new();
        };
        if start_offset >= file_size {
            return Vec::new();
        }
        let read_size = file_size - start_offset;
        if let Some(limit) = max_bytes {
            if read_size > limit as u64 {
                return Vec::new();
            }
        }
        if file.seek(SeekFrom::Start(start_offset)).is_err() {
            return Vec::new();
        }

        let mut buf = Vec::with_capacity(usize::try_from(read_size).unwrap_or(0));
        if file.take(read_size).read_to_end(&mut buf).is_err() {
            return Vec::new();
        }

        let text = String::from_utf8_lossy(&buf);
        split_lines(&text)
            .into_iter()
            .filter_map(TranscriptEntry::from_json_line)
            .collect()
    }

    /// Remove the newest matching transcript entry atomically under the serialized write lock.
    ///
    /// Uses lossy UTF-8 decoding so a stray non-UTF-8 byte in `history.jsonl` never prevents deletion.
    pub fn delete_entry(&self, timestamp: &str, text: &str) -> Result<bool, HistoryError> {
        let _write_guard = self.write_lock.lock().unwrap_or_else(|e| e.into_inner());
        let history_file = self.paths.history_file();
        let raw_bytes = match fs::read(&history_file) {
            Ok(bytes) => bytes,
            Err(err) if err.kind() == io::ErrorKind::NotFound => return Ok(false),
            Err(err) => return Err(HistoryError::Io(err)),
        };

        let raw_text = String::from_utf8_lossy(&raw_bytes);
        let mut lines: Vec<&str> = split_lines(&raw_text);
        let mut removed = false;
        for idx in (0..lines.len()).rev() {
            if let Some(entry) = TranscriptEntry::from_json_line(lines[idx]) {
                if entry.timestamp == timestamp && entry.text == text {
                    lines.remove(idx);
                    removed = true;
                    break;
                }
            }
        }
        if !removed {
            return Ok(false);
        }

        let mut content = lines.join("\n");
        if !content.is_empty() {
            content.push('\n');
        }
        write_text_atomic(&history_file, &content)?;
        Ok(true)
    }

    /// Delete `history.jsonl` if it exists under the serialized write lock.
    pub fn clear(&self) -> Result<(), HistoryError> {
        let _write_guard = self.write_lock.lock().unwrap_or_else(|e| e.into_inner());
        match fs::remove_file(self.paths.history_file()) {
            Ok(()) => Ok(()),
            Err(err) if err.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(err) => Err(HistoryError::Io(err)),
        }
    }
}

/// Ensure an existing `history.jsonl` file ends cleanly on a newline boundary before appending.
///
/// Leaves newline-terminated files untouched after a single-byte tail check. If the
/// final line lacks a trailing newline, delimits it with `\n` when it parses as a
/// complete JSON object, or truncates the incomplete trailing fragment otherwise.
fn ensure_clean_jsonl_tail_locked(history_file: &Path) -> io::Result<()> {
    let mut file = match OpenOptions::new().read(true).write(true).open(history_file) {
        Ok(file) => file,
        Err(err) if err.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(err) => return Err(err),
    };

    let file_len = file.seek(SeekFrom::End(0))?;
    if file_len == 0 {
        return Ok(());
    }

    file.seek(SeekFrom::End(-1))?;
    let mut last_byte = [0u8; 1];
    file.read_exact(&mut last_byte)?;
    if last_byte[0] == b'\n' {
        return Ok(());
    }

    let mut pos = file_len;
    let mut last_line_start = 0u64;
    let mut buf = vec![0u8; TAIL_CHUNK_SIZE];
    while pos > 0 {
        let read_size = (TAIL_CHUNK_SIZE as u64).min(pos) as usize;
        pos -= read_size as u64;
        file.seek(SeekFrom::Start(pos))?;
        file.read_exact(&mut buf[..read_size])?;
        if let Some(rel_idx) = buf[..read_size].iter().rposition(|&b| b == b'\n') {
            last_line_start = pos + rel_idx as u64 + 1;
            break;
        }
    }

    let tail_len = file_len - last_line_start;
    file.seek(SeekFrom::Start(last_line_start))?;
    let mut tail_bytes = Vec::with_capacity(usize::try_from(tail_len).unwrap_or(0));
    (&file).take(tail_len).read_to_end(&mut tail_bytes)?;
    let tail_text = String::from_utf8_lossy(&tail_bytes);

    if TranscriptEntry::from_json_line(&tail_text).is_some() {
        file.seek(SeekFrom::End(0))?;
        file.write_all(b"\n")?;
    } else {
        file.set_len(last_line_start)?;
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn unique_store(tag: &str, max_bytes: usize) -> (HistoryStore, PathBuf) {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "pulsescribe-core-history-{tag}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(&dir).unwrap();
        (HistoryStore::with_max_size_bytes(&dir, max_bytes), dir)
    }

    #[test]
    fn save_load_offset_and_clear_roundtrip() {
        let (store, dir) = unique_store("roundtrip", MAX_HISTORY_SIZE_BYTES);

        let empty_outcome = store
            .save_transcript("   \n\t  ", Some("local"), None, false, None)
            .unwrap();
        assert!(!empty_outcome.saved);
        assert_eq!(empty_outcome.rotation_warning, None);
        assert!(!store.paths().history_file().exists());

        let first_outcome = store
            .save_transcript(
                "  Hello world  ",
                Some("local"),
                Some("en"),
                true,
                Some("Slack"),
            )
            .unwrap();
        assert!(first_outcome.saved);
        assert_eq!(first_outcome.rotation_warning, None);
        let first_offset = fs::metadata(store.paths().history_file()).unwrap().len();

        let second_outcome = store
            .save_transcript("Second line", Some(""), Some(""), false, Some(""))
            .unwrap();
        assert!(second_outcome.saved);
        assert_eq!(second_outcome.rotation_warning, None);

        let (recent, sig) = store.get_recent_transcripts_with_signature(10);
        assert!(sig.is_some());
        assert_eq!(recent.len(), 2);
        assert_eq!(recent[0].text, "Second line");
        assert_eq!(recent[0].mode, None);
        assert!(!recent[0].refined);
        assert_eq!(recent[1].text, "Hello world");
        assert_eq!(recent[1].mode.as_deref(), Some("local"));
        assert_eq!(recent[1].language.as_deref(), Some("en"));
        assert_eq!(recent[1].app.as_deref(), Some("Slack"));
        assert!(recent[1].refined);

        // Incremental read from offset returns only the appended entry in file order.
        let appended = store.read_transcripts_from_offset(first_offset, Some(4096));
        assert_eq!(appended.len(), 1);
        assert_eq!(appended[0].text, "Second line");
        assert!(store
            .read_transcripts_from_offset(first_offset, Some(4))
            .is_empty());

        store.clear().unwrap();
        assert!(store.get_recent_transcripts(10).is_empty());
        // Idempotent clear on absent file.
        store.clear().unwrap();

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn save_recovers_unterminated_complete_and_truncated_jsonl_tails() {
        let (store, dir) = unique_store("tail-recovery", MAX_HISTORY_SIZE_BYTES);
        let path = store.paths().history_file();

        // Case 1: complete JSON object missing its trailing newline -> delimited, both kept.
        fs::write(
            &path,
            r#"{"timestamp":"2026-11-21T10:00:00","text":"unterminated-complete"}"#,
        )
        .unwrap();
        let outcome = store
            .save_transcript("after-complete", Some("local"), None, false, None)
            .unwrap();
        assert!(outcome.saved);
        let recent = store.get_recent_transcripts(10);
        assert_eq!(recent.len(), 2);
        assert_eq!(recent[0].text, "after-complete");
        assert_eq!(recent[1].text, "unterminated-complete");

        // Case 2: truncated partial JSON fragment at EOF -> truncated, prior + new both readable.
        let mut content = fs::read_to_string(&path).unwrap();
        content.push_str(r#"{"timestamp":"2026-11-21T10:01:00","text":"half-writ"#);
        fs::write(&path, content).unwrap();

        let outcome2 = store
            .save_transcript("after-truncated", Some("local"), None, false, None)
            .unwrap();
        assert!(outcome2.saved);
        let recent2 = store.get_recent_transcripts(10);
        assert_eq!(recent2.len(), 3);
        assert_eq!(recent2[0].text, "after-truncated");
        assert_eq!(recent2[1].text, "after-complete");
        assert_eq!(recent2[2].text, "unterminated-complete");

        let _ = fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn save_reports_committed_entry_separately_when_rotation_cannot_create_temp_file() {
        use std::os::unix::fs::PermissionsExt;

        let (store, dir) = unique_store("rotation-warning", 90);
        store
            .save_transcript("first-entry-padding-text", Some("local"), None, false, None)
            .unwrap();

        // Lock directory against creating new temp files while keeping history.jsonl writable.
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o500)).unwrap();
        let can_create_in_dir = File::create(dir.join(".perm-probe")).is_ok();
        let _ = fs::remove_file(dir.join(".perm-probe"));
        if !can_create_in_dir {
            let outcome = store
                .save_transcript(
                    "second-entry-triggers-rotation",
                    Some("local"),
                    None,
                    false,
                    None,
                )
                .unwrap();
            assert!(outcome.saved);
            assert!(outcome.rotation_warning.is_some());

            let recent = store.get_recent_transcripts(10);
            assert_eq!(recent[0].text, "second-entry-triggers-rotation");

            // Restoring directory permissions allows maintenance rotation without re-appending.
            fs::set_permissions(&dir, fs::Permissions::from_mode(0o700)).unwrap();
            assert!(store.rotate_if_needed().unwrap());
            let after_rotate = store.get_recent_transcripts(10);
            assert_eq!(after_rotate.len(), 1);
            assert_eq!(after_rotate[0].text, "second-entry-triggers-rotation");
        } else {
            fs::set_permissions(&dir, fs::Permissions::from_mode(0o700)).unwrap();
        }

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn rotation_keeps_newest_entries_and_preserves_oversized_single_entry() {
        let (store, dir) = unique_store("rotate", 180);

        for i in 0..6 {
            let entry = TranscriptEntry::with_timestamp(
                format!("2026-11-21T10:00:0{i}.000000"),
                format!("Entry number {i} with some payload text"),
                Some("local".to_string()),
                None,
                false,
                None,
            );
            store.save_entry(&entry).unwrap();
        }

        let after_normal = store.get_recent_transcripts(10);
        assert!(!after_normal.is_empty());
        assert!(after_normal.len() < 6);
        assert!(after_normal[0].text.starts_with("Entry number 5"));

        // Oversized single entry triggers immediate post-append rotation and is still kept intact.
        let huge = TranscriptEntry::with_timestamp(
            "2026-11-21T10:01:00.000000",
            "x".repeat(300),
            None,
            None,
            false,
            None,
        );
        store.save_entry(&huge).unwrap();
        let after_huge = store.get_recent_transcripts(10);
        assert_eq!(after_huge.len(), 1);
        assert_eq!(after_huge[0].text.len(), 300);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn tail_read_falls_back_to_full_read_when_tail_contains_only_invalid_lines() {
        let (store, dir) = unique_store("fallback", MAX_HISTORY_SIZE_BYTES);
        let mut lines = vec![
            r#"{"timestamp":"2026-11-21T09:00:00","text":"valid-older"}"#.to_string(),
            r#"{"timestamp":"2026-11-21T09:01:00","text":"valid-newer"}"#.to_string(),
        ];
        // Add more than tail_max_lines (max(2*3, 2+10) = 12) invalid lines after the valid ones.
        for _ in 0..30 {
            lines.push("not-json-at-all".to_string());
        }
        fs::write(store.paths().history_file(), lines.join("\n") + "\n").unwrap();

        let recent = store.get_recent_transcripts(2);
        assert_eq!(recent.len(), 2);
        assert_eq!(recent[0].text, "valid-newer");
        assert_eq!(recent[1].text, "valid-older");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn concurrent_saves_with_rotation_never_corrupt_jsonl() {
        use std::sync::Arc;
        use std::thread;

        let (store, dir) = unique_store("concurrent", 1024);
        let store = Arc::new(store);
        let handles: Vec<_> = (0..4)
            .map(|id| {
                let s = Arc::clone(&store);
                thread::spawn(move || {
                    for iter in 0..20 {
                        s.save_transcript(
                            &format!("worker-{id}-msg-{iter}"),
                            Some("local"),
                            Some("de"),
                            iter % 2 == 0,
                            None,
                        )
                        .unwrap();
                    }
                })
            })
            .collect();
        for handle in handles {
            handle.join().unwrap();
        }

        let raw = fs::read_to_string(store.paths().history_file()).unwrap();
        let non_empty: Vec<_> = raw.lines().filter(|l| !l.trim().is_empty()).collect();
        assert!(!non_empty.is_empty());
        for line in non_empty {
            assert!(
                TranscriptEntry::from_json_line(line).is_some(),
                "corrupt JSONL line: {line:?}"
            );
        }

        let _ = fs::remove_dir_all(&dir);
    }
}
