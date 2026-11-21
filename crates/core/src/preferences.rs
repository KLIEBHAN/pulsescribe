//! Persistent `~/.pulsescribe/preferences.json` and `~/.pulsescribe/.env` store.
//!
//! Mirrors `utils/preferences.py` while keeping all state scoped to an instance
//! (no global mutable caches) and preserving unknown JSON keys for coexistence
//! with the Python app.

use std::collections::BTreeMap;
use std::fs;
use std::io;
use std::path::PathBuf;
use std::sync::Mutex;

use serde_json::{Map, Value};
use thiserror::Error;

use crate::atomic_io::{write_text_atomic, write_text_atomic_restricted};
use crate::env::{
    apply_env_updates_to_lines, parse_bool, read_env_file_values, render_env_lines, EnvError,
};
use crate::onboarding::{OnboardingChoice, OnboardingStep};
use crate::paths::{build_file_signature, AppPaths, FileSignature};

const TOGGLE_HOTKEY_ENV_KEY: &str = "PULSESCRIBE_TOGGLE_HOTKEY";
const HOLD_HOTKEY_ENV_KEY: &str = "PULSESCRIBE_HOLD_HOTKEY";
const LEGACY_HOTKEY_ENV_KEYS: [&str; 2] = ["PULSESCRIBE_HOTKEY", "PULSESCRIBE_HOTKEY_MODE"];

/// Hotkey slot targeted by `PreferencesStore::apply_hotkey_setting`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum HotkeyKind {
    Toggle,
    Hold,
}

impl HotkeyKind {
    pub fn from_str_or_toggle(raw: &str) -> Self {
        match raw.trim().to_ascii_lowercase().as_str() {
            "hold" => Self::Hold,
            _ => Self::Toggle,
        }
    }

    pub const fn env_key(self) -> &'static str {
        match self {
            Self::Toggle => TOGGLE_HOTKEY_ENV_KEY,
            Self::Hold => HOLD_HOTKEY_ENV_KEY,
        }
    }
}

