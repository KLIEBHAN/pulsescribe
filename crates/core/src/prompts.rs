//! LLM refinement prompts, app-to-context mappings, and `~/.pulsescribe/prompts.toml`.
//!
//! Mirrors `refine/prompts.py` and `utils/custom_prompts.py` with instance-scoped
//! file-signature caching, serialized atomic saves, and automatic fallback to
//! hardcoded defaults on missing or invalid TOML files.

use std::collections::BTreeMap;
use std::fs::{self, File};
use std::io::{self, Read};
use std::path::PathBuf;
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::atomic_io::write_text_atomic;
use crate::paths::{build_file_signature, file_signature_from_metadata, AppPaths, FileSignature};

/// Known rewrite prompt context keys.
pub const KNOWN_CONTEXTS: [&str; 4] = ["default", "email", "chat", "code"];

/// Default fallback refinement prompt (matches `refine/prompts.py`).
pub const DEFAULT_REFINE_PROMPT: &str = "Korrigiere dieses Transkript:
- Entferne Füllwörter (ähm, also, quasi, sozusagen)
- Korrigiere Grammatik und Rechtschreibung
- Formatiere in saubere Absätze
- Behalte den originalen Inhalt und Stil bei
- Bleibe so nah wie möglich am Originaltext

Gib NUR den korrigierten Text zurück, keine Erklärungen.";

/// Default spoken voice-command instructions prepended when voice commands are enabled.
pub const VOICE_COMMANDS_INSTRUCTION: &str = "
Interpretiere gesprochene Befehle und entferne sie aus dem Text:
- \"neuer Absatz\" / \"new paragraph\" → Absatz (doppelter Zeilenumbruch)
- \"neue Zeile\" / \"new line\" → Zeilenumbruch
- \"Punkt\" / \"period\" → Satz mit Punkt beenden
- \"Komma\" / \"comma\" → Komma einfügen
- \"Fragezeichen\" / \"question mark\" → Fragezeichen
- \"Ausrufezeichen\" / \"exclamation mark\" → Ausrufezeichen
- \"Doppelpunkt\" / \"colon\" → Doppelpunkt
- \"Semikolon\" / \"semicolon\" → Semikolon
Wende die Befehle an der gesprochenen Stelle an und entferne den Befehlstext.";

pub const EMAIL_REFINE_PROMPT: &str = "Korrigiere dieses Transkript für eine E-Mail:
- Formeller, professioneller Ton
- Vollständige, grammatikalisch korrekte Sätze
- Grußformeln und Anrede beibehalten
- Klar strukturierte Absätze
- Bleibe so nah wie möglich am Originaltext

Gib NUR den korrigierten Text zurück.";

pub const CHAT_REFINE_PROMPT: &str = "Korrigiere dieses Transkript für eine Chat-Nachricht:
- Lockerer, natürlicher Ton
- Kurz und prägnant
- Emojis können beibehalten werden
- Keine übermäßige Formalisierung
- Bleibe so nah wie möglich am Originaltext

Gib NUR den korrigierten Text zurück.";

pub const CODE_REFINE_PROMPT: &str = "Korrigiere dieses Transkript für technischen Kontext:
- Technische Fachbegriffe exakt beibehalten
- Code-Snippets, Variablennamen und Befehle nicht ändern
- Camel/Snake-Case erkennen und beibehalten
- Englische Begriffe nicht eindeutschen
- Bleibe so nah wie möglich am Originaltext

Gib NUR den korrigierten Text zurück.";

/// Built-in app-name to context mappings for automatic context detection.
pub const DEFAULT_APP_CONTEXTS: [(&str, &str); 22] = [
    ("Mail", "email"),
    ("Outlook", "email"),
    ("Spark", "email"),
    ("Thunderbird", "email"),
    ("Slack", "chat"),
    ("Discord", "chat"),
    ("Telegram", "chat"),
    ("WhatsApp", "chat"),
    ("Messages", "chat"),
    ("Signal", "chat"),
    ("Nachrichten", "chat"),
    ("Code", "code"),
    ("VS Code", "code"),
    ("Visual Studio Code", "code"),
    ("Cursor", "code"),
    ("Zed", "code"),
    ("PyCharm", "code"),
    ("IntelliJ IDEA", "code"),
    ("Xcode", "code"),
    ("Terminal", "code"),
    ("iTerm2", "code"),
    ("Ghostty", "code"),
];

const APP_MAPPINGS_HEADER: &str = "# App → Context Mappings (one per line: AppName = context)";

