//! Shared custom vocabulary loader and validator (`~/.pulsescribe/vocabulary.json`).
//!
//! Mirrors `utils/vocabulary.py` with instance-scoped file-signature caching,
//! serialized read-modify-write persistence, `0600` atomic writes on Unix, and
//! preservation of unknown top-level JSON keys for Python coexistence.

use std::collections::HashSet;
use std::fs;
use std::io;
use std::path::PathBuf;
use std::sync::Mutex;

use serde_json::{Map, Value};
use thiserror::Error;

use crate::atomic_io::write_text_atomic_restricted;
use crate::paths::{build_file_signature, AppPaths, FileSignature};

/// Maximum recommended keywords for local Whisper/Parakeet biasing.
pub const LOCAL_KEYWORD_LIMIT: usize = 50;
/// Maximum recommended keywords for Deepgram keyterm/keyword biasing.
pub const DEEPGRAM_KEYWORD_LIMIT: usize = 100;

/// Errors produced by `VocabularyStore` write operations.
#[derive(Debug, Error)]
pub enum VocabularyError {
    #[error("I/O error: {0}")]
    Io(#[from] io::Error),
    #[error("JSON serialization error: {0}")]
    Json(#[from] serde_json::Error),
}

/// Normalized analysis of vocabulary editor text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VocabularyInputAnalysis {
    pub keywords: Vec<String>,
    pub issues: Vec<String>,
    pub input_count: usize,
    pub duplicate_count: usize,
    pub exceeds_local_limit: bool,
    pub exceeds_deepgram_limit: bool,
}

impl VocabularyInputAnalysis {
    pub fn keyword_count(&self) -> usize {
        self.keywords.len()
    }
}

/// Loaded or persisted vocabulary JSON state together with validation issues
/// and the file metadata signature observed on disk.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VocabularyState {
    pub data: Map<String, Value>,
    pub issues: Vec<String>,
    pub signature: Option<FileSignature>,
}

impl VocabularyState {
    /// Extract the normalized keyword strings from `data["keywords"]`.
    pub fn keywords(&self) -> Vec<String> {
        self.data
            .get("keywords")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(Value::as_str)
                    .map(ToOwned::to_owned)
                    .collect()
            })
            .unwrap_or_default()
    }
}

#[derive(Debug, Clone)]
struct KeywordAnalysis {
    normalized: Vec<String>,
    issues: Vec<String>,
    input_count: usize,
    duplicate_count: usize,
    exceeds_local_limit: bool,
    exceeds_deepgram_limit: bool,
}

#[derive(Debug, Clone)]
struct CachedVocabulary {
    signature: FileSignature,
    data: Map<String, Value>,
    issues: Vec<String>,
}