/// Errors produced by `PreferencesStore`.
#[derive(Debug, Error)]
pub enum PreferencesError {
    #[error("I/O error: {0}")]
    Io(#[from] io::Error),
    #[error("JSON serialization error: {0}")]
    Json(#[from] serde_json::Error),
    #[error(transparent)]
    Env(#[from] EnvError),
}

type CachedMap<T> = Option<(FileSignature, T)>;

/// Reads and writes `~/.pulsescribe/preferences.json` and `~/.pulsescribe/.env`
/// with file-signature caching, serialized read-modify-write transactions, and
/// crash-safe atomic updates.
#[derive(Debug)]
pub struct PreferencesStore {
    paths: AppPaths,
    env_cache: Mutex<CachedMap<BTreeMap<String, String>>>,
    prefs_cache: Mutex<CachedMap<Map<String, Value>>>,
    env_write_lock: Mutex<()>,
    prefs_write_lock: Mutex<()>,
}

impl PreferencesStore {
    /// Create a store rooted at an explicit configuration directory.
    pub fn new(config_dir: impl Into<PathBuf>) -> Self {
        Self {
            paths: AppPaths::new(config_dir),
            env_cache: Mutex::new(None),
            prefs_cache: Mutex::new(None),
            env_write_lock: Mutex::new(()),
            prefs_write_lock: Mutex::new(()),
        }
    }

    /// Create a store pointing at the current user's `~/.pulsescribe/`.
    pub fn default_user() -> Option<Self> {
        AppPaths::default_user().map(|paths| Self {
            paths,
            env_cache: Mutex::new(None),
            prefs_cache: Mutex::new(None),
            env_write_lock: Mutex::new(()),
            prefs_write_lock: Mutex::new(()),
        })
    }

    pub fn paths(&self) -> &AppPaths {
        &self.paths
    }

    /// Read `~/.pulsescribe/preferences.json`, falling back to an empty object on
    /// missing, malformed, or non-object files.
    pub fn load_preferences(&self) -> Map<String, Value> {
        let prefs_path = self.paths.preferences_file();
        let Ok(sig) = build_file_signature(&prefs_path) else {
            self.invalidate_preferences_cache();
            return Map::new();
        };

        {
            let guard = self.prefs_cache.lock().unwrap_or_else(|e| e.into_inner());
            if let Some((cached_sig, cached)) = guard.as_ref() {
                if *cached_sig == sig {
                    return cached.clone();
                }
            }
        }

        let loaded = fs::read_to_string(&prefs_path)
            .ok()
            .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
            .and_then(|val| val.as_object().cloned())
            .unwrap_or_default();

        let mut guard = self.prefs_cache.lock().unwrap_or_else(|e| e.into_inner());
        *guard = Some((sig, loaded.clone()));
        loaded
    }

    /// Persist `preferences.json` atomically as pretty-printed JSON.
    pub fn save_preferences(&self, prefs: &Map<String, Value>) -> Result<(), PreferencesError> {
        let _write_guard = self
            .prefs_write_lock
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        self.save_preferences_locked(prefs)
    }

    fn save_preferences_locked(&self, prefs: &Map<String, Value>) -> Result<(), PreferencesError> {
        let serialized = serde_json::to_string_pretty(prefs)?;
        write_text_atomic(&self.paths.preferences_file(), &serialized)?;
        self.invalidate_preferences_cache();
        Ok(())
    }

    fn mutate_preferences(
        &self,
        mutator: impl FnOnce(&mut Map<String, Value>),
    ) -> Result<(), PreferencesError> {
        let _write_guard = self
            .prefs_write_lock
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let mut prefs = self.load_preferences();
        mutator(&mut prefs);
        self.save_preferences_locked(&prefs)
    }

    fn invalidate_preferences_cache(&self) {
        let mut guard = self.prefs_cache.lock().unwrap_or_else(|e| e.into_inner());
        *guard = None;
    }

    fn invalidate_env_cache(&self) {
        let mut guard = self.env_cache.lock().unwrap_or_else(|e| e.into_inner());
        *guard = None;
    }

    pub fn has_seen_onboarding(&self) -> bool {
        coerce_bool_preference(self.load_preferences().get("has_seen_onboarding"), false)
    }

    pub fn set_onboarding_seen(&self, seen: bool) -> Result<(), PreferencesError> {
        self.mutate_preferences(|prefs| {
            prefs.insert("has_seen_onboarding".to_string(), Value::Bool(seen));
        })
    }

    /// Persisted onboarding step with backwards-compat fallback when `onboarding_step`
    /// is absent but `has_seen_onboarding` is true.
    pub fn get_onboarding_step(&self) -> OnboardingStep {
        let prefs = self.load_preferences();
        if let Some(step) = prefs
            .get("onboarding_step")
            .and_then(value_as_string)
            .and_then(|s| OnboardingStep::parse(&s))
        {
            return step;
        }
        if coerce_bool_preference(prefs.get("has_seen_onboarding"), false) {
            OnboardingStep::Done
        } else {
            OnboardingStep::ChooseGoal
        }
    }

    /// Persist the onboarding step; setting `Done` also marks `has_seen_onboarding = true`.
    pub fn set_onboarding_step(&self, step: OnboardingStep) -> Result<(), PreferencesError> {
        self.mutate_preferences(|prefs| {
            prefs.insert(
                "onboarding_step".to_string(),
                Value::String(step.as_str().to_string()),
            );
            if step == OnboardingStep::Done {
                prefs.insert("has_seen_onboarding".to_string(), Value::Bool(true));
            }
        })
    }

    pub fn get_onboarding_choice(&self) -> Option<OnboardingChoice> {
        self.load_preferences()
            .get("onboarding_choice")
            .and_then(value_as_string)
            .and_then(|s| OnboardingChoice::parse(&s))
    }

    pub fn set_onboarding_choice(
        &self,
        choice: Option<OnboardingChoice>,
    ) -> Result<(), PreferencesError> {
        self.mutate_preferences(|prefs| match choice {
            Some(choice) => {
                prefs.insert(
                    "onboarding_choice".to_string(),
                    Value::String(choice.as_str().to_string()),
                );
            }
            None => {
                prefs.remove("onboarding_choice");
            }
        })
    }

    pub fn env_file_exists(&self) -> bool {
        self.paths.env_file().exists()
    }

    /// True only when `.env` exists AND onboarding reached `Done`.
    pub fn is_onboarding_complete(&self) -> bool {
        self.env_file_exists() && self.get_onboarding_step() == OnboardingStep::Done
    }

    pub fn get_show_welcome_on_startup(&self) -> bool {
        coerce_bool_preference(self.load_preferences().get("show_welcome_on_startup"), true)
    }

    pub fn set_show_welcome_on_startup(&self, show: bool) -> Result<(), PreferencesError> {
        self.mutate_preferences(|prefs| {
            prefs.insert("show_welcome_on_startup".to_string(), Value::Bool(show));
        })
    }

    /// Read `~/.pulsescribe/.env` keeping the first assignment for duplicate keys.
    pub fn read_env_file(&self) -> BTreeMap<String, String> {
        let env_path = self.paths.env_file();
        let Ok(sig) = build_file_signature(&env_path) else {
            self.invalidate_env_cache();
            return BTreeMap::new();
        };

        {
            let guard = self.env_cache.lock().unwrap_or_else(|e| e.into_inner());
            if let Some((cached_sig, cached)) = guard.as_ref() {
                if *cached_sig == sig {
                    return cached.clone();
                }
            }
        }

        let loaded = read_env_file_values(&env_path, true);
        let mut guard = self.env_cache.lock().unwrap_or_else(|e| e.into_inner());
        *guard = Some((sig, loaded.clone()));
        loaded
    }

    pub fn get_env_setting(&self, key_name: &str) -> Option<String> {
        self.read_env_file().get(key_name).cloned()
    }

    pub fn get_api_key(&self, key_name: &str) -> Option<String> {
        self.get_env_setting(key_name)
    }

    /// Persist one `.env` setting while preserving later duplicates and unchanged formatting.
    pub fn save_env_setting(&self, key_name: &str, value: &str) -> Result<bool, PreferencesError> {
        self.update_env_file_impl(&[(key_name.to_string(), Some(value.to_string()))], true)
    }

    pub fn save_api_key(&self, key_name: &str, value: &str) -> Result<bool, PreferencesError> {
        self.save_env_setting(key_name, value)
    }

    /// Save a trimmed API key or remove it when empty/None. Returns `true` if saved.
    pub fn set_api_key(
        &self,
        key_name: &str,
        value: Option<&str>,
    ) -> Result<bool, PreferencesError> {
        let normalized = value.unwrap_or("").trim();
        if normalized.is_empty() {
            self.remove_env_setting(key_name)?;
            return Ok(false);
        }
        self.save_api_key(key_name, normalized)?;
        Ok(true)
    }

    /// Remove all definitions of `key_name` from `.env`.
    pub fn remove_env_setting(&self, key_name: &str) -> Result<bool, PreferencesError> {
        self.update_env_file_impl(&[(key_name.to_string(), None)], false)
    }

    /// Apply multiple `.env` updates in a single atomic read/write pass, collapsing
    /// duplicates of updated keys to canonical `KEY=value` lines.
    pub fn update_env_settings(
        &self,
        updates: &[(String, Option<String>)],
    ) -> Result<bool, PreferencesError> {
        if updates.is_empty() {
            return Ok(false);
        }
        self.update_env_file_impl(updates, false)
    }

    /// Persist a toggle or hold hotkey and clear legacy hotkey keys in one atomic update.
    pub fn apply_hotkey_setting(
        &self,
        kind: HotkeyKind,
        hotkey_str: &str,
    ) -> Result<bool, PreferencesError> {
        let value = hotkey_str.trim().to_ascii_lowercase();
        if value.is_empty() {
            return Ok(false);
        }
        let mut updates = vec![(kind.env_key().to_string(), Some(value))];
        for legacy_key in LEGACY_HOTKEY_ENV_KEYS {
            updates.push((legacy_key.to_string(), None));
        }
        self.update_env_settings(&updates)
    }

    fn update_env_file_impl(
        &self,
        updates: &[(String, Option<String>)],
        preserve_following_duplicates: bool,
    ) -> Result<bool, PreferencesError> {
        let _write_guard = self
            .env_write_lock
            .lock()
            .unwrap_or_else(|e| e.into_inner());

        let env_path = self.paths.env_file();
        let existing_lines: Vec<String> = if env_path.exists() {
            let bytes = fs::read(&env_path)?;
            String::from_utf8_lossy(&bytes)
                .lines()
                .map(ToOwned::to_owned)
                .collect()
        } else {
            Vec::new()
        };

        let (new_lines, changed) =
            apply_env_updates_to_lines(&existing_lines, updates, preserve_following_duplicates)?;
        if !changed {
            self.invalidate_env_cache();
            return Ok(false);
        }

        write_text_atomic_restricted(&env_path, &render_env_lines(&new_lines))?;
        self.invalidate_env_cache();
        Ok(true)
    }
}

fn value_as_string(value: &Value) -> Option<String> {
    match value {
        Value::Null => None,
        Value::String(s) => Some(s.clone()),
        Value::Bool(b) => Some(b.to_string()),
        Value::Number(n) => Some(n.to_string()),
        _ => None,
    }
}

fn coerce_bool_preference(raw: Option<&Value>, default: bool) -> bool {
    match raw {
        None | Some(Value::Null) => default,
        Some(Value::Bool(b)) => *b,
        Some(other) => value_as_string(other)
            .as_deref()
            .and_then(|s| parse_bool(Some(s)))
            .unwrap_or(default),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn unique_store(tag: &str) -> (PreferencesStore, PathBuf) {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "pulsescribe-core-prefs-{tag}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(&dir).unwrap();
        (PreferencesStore::new(&dir), dir)
    }

    #[test]
    fn onboarding_completion_requires_both_done_step_and_env_file() {
        let (store, dir) = unique_store("onboarding");

        assert!(!store.has_seen_onboarding());
        assert_eq!(store.get_onboarding_step(), OnboardingStep::ChooseGoal);
        assert!(!store.is_onboarding_complete());
        assert!(store.get_show_welcome_on_startup());

        // Legacy compatibility: has_seen_onboarding without onboarding_step -> Done
        store.set_onboarding_seen(true).unwrap();
        assert_eq!(store.get_onboarding_step(), OnboardingStep::Done);
        assert!(!store.is_onboarding_complete()); // .env still missing

        store.save_env_setting("PULSESCRIBE_MODE", "local").unwrap();
        assert!(store.is_onboarding_complete());

        store
            .set_onboarding_choice(Some(OnboardingChoice::Private))
            .unwrap();
        assert_eq!(
            store.get_onboarding_choice(),
            Some(OnboardingChoice::Private)
        );
        store.set_onboarding_choice(None).unwrap();
        assert_eq!(store.get_onboarding_choice(), None);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn preferences_preserve_unknown_keys_and_recover_from_corrupt_json() {
        let (store, dir) = unique_store("preserve");
        fs::write(
            store.paths().preferences_file(),
            r#"{"future_python_key": 123, "show_welcome_on_startup": "false"}"#,
        )
        .unwrap();

        assert!(!store.get_show_welcome_on_startup());
        store.set_onboarding_step(OnboardingStep::Done).unwrap();

        let reloaded = store.load_preferences();
        assert_eq!(reloaded.get("future_python_key"), Some(&Value::from(123)));
        assert_eq!(
            reloaded.get("has_seen_onboarding"),
            Some(&Value::Bool(true))
        );

        fs::write(store.paths().preferences_file(), "{not valid json").unwrap();
        store.invalidate_preferences_cache();
        assert!(store.load_preferences().is_empty());

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn apply_hotkey_setting_clears_legacy_keys_and_set_api_key_trims_or_removes() {
        let (store, dir) = unique_store("hotkeys");
        fs::write(
            store.paths().env_file(),
            "PULSESCRIBE_HOTKEY=f18\nPULSESCRIBE_HOTKEY_MODE=toggle\nPULSESCRIBE_HOLD_HOTKEY=fn\n",
        )
        .unwrap();

        assert!(store
            .apply_hotkey_setting(HotkeyKind::Toggle, "  F19 ")
            .unwrap());
        let env = store.read_env_file();
        assert_eq!(
            env.get("PULSESCRIBE_TOGGLE_HOTKEY").map(String::as_str),
            Some("f19")
        );
        assert_eq!(
            env.get("PULSESCRIBE_HOLD_HOTKEY").map(String::as_str),
            Some("fn")
        );
        assert!(!env.contains_key("PULSESCRIBE_HOTKEY"));
        assert!(!env.contains_key("PULSESCRIBE_HOTKEY_MODE"));

        assert!(store
            .set_api_key("DEEPGRAM_API_KEY", Some("  dg-secret  "))
            .unwrap());
        assert_eq!(
            store.get_api_key("DEEPGRAM_API_KEY").as_deref(),
            Some("dg-secret")
        );
        assert!(!store.set_api_key("DEEPGRAM_API_KEY", Some("   ")).unwrap());
        assert_eq!(store.get_api_key("DEEPGRAM_API_KEY"), None);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn concurrent_mutations_do_not_lose_preference_or_env_updates() {
        use std::sync::Arc;
        use std::thread;

        let (store, dir) = unique_store("concurrent");
        let store = Arc::new(store);
        store.save_api_key("REMOVED_KEY", "secret").unwrap();

        let s1 = Arc::clone(&store);
        let s2 = Arc::clone(&store);
        let t1 = thread::spawn(move || {
            for _ in 0..20 {
                s1.set_onboarding_seen(true).unwrap();
                s1.save_env_setting("PULSESCRIBE_MODE", "local").unwrap();
            }
        });
        let t2 = thread::spawn(move || {
            for _ in 0..20 {
                s2.set_show_welcome_on_startup(false).unwrap();
                s2.remove_env_setting("REMOVED_KEY").unwrap();
            }
        });
        t1.join().unwrap();
        t2.join().unwrap();

        assert!(store.has_seen_onboarding());
        assert!(!store.get_show_welcome_on_startup());
        assert_eq!(
            store.get_env_setting("PULSESCRIBE_MODE").as_deref(),
            Some("local")
        );
        assert_eq!(store.get_api_key("REMOVED_KEY"), None);

        let _ = fs::remove_dir_all(&dir);
    }
}