/// Selectable tabs/sections inside the prompt editor UI.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PromptEditorContext {
    Default,
    Email,
    Chat,
    Code,
    VoiceCommands,
    AppMappings,
}

impl PromptEditorContext {
    pub const ALL: [Self; 6] = [
        Self::Default,
        Self::Email,
        Self::Chat,
        Self::Code,
        Self::VoiceCommands,
        Self::AppMappings,
    ];

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Default => "default",
            Self::Email => "email",
            Self::Chat => "chat",
            Self::Code => "code",
            Self::VoiceCommands => "voice_commands",
            Self::AppMappings => "app_mappings",
        }
    }

    /// Normalize UI labels or internal keys to a stable `PromptEditorContext`, falling back to `Default`.
    pub fn parse_or_default(value: Option<&str>) -> Self {
        let normalized = value
            .unwrap_or("")
            .trim()
            .trim_matches('"')
            .trim_matches('\'')
            .to_lowercase();
        match normalized.as_str() {
            "email" => Self::Email,
            "chat" => Self::Chat,
            "code" => Self::Code,
            "voice_commands" | "voice commands" | "── voice commands" => Self::VoiceCommands,
            "app_mappings" | "app mappings" | "── app mappings" => Self::AppMappings,
            _ => Self::Default,
        }
    }

    pub const fn label(self) -> &'static str {
        match self {
            Self::Default => "Default",
            Self::Email => "Email",
            Self::Chat => "Chat",
            Self::Code => "Code",
            Self::VoiceCommands => "Voice Commands",
            Self::AppMappings => "App Mappings",
        }
    }

    pub const fn description(self) -> &'static str {
        match self {
            Self::Default => {
                "Fallback instructions used when PulseScribe cannot infer a more specific writing context."
            }
            Self::Email => {
                "Tune how rewritten dictation should sound in emails, including tone, structure, and sign-offs."
            }
            Self::Chat => "Tune short back-and-forth messages for chat apps and messengers.",
            Self::Code => {
                "Tune formatting and cleanup when you dictate technical text, code, or commit messages."
            }
            Self::VoiceCommands => {
                "Define spoken editing commands such as punctuation, formatting, or cursor actions."
            }
            Self::AppMappings => {
                "Map app names to prompt contexts so PulseScribe can choose the right rewrite style automatically."
            }
        }
    }

    pub const fn placeholder(self) -> &'static str {
        match self {
            Self::Default => "Add custom fallback instructions for general dictation…",
            Self::Email => "Add custom instructions for email dictation…",
            Self::Chat => "Add custom instructions for chat dictation…",
            Self::Code => "Add custom instructions for technical or code-heavy dictation…",
            Self::VoiceCommands => "Customize spoken editing commands…",
            Self::AppMappings => "Add one mapping per line, for example: Slack = chat",
        }
    }
}

/// Normalize UI-facing prompt editor labels back to stable internal keys.
pub fn normalize_prompt_editor_context(value: Option<&str>) -> &'static str {
    PromptEditorContext::parse_or_default(value).as_str()
}

/// Normalized editor state suitable for dirty/no-op comparisons.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PromptEditorSemanticState {
    Text(String),
    AppMappings(Vec<(String, String)>),
}

/// Compute normalized semantic state for a prompt editor section.
pub fn prompt_editor_semantic_state(
    context: Option<&str>,
    text: Option<&str>,
) -> PromptEditorSemanticState {
    let normalized_text = text.unwrap_or("");
    match PromptEditorContext::parse_or_default(context) {
        PromptEditorContext::AppMappings => {
            let mappings = parse_app_mappings(normalized_text).into_iter().collect();
            PromptEditorSemanticState::AppMappings(mappings)
        }
        _ => PromptEditorSemanticState::Text(normalized_text.to_string()),
    }
}

/// Merged prompt configuration (defaults plus user overrides).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PromptsConfig {
    pub voice_commands_instruction: String,
    pub prompts: BTreeMap<String, String>,
    pub app_contexts: BTreeMap<String, String>,
}

/// Sparse user overrides persisted to `~/.pulsescribe/prompts.toml`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PromptOverrides {
    pub voice_commands_instruction: Option<String>,
    pub prompts: BTreeMap<String, String>,
    pub app_contexts: BTreeMap<String, String>,
}

