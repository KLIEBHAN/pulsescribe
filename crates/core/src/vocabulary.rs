//! Shared custom vocabulary loader and validator (`~/.pulsescribe/vocabulary.json`).
//!
//! Mirrors `utils/vocabulary.py` with instance-scoped file-signature caching,
//! serialized read-modify-write persistence, `0600` atomic writes on Unix, and
//! preservation of unknown top-level JSON keys for Python coexistence.

use std::collections::HashSet;
use std::fs::{self, File};
use std::io::{self, Read};
use std::path::PathBuf;
use std::sync::Mutex;

use serde_json::{Map, Value};
use thiserror::Error;

use crate::atomic_io::write_text_atomic_restricted;
use crate::paths::{build_file_signature, file_signature_from_metadata, AppPaths, FileSignature};

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

/// Non-Cherokee Unicode scalar values whose full case folding (CaseFolding.txt C+F)
/// differs from `char::to_lowercase()`, sorted by codepoint for binary search.
const CASEFOLD_OVERRIDES: &[(char, &str)] = &[
    ('\u{00B5}', "\u{03BC}"),
    ('\u{00DF}', "ss"),
    ('\u{0149}', "\u{02BC}n"),
    ('\u{017F}', "s"),
    ('\u{01F0}', "j\u{030C}"),
    ('\u{0345}', "\u{03B9}"),
    ('\u{0390}', "\u{03B9}\u{0308}\u{0301}"),
    ('\u{03B0}', "\u{03C5}\u{0308}\u{0301}"),
    ('\u{03C2}', "\u{03C3}"),
    ('\u{03D0}', "\u{03B2}"),
    ('\u{03D1}', "\u{03B8}"),
    ('\u{03D5}', "\u{03C6}"),
    ('\u{03D6}', "\u{03C0}"),
    ('\u{03F0}', "\u{03BA}"),
    ('\u{03F1}', "\u{03C1}"),
    ('\u{03F5}', "\u{03B5}"),
    ('\u{0587}', "\u{0565}\u{0582}"),
    ('\u{1C80}', "\u{0432}"),
    ('\u{1C81}', "\u{0434}"),
    ('\u{1C82}', "\u{043E}"),
    ('\u{1C83}', "\u{0441}"),
    ('\u{1C84}', "\u{0442}"),
    ('\u{1C85}', "\u{0442}"),
    ('\u{1C86}', "\u{044A}"),
    ('\u{1C87}', "\u{0463}"),
    ('\u{1C88}', "\u{A64B}"),
    ('\u{1E96}', "h\u{0331}"),
    ('\u{1E97}', "t\u{0308}"),
    ('\u{1E98}', "w\u{030A}"),
    ('\u{1E99}', "y\u{030A}"),
    ('\u{1E9A}', "a\u{02BE}"),
    ('\u{1E9B}', "\u{1E61}"),
    ('\u{1E9E}', "ss"),
    ('\u{1F50}', "\u{03C5}\u{0313}"),
    ('\u{1F52}', "\u{03C5}\u{0313}\u{0300}"),
    ('\u{1F54}', "\u{03C5}\u{0313}\u{0301}"),
    ('\u{1F56}', "\u{03C5}\u{0313}\u{0342}"),
    ('\u{1F80}', "\u{1F00}\u{03B9}"),
    ('\u{1F81}', "\u{1F01}\u{03B9}"),
    ('\u{1F82}', "\u{1F02}\u{03B9}"),
    ('\u{1F83}', "\u{1F03}\u{03B9}"),
    ('\u{1F84}', "\u{1F04}\u{03B9}"),
    ('\u{1F85}', "\u{1F05}\u{03B9}"),
    ('\u{1F86}', "\u{1F06}\u{03B9}"),
    ('\u{1F87}', "\u{1F07}\u{03B9}"),
    ('\u{1F88}', "\u{1F00}\u{03B9}"),
    ('\u{1F89}', "\u{1F01}\u{03B9}"),
    ('\u{1F8A}', "\u{1F02}\u{03B9}"),
    ('\u{1F8B}', "\u{1F03}\u{03B9}"),
    ('\u{1F8C}', "\u{1F04}\u{03B9}"),
    ('\u{1F8D}', "\u{1F05}\u{03B9}"),
    ('\u{1F8E}', "\u{1F06}\u{03B9}"),
    ('\u{1F8F}', "\u{1F07}\u{03B9}"),
    ('\u{1F90}', "\u{1F20}\u{03B9}"),
    ('\u{1F91}', "\u{1F21}\u{03B9}"),
    ('\u{1F92}', "\u{1F22}\u{03B9}"),
    ('\u{1F93}', "\u{1F23}\u{03B9}"),
    ('\u{1F94}', "\u{1F24}\u{03B9}"),
    ('\u{1F95}', "\u{1F25}\u{03B9}"),
    ('\u{1F96}', "\u{1F26}\u{03B9}"),
    ('\u{1F97}', "\u{1F27}\u{03B9}"),
    ('\u{1F98}', "\u{1F20}\u{03B9}"),
    ('\u{1F99}', "\u{1F21}\u{03B9}"),
    ('\u{1F9A}', "\u{1F22}\u{03B9}"),
    ('\u{1F9B}', "\u{1F23}\u{03B9}"),
    ('\u{1F9C}', "\u{1F24}\u{03B9}"),
    ('\u{1F9D}', "\u{1F25}\u{03B9}"),
    ('\u{1F9E}', "\u{1F26}\u{03B9}"),
    ('\u{1F9F}', "\u{1F27}\u{03B9}"),
    ('\u{1FA0}', "\u{1F60}\u{03B9}"),
    ('\u{1FA1}', "\u{1F61}\u{03B9}"),
    ('\u{1FA2}', "\u{1F62}\u{03B9}"),
    ('\u{1FA3}', "\u{1F63}\u{03B9}"),
    ('\u{1FA4}', "\u{1F64}\u{03B9}"),
    ('\u{1FA5}', "\u{1F65}\u{03B9}"),
    ('\u{1FA6}', "\u{1F66}\u{03B9}"),
    ('\u{1FA7}', "\u{1F67}\u{03B9}"),
    ('\u{1FA8}', "\u{1F60}\u{03B9}"),
    ('\u{1FA9}', "\u{1F61}\u{03B9}"),
    ('\u{1FAA}', "\u{1F62}\u{03B9}"),
    ('\u{1FAB}', "\u{1F63}\u{03B9}"),
    ('\u{1FAC}', "\u{1F64}\u{03B9}"),
    ('\u{1FAD}', "\u{1F65}\u{03B9}"),
    ('\u{1FAE}', "\u{1F66}\u{03B9}"),
    ('\u{1FAF}', "\u{1F67}\u{03B9}"),
    ('\u{1FB2}', "\u{1F70}\u{03B9}"),
    ('\u{1FB3}', "\u{03B1}\u{03B9}"),
    ('\u{1FB4}', "\u{03AC}\u{03B9}"),
    ('\u{1FB6}', "\u{03B1}\u{0342}"),
    ('\u{1FB7}', "\u{03B1}\u{0342}\u{03B9}"),
    ('\u{1FBC}', "\u{03B1}\u{03B9}"),
    ('\u{1FBE}', "\u{03B9}"),
    ('\u{1FC2}', "\u{1F74}\u{03B9}"),
    ('\u{1FC3}', "\u{03B7}\u{03B9}"),
    ('\u{1FC4}', "\u{03AE}\u{03B9}"),
    ('\u{1FC6}', "\u{03B7}\u{0342}"),
    ('\u{1FC7}', "\u{03B7}\u{0342}\u{03B9}"),
    ('\u{1FCC}', "\u{03B7}\u{03B9}"),
    ('\u{1FD2}', "\u{03B9}\u{0308}\u{0300}"),
    ('\u{1FD3}', "\u{03B9}\u{0308}\u{0301}"),
    ('\u{1FD6}', "\u{03B9}\u{0342}"),
    ('\u{1FD7}', "\u{03B9}\u{0308}\u{0342}"),
    ('\u{1FE2}', "\u{03C5}\u{0308}\u{0300}"),
    ('\u{1FE3}', "\u{03C5}\u{0308}\u{0301}"),
    ('\u{1FE4}', "\u{03C1}\u{0313}"),
    ('\u{1FE6}', "\u{03C5}\u{0342}"),
    ('\u{1FE7}', "\u{03C5}\u{0308}\u{0342}"),
    ('\u{1FF2}', "\u{1F7C}\u{03B9}"),
    ('\u{1FF3}', "\u{03C9}\u{03B9}"),
    ('\u{1FF4}', "\u{03CE}\u{03B9}"),
    ('\u{1FF6}', "\u{03C9}\u{0342}"),
    ('\u{1FF7}', "\u{03C9}\u{0342}\u{03B9}"),
    ('\u{1FFC}', "\u{03C9}\u{03B9}"),
    ('\u{FB00}', "ff"),
    ('\u{FB01}', "fi"),
    ('\u{FB02}', "fl"),
    ('\u{FB03}', "ffi"),
    ('\u{FB04}', "ffl"),
    ('\u{FB05}', "st"),
    ('\u{FB06}', "st"),
    ('\u{FB13}', "\u{0574}\u{0576}"),
    ('\u{FB14}', "\u{0574}\u{0565}"),
    ('\u{FB15}', "\u{0574}\u{056B}"),
    ('\u{FB16}', "\u{057E}\u{0576}"),
    ('\u{FB17}', "\u{0574}\u{056D}"),
];