/// Case-folding key matching Python `str.casefold()` for Latin/German scripts.
fn casefold_key(keyword: &str) -> String {
    keyword.to_lowercase().replace('ß', "ss")
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

fn dedupe_keywords(keywords: Vec<String>) -> Vec<String> {
    let mut seen = HashSet::with_capacity(keywords.len());
    let mut result = Vec::with_capacity(keywords.len());
    for keyword in keywords {
        let key = casefold_key(&keyword);
        if seen.insert(key) {
            result.push(keyword);
        }
    }
    result
}

fn build_keyword_issues(
    non_string_count: usize,
    duplicate_count: usize,
    keyword_count: usize,
) -> Vec<String> {
    let mut issues = Vec::new();
    if non_string_count > 0 {
        issues.push(format!(
            "{non_string_count} keywords are not text and are ignored."
        ));
    }
    if duplicate_count > 0 {
        issues.push(format!("{duplicate_count} duplicate keywords found."));
    }
    if keyword_count > DEEPGRAM_KEYWORD_LIMIT {
        issues.push(format!(
            "{keyword_count} keywords: Deepgram uses at most {DEEPGRAM_KEYWORD_LIMIT}, Local at most {LOCAL_KEYWORD_LIMIT}."
        ));
    } else if keyword_count > LOCAL_KEYWORD_LIMIT {
        issues.push(format!(
            "{keyword_count} keywords: Local uses at most {LOCAL_KEYWORD_LIMIT}."
        ));
    }
    issues
}

fn analyze_cleaned_keywords(cleaned: Vec<String>, non_string_count: usize) -> KeywordAnalysis {
    let input_count = cleaned.len();
    let normalized = dedupe_keywords(cleaned);
    let duplicate_count = input_count - normalized.len();
    let keyword_count = normalized.len();
    let issues = build_keyword_issues(non_string_count, duplicate_count, keyword_count);

    KeywordAnalysis {
        normalized,
        issues,
        input_count,
        duplicate_count,
        exceeds_local_limit: keyword_count > LOCAL_KEYWORD_LIMIT,
        exceeds_deepgram_limit: keyword_count > DEEPGRAM_KEYWORD_LIMIT,
    }
}

fn analyze_keywords_value(raw_keywords: Option<&Value>) -> KeywordAnalysis {
    let Some(raw) = raw_keywords else {
        return KeywordAnalysis {
            normalized: Vec::new(),
            issues: Vec::new(),
            input_count: 0,
            duplicate_count: 0,
            exceeds_local_limit: false,
            exceeds_deepgram_limit: false,
        };
    };
    if raw.is_null() {
        return KeywordAnalysis {
            normalized: Vec::new(),
            issues: Vec::new(),
            input_count: 0,
            duplicate_count: 0,
            exceeds_local_limit: false,
            exceeds_deepgram_limit: false,
        };
    }

    let Some(items) = raw.as_array() else {
        return KeywordAnalysis {
            normalized: Vec::new(),
            issues: vec!["'keywords' must be a list.".to_string()],
            input_count: 0,
            duplicate_count: 0,
            exceeds_local_limit: false,
            exceeds_deepgram_limit: false,
        };
    };

    let mut cleaned = Vec::with_capacity(items.len());
    let mut non_string_count = 0usize;
    for item in items {
        match item.as_str() {
            Some(s) => {
                let trimmed = s.trim();
                if !trimmed.is_empty() {
                    cleaned.push(trimmed.to_string());
                }
            }
            None => non_string_count += 1,
        }
    }

    analyze_cleaned_keywords(cleaned, non_string_count)
}

fn empty_vocabulary_object() -> Map<String, Value> {
    let mut map = Map::new();
    map.insert("keywords".to_string(), Value::Array(Vec::new()));
    map
}

fn keywords_to_json_array(keywords: &[String]) -> Value {
    Value::Array(keywords.iter().cloned().map(Value::String).collect())
}

/// Split editor text into keyword candidates using lines and commas.
pub fn split_vocabulary_text(raw_text: Option<&str>) -> Vec<String> {
    let Some(raw) = raw_text else {
        return Vec::new();
    };
    let mut parts = Vec::new();
    for line in split_lines(raw) {
        for chunk in line.split(',') {
            let keyword = chunk.trim();
            if !keyword.is_empty() {
                parts.push(keyword.to_string());
            }
        }
    }
    parts
}

/// Analyze vocabulary editor text using the same normalization and warnings as persistence.
pub fn analyze_vocabulary_text(raw_text: Option<&str>) -> VocabularyInputAnalysis {
    let analysis = analyze_cleaned_keywords(split_vocabulary_text(raw_text), 0);
    VocabularyInputAnalysis {
        keywords: analysis.normalized,
        issues: analysis.issues,
        input_count: analysis.input_count,
        duplicate_count: analysis.duplicate_count,
        exceeds_local_limit: analysis.exceeds_local_limit,
        exceeds_deepgram_limit: analysis.exceeds_deepgram_limit,
    }
}

/// Parse vocabulary JSON text and return normalized object data plus validation issues.
pub fn parse_vocabulary_text(raw_text: &str) -> (Map<String, Value>, Vec<String>) {
    let Ok(parsed_value) = serde_json::from_str::<Value>(raw_text) else {
        return (
            empty_vocabulary_object(),
            vec!["The vocabulary file is not valid JSON.".to_string()],
        );
    };

    let Some(mut obj) = parsed_value.as_object().cloned() else {
        return (
            empty_vocabulary_object(),
            vec!["The vocabulary file must be a JSON object.".to_string()],
        );
    };

    let analysis = analyze_keywords_value(obj.get("keywords"));
    obj.insert(
        "keywords".to_string(),
        keywords_to_json_array(&analysis.normalized),
    );
    (obj, analysis.issues)
}

/// Reads and writes `~/.pulsescribe/vocabulary.json` with signature-based caching
/// and serialized atomic updates.
#[derive(Debug)]
pub struct VocabularyStore {
    paths: AppPaths,
    cache: Mutex<Option<CachedVocabulary>>,
    write_lock: Mutex<()>,
}

impl VocabularyStore {
    /// Create a store rooted at an explicit configuration directory.
    pub fn new(config_dir: impl Into<PathBuf>) -> Self {
        Self {
            paths: AppPaths::new(config_dir),
            cache: Mutex::new(None),
            write_lock: Mutex::new(()),
        }
    }

    /// Create a store pointing at the current user's `~/.pulsescribe/`.
    pub fn default_user() -> Option<Self> {
        AppPaths::default_user().map(|paths| Self {
            paths,
            cache: Mutex::new(None),
            write_lock: Mutex::new(()),
        })
    }

    pub fn paths(&self) -> &AppPaths {
        &self.paths
    }

    /// Load normalized vocabulary data, validation issues, and file signature in one pass.
    pub fn load_state(&self) -> VocabularyState {
        let vocab_file = self.paths.vocabulary_file();
        let signature = match build_file_signature(&vocab_file) {
            Ok(sig) => sig,
            Err(err) if err.kind() == io::ErrorKind::NotFound => {
                self.invalidate_cache();
                return VocabularyState {
                    data: empty_vocabulary_object(),
                    issues: Vec::new(),
                    signature: None,
                };
            }
            Err(err) => {
                self.invalidate_cache();
                return VocabularyState {
                    data: empty_vocabulary_object(),
                    issues: vec![format!("The vocabulary file could not be read: {err}")],
                    signature: None,
                };
            }
        };

        {
            let guard = self.cache.lock().unwrap_or_else(|e| e.into_inner());
            if let Some(cached) = guard.as_ref() {
                if cached.signature == signature {
                    return VocabularyState {
                        data: cached.data.clone(),
                        issues: cached.issues.clone(),
                        signature: Some(signature),
                    };
                }
            }
        }

        let (data, issues) = match fs::read_to_string(&vocab_file) {
            Ok(raw_text) => parse_vocabulary_text(&raw_text),
            Err(err) => (
                empty_vocabulary_object(),
                vec![format!("The vocabulary file could not be read: {err}")],
            ),
        };

        self.update_cache(signature, &data, &issues);
        VocabularyState {
            data,
            issues,
            signature: Some(signature),
        }
    }

    /// Load the normalized vocabulary JSON object (always containing `"keywords"`).
    pub fn load(&self) -> Map<String, Value> {
        self.load_state().data
    }

    /// Load only the normalized keyword strings.
    pub fn load_keywords(&self) -> Vec<String> {
        self.load_state().keywords()
    }

    /// Validate the vocabulary file and return warning messages.
    pub fn validate(&self) -> Vec<String> {
        self.load_state().issues
    }

    /// Normalize and persist keywords atomically (`0600` on Unix), preserving unknown
    /// top-level JSON keys and skipping disk writes when on-disk data already matches.
    pub fn save_state<S: AsRef<str>>(
        &self,
        keywords: &[S],
    ) -> Result<VocabularyState, VocabularyError> {
        let _write_guard = self.write_lock.lock().unwrap_or_else(|e| e.into_inner());
        let vocab_file = self.paths.vocabulary_file();

        let cleaned: Vec<String> = keywords
            .iter()
            .filter_map(|item| {
                let trimmed = item.as_ref().trim();
                (!trimmed.is_empty()).then(|| trimmed.to_string())
            })
            .collect();
        let analysis = analyze_cleaned_keywords(cleaned, 0);

        let current_signature = build_file_signature(&vocab_file).ok();
        let existing_object = current_signature.and_then(|_| {
            fs::read_to_string(&vocab_file)
                .ok()
                .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
                .and_then(|val| val.as_object().cloned())
        });

        let mut data = existing_object.clone().unwrap_or_default();
        data.insert(
            "keywords".to_string(),
            keywords_to_json_array(&analysis.normalized),
        );

        let on_disk_issues = build_keyword_issues(0, 0, analysis.normalized.len());

        if let (Some(sig), Some(existing)) = (current_signature, existing_object.as_ref()) {
            if existing == &data {
                self.update_cache(sig, &data, &on_disk_issues);
                return Ok(VocabularyState {
                    data,
                    issues: analysis.issues,
                    signature: Some(sig),
                });
            }
        }

        let serialized = serde_json::to_string_pretty(&data)?;
        write_text_atomic_restricted(&vocab_file, &serialized)?;

        let new_signature = match build_file_signature(&vocab_file) {
            Ok(sig) => {
                self.update_cache(sig, &data, &on_disk_issues);
                Some(sig)
            }
            Err(_) => {
                self.invalidate_cache();
                None
            }
        };

        Ok(VocabularyState {
            data,
            issues: analysis.issues,
            signature: new_signature,
        })
    }

    /// Normalize and persist keywords atomically.
    pub fn save<S: AsRef<str>>(&self, keywords: &[S]) -> Result<(), VocabularyError> {
        self.save_state(keywords).map(|_| ())
    }

    fn update_cache(&self, signature: FileSignature, data: &Map<String, Value>, issues: &[String]) {
        let mut guard = self.cache.lock().unwrap_or_else(|e| e.into_inner());
        *guard = Some(CachedVocabulary {
            signature,
            data: data.clone(),
            issues: issues.to_vec(),
        });
    }

    fn invalidate_cache(&self) {
        let mut guard = self.cache.lock().unwrap_or_else(|e| e.into_inner());
        *guard = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn unique_store(tag: &str) -> (VocabularyStore, PathBuf) {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "pulsescribe-core-vocab-{tag}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(&dir).unwrap();
        (VocabularyStore::new(&dir), dir)
    }

    #[test]
    fn split_and_analyze_handles_lines_commas_duplicates_and_limits() {
        let parts = split_vocabulary_text(Some("PulseScribe, Whisper\r\n  Deepgram , , \rGroq"));
        assert_eq!(parts, vec!["PulseScribe", "Whisper", "Deepgram", "Groq"]);

        let analysis = analyze_vocabulary_text(Some("Whisper, whisper, Straße, STRASSE, Groq"));
        assert_eq!(analysis.keywords, vec!["Whisper", "Straße", "Groq"]);
        assert_eq!(analysis.input_count, 5);
        assert_eq!(analysis.duplicate_count, 2);
        assert_eq!(analysis.keyword_count(), 3);
        assert_eq!(analysis.issues, vec!["2 duplicate keywords found."]);
        assert!(!analysis.exceeds_local_limit);
        assert!(!analysis.exceeds_deepgram_limit);

        let many: Vec<String> = (0..101).map(|i| format!("term{i}")).collect();
        let over_deepgram = analyze_vocabulary_text(Some(&many.join(",")));
        assert!(over_deepgram.exceeds_local_limit);
        assert!(over_deepgram.exceeds_deepgram_limit);
        assert_eq!(
            over_deepgram.issues,
            vec!["101 keywords: Deepgram uses at most 100, Local at most 50."]
        );
    }

    #[test]
    fn store_preserves_extra_fields_skips_noop_and_handles_corrupt_files() {
        let (store, dir) = unique_store("store");
        assert!(store.load_keywords().is_empty());
        assert!(store.validate().is_empty());

        fs::write(
            store.paths().vocabulary_file(),
            r#"{"keywords": ["PulseScribe", "pulsescribe", 42], "extra": {"keep": true}}"#,
        )
        .unwrap();

        let state = store.load_state();
        assert_eq!(state.keywords(), vec!["PulseScribe"]);
        assert_eq!(
            state.issues,
            vec![
                "1 keywords are not text and are ignored.",
                "1 duplicate keywords found.",
            ]
        );
        assert_eq!(
            state.data.get("extra"),
            Some(&serde_json::json!({"keep": true}))
        );

        // Save cleans duplicates on disk while preserving extra fields.
        let saved = store.save_state(&["PulseScribe", "Deepgram"]).unwrap();
        assert_eq!(saved.keywords(), vec!["PulseScribe", "Deepgram"]);
        assert_eq!(
            saved.data.get("extra"),
            Some(&serde_json::json!({"keep": true}))
        );

        // Second identical save (even with duplicate input) skips rewriting disk while
        // reporting input warnings and keeping cached on-disk validation clean.
        let sig_before = build_file_signature(&store.paths().vocabulary_file()).unwrap();
        let noop = store
            .save_state(&["PulseScribe", "pulsescribe", "Deepgram"])
            .unwrap();
        assert_eq!(noop.signature, Some(sig_before));
        assert_eq!(noop.issues, vec!["1 duplicate keywords found."]);
        assert!(store.validate().is_empty());

        // Corrupt UTF-8 and corrupt JSON recover safely.
        fs::write(store.paths().vocabulary_file(), [0xff, 0xfe, 0x00]).unwrap();
        store.invalidate_cache();
        let bad_utf8 = store.load_state();
        assert!(bad_utf8.keywords().is_empty());
        assert_eq!(bad_utf8.issues.len(), 1);
        assert!(bad_utf8.issues[0].starts_with("The vocabulary file could not be read:"));

        // Saving [] over a corrupt file rewrites valid JSON instead of treating it as a no-op.
        let repaired = store.save_state::<&str>(&[]).unwrap();
        assert!(repaired.keywords().is_empty());
        assert!(repaired.issues.is_empty());
        assert!(store.validate().is_empty());

        let _ = fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn save_sets_0600_permissions() {
        use std::os::unix::fs::PermissionsExt;

        let (store, dir) = unique_store("perms");
        store.save(&["PulseScribe"]).unwrap();
        let mode = fs::metadata(store.paths().vocabulary_file())
            .unwrap()
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(mode, 0o600);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn concurrent_saves_remain_valid_json() {
        use std::sync::Arc;
        use std::thread;

        let (store, dir) = unique_store("concurrent");
        let store = Arc::new(store);
        let handles: Vec<_> = (0..4)
            .map(|id| {
                let s = Arc::clone(&store);
                thread::spawn(move || {
                    for iter in 0..15 {
                        s.save(&[format!("worker{id}"), format!("iter{iter}")])
                            .unwrap();
                    }
                })
            })
            .collect();
        for handle in handles {
            handle.join().unwrap();
        }

        let final_state = store.load_state();
        assert!(final_state.issues.is_empty());
        assert_eq!(final_state.keywords().len(), 2);

        let _ = fs::remove_dir_all(&dir);
    }
}