impl PromptsConfig {
    /// Return the built-in default prompts, voice-commands instruction, and app mappings.
    pub fn defaults() -> Self {
        let mut prompts = BTreeMap::new();
        prompts.insert("default".to_string(), DEFAULT_REFINE_PROMPT.to_string());
        prompts.insert("email".to_string(), EMAIL_REFINE_PROMPT.to_string());
        prompts.insert("chat".to_string(), CHAT_REFINE_PROMPT.to_string());
        prompts.insert("code".to_string(), CODE_REFINE_PROMPT.to_string());

        let app_contexts = DEFAULT_APP_CONTEXTS
            .iter()
            .map(|(app, ctx)| ((*app).to_string(), (*ctx).to_string()))
            .collect();

        Self {
            voice_commands_instruction: VOICE_COMMANDS_INSTRUCTION.to_string(),
            prompts,
            app_contexts,
        }
    }

    /// Return the prompt text for `context`, falling back to `"default"` for unknown contexts.
    pub fn prompt_for_context(&self, context: &str) -> &str {
        let effective = if KNOWN_CONTEXTS.contains(&context) {
            context
        } else {
            "default"
        };
        self.prompts
            .get(effective)
            .or_else(|| self.prompts.get("default"))
            .map(String::as_str)
            .unwrap_or(DEFAULT_REFINE_PROMPT)
    }

    /// Return the full refinement prompt, optionally prepending voice-command instructions.
    pub fn refine_prompt_for_context(&self, context: &str, voice_commands: bool) -> String {
        let prompt = self.prompt_for_context(context);
        if voice_commands {
            format!("{}\n{prompt}", self.voice_commands_instruction)
        } else {
            prompt.to_string()
        }
    }

    /// Return the editable UI text for one prompt-editor context.
    pub fn editor_text(&self, context: PromptEditorContext) -> String {
        match context {
            PromptEditorContext::VoiceCommands => self.voice_commands_instruction.clone(),
            PromptEditorContext::AppMappings => format_app_mappings(&self.app_contexts),
            other => self
                .prompts
                .get(other.as_str())
                .cloned()
                .unwrap_or_default(),
        }
    }

    /// Return a copy of `self` with one editor draft applied using storage semantics.
    pub fn apply_editor_text(&self, context: PromptEditorContext, text: &str) -> Self {
        let mut next = self.clone();
        match context {
            PromptEditorContext::VoiceCommands => {
                next.voice_commands_instruction = text.to_string();
            }
            PromptEditorContext::AppMappings => {
                next.app_contexts = parse_app_mappings(text);
            }
            other => {
                next.prompts
                    .insert(other.as_str().to_string(), text.to_string());
            }
        }
        next
    }

    /// Reduce merged prompt configuration to non-empty overrides differing from `defaults`.
    pub fn filter_overrides(&self, defaults: &PromptsConfig) -> PromptOverrides {
        let voice_commands_instruction = if !self.voice_commands_instruction.is_empty()
            && self.voice_commands_instruction != defaults.voice_commands_instruction
        {
            Some(self.voice_commands_instruction.clone())
        } else {
            None
        };

        let mut prompts = BTreeMap::new();
        for (context, prompt_text) in &self.prompts {
            let default_prompt = defaults
                .prompts
                .get(context)
                .map(String::as_str)
                .unwrap_or("");
            if !prompt_text.is_empty() && prompt_text != default_prompt {
                prompts.insert(context.clone(), prompt_text.clone());
            }
        }

        let mut app_contexts = BTreeMap::new();
        for (app, ctx) in &self.app_contexts {
            if let Some((normalized_app, normalized_ctx)) = normalize_app_context_entry(app, ctx) {
                if defaults
                    .app_contexts
                    .get(&normalized_app)
                    .map(String::as_str)
                    == Some(normalized_ctx)
                {
                    continue;
                }
                app_contexts.insert(normalized_app, normalized_ctx.to_string());
            }
        }

        PromptOverrides {
            voice_commands_instruction,
            prompts,
            app_contexts,
        }
    }
}

impl PromptOverrides {
    pub fn is_empty(&self) -> bool {
        self.voice_commands_instruction.is_none()
            && self.prompts.is_empty()
            && self.app_contexts.is_empty()
    }

    /// Merge these overrides onto hardcoded defaults using the same rules as TOML loading.
    pub fn merge_with_defaults(&self) -> PromptsConfig {
        let mut merged = PromptsConfig::defaults();

        if let Some(instruction) = &self.voice_commands_instruction {
            merged.voice_commands_instruction = instruction.clone();
        }

        for context in KNOWN_CONTEXTS {
            if let Some(prompt_text) = self.prompts.get(context) {
                merged
                    .prompts
                    .insert(context.to_string(), prompt_text.clone());
            }
        }

        for (app, ctx) in &self.app_contexts {
            if let Some((normalized_app, normalized_ctx)) = normalize_app_context_entry(app, ctx) {
                merged
                    .app_contexts
                    .insert(normalized_app, normalized_ctx.to_string());
            }
        }

        merged
    }