/// Full Unicode case-folding key matching Python `str.casefold()`.
fn casefold_key(keyword: &str) -> String {
    if keyword.is_ascii() {
        return keyword.to_ascii_lowercase();
    }
    let mut out = String::with_capacity(keyword.len());
    for ch in keyword.chars() {
        match ch {
            '\u{13A0}'..='\u{13F5}' => out.push(ch),
            '\u{13F8}'..='\u{13FD}' => {
                if let Some(folded) = char::from_u32(ch as u32 - 8) {
                    out.push(folded);
                }
            }
            '\u{AB70}'..='\u{ABBF}' => {
                if let Some(folded) = char::from_u32(ch as u32 - 0x97D0) {
                    out.push(folded);
                }
            }
            _ => {
                if let Ok(idx) = CASEFOLD_OVERRIDES.binary_search_by_key(&ch, |&(k, _)| k) {
                    out.push_str(CASEFOLD_OVERRIDES[idx].1);
                } else {
                    out.extend(ch.to_lowercase());
                }
            }
        }
    }
    out
}

#[cfg(unix)]
fn is_owner_only_permissions(meta: &fs::Metadata) -> bool {
    use std::os::unix::fs::PermissionsExt;
    meta.permissions().mode() & 0o777 == 0o600
}

#[cfg(not(unix))]
fn is_owner_only_permissions(_meta: &fs::Metadata) -> bool {
    true
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
        let stat_signature = match build_file_signature(&vocab_file) {
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
                if cached.signature == stat_signature {
                    return VocabularyState {
                        data: cached.data.clone(),
                        issues: cached.issues.clone(),
                        signature: Some(stat_signature),
                    };
                }
            }
        }

        // Open once and derive signature + contents from the same handle so transient
        // read errors are never cached and concurrent replacements stay consistent.
        let mut file = match File::open(&vocab_file) {
            Ok(file) => file,
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

        let signature = match file.metadata() {
            Ok(meta) => file_signature_from_metadata(&meta),
            Err(err) => {
                self.invalidate_cache();
                return VocabularyState {
                    data: empty_vocabulary_object(),
                    issues: vec![format!("The vocabulary file could not be read: {err}")],
                    signature: None,
                };
            }
        };

        let mut raw_bytes = Vec::new();
        if let Err(err) = file.read_to_end(&mut raw_bytes) {
            self.invalidate_cache();
            return VocabularyState {
                data: empty_vocabulary_object(),
                issues: vec![format!("The vocabulary file could not be read: {err}")],
                signature: None,
            };
        }

        let (data, issues) = match String::from_utf8(raw_bytes) {
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
    /// top-level JSON keys and skipping disk writes when on-disk data and permissions
    /// already match.
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

        let (existing_sig, existing_owner_only, existing_object) = match File::open(&vocab_file) {
            Ok(mut file) => {
                let meta = file.metadata()?;
                let sig = file_signature_from_metadata(&meta);
                let owner_only = is_owner_only_permissions(&meta);
                let mut raw_bytes = Vec::new();
                file.read_to_end(&mut raw_bytes)?;
                let parsed = String::from_utf8(raw_bytes)
                    .ok()
                    .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
                    .and_then(|val| val.as_object().cloned());
                (Some(sig), owner_only, parsed)
            }
            Err(err) if err.kind() == io::ErrorKind::NotFound => (None, false, None),
            Err(err) => return Err(VocabularyError::Io(err)),
        };

        let mut data = existing_object.clone().unwrap_or_default();
        data.insert(
            "keywords".to_string(),
            keywords_to_json_array(&analysis.normalized),
        );

        let on_disk_issues = build_keyword_issues(0, 0, analysis.normalized.len());

        if existing_owner_only {
            if let (Some(sig), Some(existing)) = (existing_sig, existing_object.as_ref()) {
                if existing == &data {
                    self.update_cache(sig, &data, &on_disk_issues);
                    return Ok(VocabularyState {
                        data,
                        issues: analysis.issues,
                        signature: Some(sig),
                    });
                }
            }
        }

        let serialized = serde_json::to_string_pretty(&data)?;
        write_text_atomic_restricted(&vocab_file, &serialized)?;
        self.invalidate_cache();

        Ok(VocabularyState {
            data,
            issues: analysis.issues,
            signature: None,
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

        let analysis = analyze_vocabulary_text(Some(
            "Whisper, whisper, Straße, STRASSE, ſage, sage, ﬁle, FILE, Groq",
        ));
        assert_eq!(
            analysis.keywords,
            vec!["Whisper", "Straße", "ſage", "ﬁle", "Groq"]
        );
        assert_eq!(analysis.input_count, 9);
        assert_eq!(analysis.duplicate_count, 4);
        assert_eq!(analysis.keyword_count(), 5);
        assert_eq!(analysis.issues, vec!["4 duplicate keywords found."]);
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
    fn save_sets_0600_and_enforces_mode_before_noop_and_preserves_unreadable_files() {
        use std::os::unix::fs::PermissionsExt;

        let (store, dir) = unique_store("perms");
        let path = store.paths().vocabulary_file();

        // Matching content with loose 0644 mode must NOT take the no-op shortcut.
        fs::write(&path, "{\n  \"keywords\": [\n    \"PulseScribe\"\n  ]\n}").unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o644)).unwrap();

        let saved = store.save_state(&["PulseScribe"]).unwrap();
        assert_eq!(saved.signature, None);
        let mode = fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);

        // Once 0600 and matching, a subsequent save skips rewriting disk.
        let sig_before = build_file_signature(&path).unwrap();
        let noop = store.save_state(&["PulseScribe"]).unwrap();
        assert_eq!(noop.signature, Some(sig_before));

        // Unreadable existing file must NOT be overwritten (nor cached on load).
        fs::write(
            &path,
            r#"{"keywords": ["PulseScribe"], "extra_field": "preserve-me"}"#,
        )
        .unwrap();
        store.invalidate_cache();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o000)).unwrap();
        let can_read_000 = File::open(&path).is_ok();
        if !can_read_000 {
            let load_err = store.load_state();
            assert!(load_err.keywords().is_empty());
            assert_eq!(load_err.signature, None);
            assert!(store.save(&["NewWord"]).is_err());

            // After restoring read permissions, load retries cleanly and extra_field is intact.
            fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
            let recovered = store.load_state();
            assert_eq!(recovered.keywords(), vec!["PulseScribe"]);
            assert_eq!(
                recovered.data.get("extra_field"),
                Some(&Value::String("preserve-me".to_string()))
            );
        } else {
            fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
        }

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