    /// Serialize overrides into the stable, human-readable `prompts.toml` format.
    pub fn to_toml_string(&self) -> String {
        let mut lines = vec![
            "# Custom Prompts für pulsescribe".to_string(),
            String::new(),
        ];

        if let Some(instruction) = &self.voice_commands_instruction {
            lines.push("[voice_commands]".to_string());
            lines.push(format!(
                "instruction = \"\"\"\n{}\"\"\"",
                escape_toml_multiline(instruction)
            ));
            lines.push(String::new());
        }

        if !self.prompts.is_empty() {
            // Emit known contexts in canonical order first, then any custom keys sorted.
            for context in KNOWN_CONTEXTS {
                if let Some(prompt_text) = self.prompts.get(context) {
                    push_prompt_section(&mut lines, context, prompt_text);
                }
            }
            for (context, prompt_text) in &self.prompts {
                if !KNOWN_CONTEXTS.contains(&context.as_str()) {
                    push_prompt_section(&mut lines, context, prompt_text);
                }
            }
        }

        if !self.app_contexts.is_empty() {
            lines.push("[app_contexts]".to_string());
            for (app, ctx) in &self.app_contexts {
                let escaped_app = escape_toml_basic_string(app);
                let escaped_ctx = escape_toml_basic_string(ctx);
                lines.push(format!("\"{escaped_app}\" = \"{escaped_ctx}\""));
            }
            lines.push(String::new());
        }

        lines.join("\n")
    }
}

/// Build canonical storage overrides from an existing merged config and raw editor drafts.
pub fn build_prompt_overrides_from_editor_state(
    existing: &PromptsConfig,
    drafts: &BTreeMap<String, String>,
    contexts: Option<&[&str]>,
    defaults: Option<&PromptsConfig>,
) -> PromptOverrides {
    let default_owned;
    let baseline = match defaults {
        Some(d) => d,
        None => {
            default_owned = PromptsConfig::defaults();
            &default_owned
        }
    };

    let mut normalized_drafts = BTreeMap::new();
    for (raw_ctx, text) in drafts {
        let ctx = PromptEditorContext::parse_or_default(Some(raw_ctx));
        normalized_drafts.insert(ctx, text.as_str());
    }

    let selected: Vec<PromptEditorContext> = match contexts {
        Some(list) => list
            .iter()
            .map(|c| PromptEditorContext::parse_or_default(Some(c)))
            .collect(),
        None => normalized_drafts.keys().copied().collect(),
    };

    let mut next = existing.clone();
    for ctx in selected {
        if let Some(text) = normalized_drafts.get(&ctx) {
            next = next.apply_editor_text(ctx, text);
        }
    }

    next.filter_overrides(baseline)
}

/// Format app→context mappings into sorted editor text (`AppName = context`).
///
/// Ordinary app names stay unquoted (`Slack = chat`), while names containing
/// editor delimiters (`=`, `"`, `\`, `#`, control chars, or surrounding whitespace)
/// are quoted so they round-trip safely through `parse_app_mappings`.
pub fn format_app_mappings(mappings: &BTreeMap<String, String>) -> String {
    let mut lines = Vec::with_capacity(mappings.len() + 1);
    lines.push(APP_MAPPINGS_HEADER.to_string());
    for (app, ctx) in mappings {
        if needs_app_mapping_quotes(app) {
            lines.push(format!("\"{}\" = {ctx}", escape_toml_basic_string(app)));
        } else {
            lines.push(format!("{app} = {ctx}"));
        }
    }
    lines.join("\n")
}

fn needs_app_mapping_quotes(app: &str) -> bool {
    app.trim() != app
        || app.chars().any(|c| {
            matches!(c, '=' | '"' | '\\' | '#' | '\n' | '\r' | '\t')
                || (c as u32) < 0x20
                || c == '\x7f'
        })
}

fn split_app_mapping_line(line: &str) -> Option<(String, &str)> {
    if let Some(rest) = line.strip_prefix('"') {
        let mut app = String::new();
        let mut chars = rest.chars().peekable();
        let mut closed = false;
        while let Some(ch) = chars.next() {
            match ch {
                '\\' => match chars.next() {
                    Some('\\') => app.push('\\'),
                    Some('"') => app.push('"'),
                    Some('n') => app.push('\n'),
                    Some('r') => app.push('\r'),
                    Some('t') => app.push('\t'),
                    Some(other) => {
                        app.push('\\');
                        app.push(other);
                    }
                    None => break,
                },
                '"' => {
                    closed = true;
                    break;
                }
                other => app.push(other),
            }
        }
        if closed {
            let remainder: String = chars.collect();
            let trimmed_rest = remainder.trim_start();
            if let Some(ctx_part) = trimmed_rest.strip_prefix('=') {
                // Borrow ctx_part slice directly from `line`.
                let offset = line.len() - ctx_part.len();
                return Some((app, &line[offset..]));
            }
            return None;
        }
    }

    let (raw_app, raw_ctx) = line.split_once('=')?;
    Some((raw_app.trim().trim_matches('"').to_string(), raw_ctx))
}

/// Parse editor text into validated `AppName -> context` mappings.
pub fn parse_app_mappings(text: &str) -> BTreeMap<String, String> {
    let mut result = BTreeMap::new();
    for raw_line in text.trim().split('\n') {
        let line = raw_line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some((app_candidate, raw_ctx)) = split_app_mapping_line(line) else {
            continue;
        };
        let ctx_candidate = raw_ctx.split_once('#').map_or(raw_ctx, |(left, _)| left);
        if let Some((app, ctx)) = normalize_app_context_entry(&app_candidate, ctx_candidate) {
            result.insert(app, ctx.to_string());
        }
    }
    result
}

/// Parse a `prompts.toml` document and merge valid sections with hardcoded defaults.
pub fn parse_and_merge_prompts_toml(raw: &str) -> Result<PromptsConfig, toml::de::Error> {
    let table: toml::Table = toml::from_str(raw)?;
    Ok(merge_toml_table_with_defaults(&table))
}

fn merge_toml_table_with_defaults(user: &toml::Table) -> PromptsConfig {
    let mut merged = PromptsConfig::defaults();

    if let Some(instruction) = user
        .get("voice_commands")
        .and_then(toml::Value::as_table)
        .and_then(|section| section.get("instruction"))
        .and_then(toml::Value::as_str)
    {
        merged.voice_commands_instruction = instruction.to_string();
    }

    if let Some(prompts_table) = user.get("prompts").and_then(toml::Value::as_table) {
        for context in KNOWN_CONTEXTS {
            if let Some(prompt_text) = prompts_table
                .get(context)
                .and_then(toml::Value::as_table)
                .and_then(|section| section.get("prompt"))
                .and_then(toml::Value::as_str)
            {
                merged
                    .prompts
                    .insert(context.to_string(), prompt_text.to_string());
            }
        }
    }

    if let Some(app_contexts_table) = user.get("app_contexts").and_then(toml::Value::as_table) {
        for (app, val) in app_contexts_table {
            if let Some(ctx_str) = val.as_str() {
                if let Some((normalized_app, normalized_ctx)) =
                    normalize_app_context_entry(app, ctx_str)
                {
                    merged
                        .app_contexts
                        .insert(normalized_app, normalized_ctx.to_string());
                }
            }
        }
    }

    merged
}

fn normalize_context_name(value: &str) -> Option<&'static str> {
    let normalized = value
        .trim()
        .trim_matches('"')
        .trim_matches('\'')
        .to_lowercase();
    KNOWN_CONTEXTS
        .iter()
        .copied()
        .find(|&known| known == normalized)
}

fn normalize_app_context_entry(app: &str, ctx: &str) -> Option<(String, &'static str)> {
    let normalized_app = app.trim();
    let normalized_ctx = normalize_context_name(ctx)?;
    if normalized_app.is_empty() {
        return None;
    }
    Some((normalized_app.to_string(), normalized_ctx))
}

fn push_prompt_section(lines: &mut Vec<String>, context: &str, prompt_text: &str) {
    let header = if is_toml_bare_key(context) {
        format!("[prompts.{context}]")
    } else {
        format!("[prompts.\"{}\"]", escape_toml_basic_string(context))
    };
    lines.push(header);
    lines.push(format!(
        "prompt = \"\"\"\n{}\"\"\"",
        escape_toml_multiline(prompt_text)
    ));
    lines.push(String::new());
}

fn is_toml_bare_key(key: &str) -> bool {
    !key.is_empty()
        && key
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

fn escape_toml_basic_string(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for ch in text.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 || c == '\x7f' => {
                out.push_str(&format!("\\u{:04X}", c as u32));
            }
            c => out.push(c),
        }
    }
    out
}

/// Escape text for TOML multiline basic strings (`"""..."""`), staying byte-identical
/// to Python's `_escape_toml_multiline` for normal text while also safely handling
/// runs of 4+ quotes and control characters so serialized TOML always round-trips.
fn escape_toml_multiline(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut chars = text.chars().peekable();
    while let Some(ch) = chars.next() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '"' => {
                let mut quote_count = 1usize;
                while chars.peek() == Some(&'"') {
                    chars.next();
                    quote_count += 1;
                }
                if quote_count < 3 {
                    for _ in 0..quote_count {
                        out.push('"');
                    }
                } else {
                    for _ in 0..(quote_count / 3) {
                        out.push_str("\\\"\"\"");
                    }
                    let rem = quote_count % 3;
                    if rem > 0 {
                        out.push_str("\\\"");
                        for _ in 1..rem {
                            out.push('"');
                        }
                    }
                }
            }
            '\r' => {
                if chars.peek() == Some(&'\n') {
                    out.push('\r');
                } else {
                    out.push_str("\\r");
                }
            }
            '\n' | '\t' => out.push(ch),
            c if (c as u32) < 0x20 || c == '\x7f' => {
                out.push_str(&format!("\\u{:04X}", c as u32));
            }
            c => out.push(c),
        }
    }
    out
}

/// Errors produced by `PromptsStore` write/reset operations.
#[derive(Debug, Error)]
pub enum PromptsError {
    #[error("I/O error: {0}")]
    Io(#[from] io::Error),
}

/// Reads and writes `~/.pulsescribe/prompts.toml` with signature-based caching
/// and serialized atomic updates.
#[derive(Debug)]
pub struct PromptsStore {
    paths: AppPaths,
    cache: Mutex<Option<(FileSignature, PromptsConfig)>>,
    write_lock: Mutex<()>,
}

impl PromptsStore {
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

    /// Load merged custom prompts with automatic fallback to hardcoded defaults on
    /// missing, unreadable, or malformed TOML files.
    pub fn load(&self) -> PromptsConfig {
        let prompts_file = self.paths.prompts_file();
        let Ok(stat_signature) = build_file_signature(&prompts_file) else {
            self.invalidate_cache();
            return PromptsConfig::defaults();
        };

        {
            let guard = self.cache.lock().unwrap_or_else(|e| e.into_inner());
            if let Some((cached_sig, cached_config)) = guard.as_ref() {
                if *cached_sig == stat_signature {
                    return cached_config.clone();
                }
            }
        }

        // Open once so signature and content belong to the same file and transient
        // I/O errors are never cached as sticky defaults.
        let Ok(mut file) = File::open(&prompts_file) else {
            self.invalidate_cache();
            return PromptsConfig::defaults();
        };
        let Ok(meta) = file.metadata() else {
            self.invalidate_cache();
            return PromptsConfig::defaults();
        };
        let signature = file_signature_from_metadata(&meta);
        let mut raw_bytes = Vec::new();
        if file.read_to_end(&mut raw_bytes).is_err() {
            self.invalidate_cache();
            return PromptsConfig::defaults();
        }

        let loaded = String::from_utf8(raw_bytes)
            .ok()
            .and_then(|raw| parse_and_merge_prompts_toml(&raw).ok())
            .unwrap_or_else(PromptsConfig::defaults);

        let mut guard = self.cache.lock().unwrap_or_else(|e| e.into_inner());
        *guard = Some((signature, loaded.clone()));
        loaded
    }

    /// Return the prompt text for `context` (unknown context falls back to `"default"`).
    pub fn get_prompt_for_context(&self, context: &str) -> String {
        self.load().prompt_for_context(context).to_string()
    }

    /// Return the full refinement prompt, optionally prepending voice-command instructions.
    pub fn get_refine_prompt_for_context(&self, context: &str, voice_commands: bool) -> String {
        self.load()
            .refine_prompt_for_context(context, voice_commands)
    }

    /// Return the active voice-commands instruction.
    pub fn get_voice_commands(&self) -> String {
        self.load().voice_commands_instruction
    }

    /// Return the merged app-to-context mapping.
    pub fn get_app_contexts(&self) -> BTreeMap<String, String> {
        self.load().app_contexts
    }

    /// Persist prompt overrides atomically and return the merged configuration.
    pub fn save_overrides(
        &self,
        overrides: &PromptOverrides,
    ) -> Result<PromptsConfig, PromptsError> {
        let _write_guard = self.write_lock.lock().unwrap_or_else(|e| e.into_inner());
        let prompts_file = self.paths.prompts_file();

        write_text_atomic(&prompts_file, &overrides.to_toml_string())?;
        self.invalidate_cache();

        Ok(overrides.merge_with_defaults())
    }

    /// Remove the custom `prompts.toml` file (if present) and invalidate the cache.
    pub fn reset_to_defaults(&self) -> Result<(), PromptsError> {
        let _write_guard = self.write_lock.lock().unwrap_or_else(|e| e.into_inner());
        let prompts_file = self.paths.prompts_file();

        match fs::remove_file(&prompts_file) {
            Ok(()) => {}
            Err(err) if err.kind() == io::ErrorKind::NotFound => {}
            Err(err) => return Err(PromptsError::Io(err)),
        }
        self.invalidate_cache();
        Ok(())
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

    fn unique_store(tag: &str) -> (PromptsStore, PathBuf) {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "pulsescribe-core-prompts-{tag}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(&dir).unwrap();
        (PromptsStore::new(&dir), dir)
    }

    #[test]
    fn roundtrip_toml_escaping_handles_backslashes_quotes_dots_and_unicode() {
        let mut overrides = PromptOverrides {
            voice_commands_instruction: Some(
                "Say \"\"\"stop\"\"\" or \"\"\"\"quad\"\"\"\" and C:\\path\\end\"\"".to_string(),
            ),
            prompts: BTreeMap::new(),
            app_contexts: BTreeMap::new(),
        };
        overrides.prompts.insert(
            "email".to_string(),
            "Line 1\\n literal\nTriple \"\"\" at end \"\"\"".to_string(),
        );
        overrides
            .app_contexts
            .insert("App.With.Dots".to_string(), "code".to_string());
        overrides
            .app_contexts
            .insert("Quoted \"App\" \\Name".to_string(), "chat".to_string());

        let toml_text = overrides.to_toml_string();
        let parsed = parse_and_merge_prompts_toml(&toml_text).expect("generated TOML must parse");

        assert_eq!(
            parsed.voice_commands_instruction,
            "Say \"\"\"stop\"\"\" or \"\"\"\"quad\"\"\"\" and C:\\path\\end\"\""
        );
        assert_eq!(
            parsed.prompt_for_context("email"),
            "Line 1\\n literal\nTriple \"\"\" at end \"\"\""
        );
        assert_eq!(
            parsed.app_contexts.get("App.With.Dots").map(String::as_str),
            Some("code")
        );
        assert_eq!(
            parsed
                .app_contexts
                .get("Quoted \"App\" \\Name")
                .map(String::as_str),
            Some("chat")
        );
        // Untouched defaults are still present.
        assert_eq!(parsed.prompt_for_context("chat"), CHAT_REFINE_PROMPT);
        assert_eq!(
            parsed.app_contexts.get("Mail").map(String::as_str),
            Some("email")
        );

        // Verify all quote-run lengths (1..=8) round-trip cleanly mid-string and at end-of-string.
        for n in 1..=8 {
            let quotes = "\"".repeat(n);
            let payload = format!("mid {quotes} text {quotes}");
            let ov = PromptOverrides {
                voice_commands_instruction: Some(payload.clone()),
                ..Default::default()
            };
            let rt = parse_and_merge_prompts_toml(&ov.to_toml_string())
                .unwrap_or_else(|e| panic!("failed round-trip for {n} quotes: {e}"));
            assert_eq!(rt.voice_commands_instruction, payload);
        }
    }

    #[test]
    fn store_loads_partial_falls_back_on_corrupt_and_resets() {
        let (store, dir) = unique_store("lifecycle");
        assert_eq!(store.load(), PromptsConfig::defaults());

        let mut overrides = PromptOverrides::default();
        overrides
            .prompts
            .insert("chat".to_string(), "Short & casual".to_string());
        overrides
            .app_contexts
            .insert("Linear".to_string(), "code".to_string());

        let saved = store.save_overrides(&overrides).unwrap();
        assert_eq!(saved.prompt_for_context("chat"), "Short & casual");
        assert_eq!(store.get_prompt_for_context("chat"), "Short & casual");
        assert_eq!(
            store.get_prompt_for_context("unknown"),
            DEFAULT_REFINE_PROMPT
        );
        assert_eq!(
            store.get_refine_prompt_for_context("chat", true),
            format!("{VOICE_COMMANDS_INSTRUCTION}\nShort & casual")
        );
        assert_eq!(
            store.get_app_contexts().get("Linear").map(String::as_str),
            Some("code")
        );

        // Malformed TOML falls back to defaults silently.
        fs::write(store.paths().prompts_file(), "[prompts.email\nbroken =").unwrap();
        store.invalidate_cache();
        assert_eq!(store.load(), PromptsConfig::defaults());

        // Reset removes file and restores defaults.
        store.reset_to_defaults().unwrap();
        assert!(!store.paths().prompts_file().exists());
        // Idempotent second reset succeeds.
        store.reset_to_defaults().unwrap();

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn editor_helpers_and_override_filtering_match_semantics() {
        assert_eq!(
            normalize_prompt_editor_context(Some("  \"── Voice Commands\"  ")),
            "voice_commands"
        );
        assert_eq!(
            normalize_prompt_editor_context(Some("App Mappings")),
            "app_mappings"
        );
        assert_eq!(normalize_prompt_editor_context(Some("mystery")), "default");

        let parsed = parse_app_mappings(
            "# comment\n\n  \"VS Code\" = CODE # inline\nInvalidLine\nGhost = invalid_ctx\nMail = 'email'\n",
        );
        assert_eq!(parsed.get("VS Code").map(String::as_str), Some("code"));
        assert_eq!(parsed.get("Mail").map(String::as_str), Some("email"));
        assert!(!parsed.contains_key("Ghost"));

        let defaults = PromptsConfig::defaults();
        assert!(defaults.filter_overrides(&defaults).is_empty());

        let mut drafts = BTreeMap::new();
        drafts.insert("email".to_string(), "Custom mail".to_string());
        drafts.insert("chat".to_string(), "Ignored draft".to_string());
        let overrides = build_prompt_overrides_from_editor_state(
            &defaults,
            &drafts,
            Some(&["email"]),
            Some(&defaults),
        );
        assert_eq!(overrides.prompts.len(), 1);
        assert_eq!(
            overrides.prompts.get("email").map(String::as_str),
            Some("Custom mail")
        );

        // App names with `=`, quotes, or backslashes round-trip through the editor format.
        let mut special = BTreeMap::new();
        special.insert("Slack".to_string(), "chat".to_string());
        special.insert("Build=Agent".to_string(), "code".to_string());
        special.insert("Quoted \"App\" \\Name".to_string(), "email".to_string());
        let formatted = format_app_mappings(&special);
        assert!(formatted.contains("Slack = chat"));
        assert!(formatted.contains("\"Build=Agent\" = code"));
        assert_eq!(parse_app_mappings(&formatted), special);
    }

    #[cfg(unix)]
    #[test]
    fn load_does_not_cache_transient_io_failures() {
        use std::os::unix::fs::PermissionsExt;

        let (store, dir) = unique_store("transient-io");
        let path = store.paths().prompts_file();
        fs::write(&path, "[prompts.chat]\nprompt = \"Custom chat\"\n").unwrap();

        fs::set_permissions(&path, fs::Permissions::from_mode(0o000)).unwrap();
        let can_read_000 = File::open(&path).is_ok();
        if !can_read_000 {
            assert_eq!(store.get_prompt_for_context("chat"), CHAT_REFINE_PROMPT);
            fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
            assert_eq!(store.get_prompt_for_context("chat"), "Custom chat");
        } else {
            fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
        }

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn concurrent_saves_and_resets_never_leave_corrupt_toml() {
        use std::sync::Arc;
        use std::thread;

        let (store, dir) = unique_store("concurrent");
        let store = Arc::new(store);
        let handles: Vec<_> = (0..4)
            .map(|id| {
                let s = Arc::clone(&store);
                thread::spawn(move || {
                    for iter in 0..10 {
                        let mut ov = PromptOverrides::default();
                        ov.prompts
                            .insert("chat".to_string(), format!("worker {id} iter {iter}"));
                        s.save_overrides(&ov).unwrap();
                    }
                })
            })
            .collect();
        for handle in handles {
            handle.join().unwrap();
        }

        let raw = fs::read_to_string(store.paths().prompts_file()).unwrap();
        let parsed = parse_and_merge_prompts_toml(&raw).expect("final prompts.toml must be valid");
        assert!(parsed.prompt_for_context("chat").starts_with("worker "));

        let _ = fs::remove_dir_all(&dir);
    }
}
