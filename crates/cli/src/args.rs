//! CLI argument definitions and post-`.env` option resolution matching `transcribe.py` and `cli/types.py`.

use std::collections::BTreeMap;
use std::fmt;
use std::path::PathBuf;
use std::str::FromStr;

use clap::{Parser, ValueEnum};
use pulsescribe_core::env::parse_bool;
use pulsescribe_providers::ResponseFormat;

use crate::CliError;

pub const MODE_ENV_VAR: &str = "PULSESCRIBE_MODE";
pub const MODEL_ENV_VAR: &str = "PULSESCRIBE_MODEL";
pub const LOCAL_MODEL_ENV_VAR: &str = "PULSESCRIBE_LOCAL_MODEL";
pub const LANGUAGE_ENV_VAR: &str = "PULSESCRIBE_LANGUAGE";
pub const DEBUG_ENV_VAR: &str = "PULSESCRIBE_DEBUG";
pub const REFINE_ENV_VAR: &str = "PULSESCRIBE_REFINE";
pub const REFINE_MODEL_ENV_VAR: &str = "PULSESCRIBE_REFINE_MODEL";
pub const REFINE_PROVIDER_ENV_VAR: &str = "PULSESCRIBE_REFINE_PROVIDER";

/// Transcription backend mode (`cli/types.py::TranscriptionMode`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
pub enum TranscriptionMode {
    Openai,
    Local,
    Deepgram,
    Groq,
    #[value(name = "openai_compatible")]
    OpenaiCompatible,
}

impl TranscriptionMode {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Openai => "openai",
            Self::Local => "local",
            Self::Deepgram => "deepgram",
            Self::Groq => "groq",
            Self::OpenaiCompatible => "openai_compatible",
        }
    }
}

impl fmt::Display for TranscriptionMode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

impl FromStr for TranscriptionMode {
    type Err = ();

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.trim().to_ascii_lowercase().as_str() {
            "openai" => Ok(Self::Openai),
            "local" => Ok(Self::Local),
            "deepgram" => Ok(Self::Deepgram),
            "groq" => Ok(Self::Groq),
            "openai_compatible" => Ok(Self::OpenaiCompatible),
            _ => Err(()),
        }
    }
}

/// Output format (`cli/types.py::ResponseFormat`).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, ValueEnum)]
pub enum CliResponseFormat {
    #[default]
    Text,
    Json,
    Srt,
    Vtt,
}

impl CliResponseFormat {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Text => "text",
            Self::Json => "json",
            Self::Srt => "srt",
            Self::Vtt => "vtt",
        }
    }

    pub const fn into_provider_format(self) -> ResponseFormat {
        match self {
            Self::Text => ResponseFormat::Text,
            Self::Json => ResponseFormat::Json,
            Self::Srt => ResponseFormat::Srt,
            Self::Vtt => ResponseFormat::Vtt,
        }
    }
}

/// LLM backend for post-processing (`cli/types.py::RefineProvider`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
pub enum CliRefineProvider {
    Openai,
    Openrouter,
    Groq,
    Gemini,
}

impl CliRefineProvider {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Openai => "openai",
            Self::Openrouter => "openrouter",
            Self::Groq => "groq",
            Self::Gemini => "gemini",
        }
    }
}

impl FromStr for CliRefineProvider {
    type Err = ();

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.trim().to_ascii_lowercase().as_str() {
            "openai" => Ok(Self::Openai),
            "openrouter" => Ok(Self::Openrouter),
            "groq" => Ok(Self::Groq),
            "gemini" => Ok(Self::Gemini),
            _ => Err(()),
        }
    }
}

/// Context override for LLM refinement (`cli/types.py::Context`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
pub enum CliContext {
    Email,
    Chat,
    Code,
    Default,
}

impl CliContext {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Email => "email",
            Self::Chat => "chat",
            Self::Code => "code",
            Self::Default => "default",
        }
    }
}

/// Audio transkribieren mit Whisper, Deepgram oder Groq.
///
/// Transkripte werden auf stdout ausgegeben, Status und Logs auf stderr.
#[derive(Debug, Clone, Parser)]
#[command(
    name = "pulsescribe",
    version,
    about = "Audio transkribieren mit Whisper, Deepgram oder Groq"
)]
pub struct CliArgs {
    /// Pfad zur Audiodatei
    pub audio: Option<PathBuf>,

    /// Vom Mikrofon aufnehmen
    #[arg(short = 'r', long)]
    pub record: bool,

    /// Ergebnis in Zwischenablage kopieren
    #[arg(short = 'c', long)]
    pub copy: bool,

    /// Transkriptions-Modus [env: PULSESCRIBE_MODE]
    #[arg(long, value_enum)]
    pub mode: Option<TranscriptionMode>,

    /// Modellname (CLI > ENV > Provider-Default) [env: PULSESCRIBE_MODEL]
    #[arg(long)]
    pub model: Option<String>,

    /// Sprachcode z.B. 'de', 'en' [env: PULSESCRIBE_LANGUAGE]
    #[arg(long)]
    pub language: Option<String>,

    /// Ausgabeformat (nur OpenAI)
    #[arg(long = "format", value_enum, default_value_t = CliResponseFormat::Text)]
    pub response_format: CliResponseFormat,

    /// Debug-Logging aktivieren [env: PULSESCRIBE_DEBUG]
    #[arg(long)]
    pub debug: bool,

    /// LLM-Nachbearbeitung aktivieren [env: PULSESCRIBE_REFINE]
    #[arg(long)]
    pub refine: bool,

    /// LLM-Nachbearbeitung deaktivieren
    #[arg(long)]
    pub no_refine: bool,

    /// Modell fuer LLM-Nachbearbeitung (default: openai/gpt-oss-120b) [env: PULSESCRIBE_REFINE_MODEL]
    #[arg(long)]
    pub refine_model: Option<String>,

    /// LLM-Provider fuer Nachbearbeitung [env: PULSESCRIBE_REFINE_PROVIDER]
    #[arg(long, value_enum)]
    pub refine_provider: Option<CliRefineProvider>,

    /// Kontext fuer LLM-Nachbearbeitung
    #[arg(long, value_enum)]
    pub context: Option<CliContext>,
}

/// Fully resolved runtime options after `.env` loading and precedence merging.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolvedCliOptions {
    pub mode: TranscriptionMode,
    pub model: Option<String>,
    pub language: Option<String>,
    pub debug: bool,
    pub refine: bool,
    pub refine_model: Option<String>,
    pub refine_provider: Option<CliRefineProvider>,
}

/// Validate audio source selection (`audio` file vs `--record`).
pub fn validate_audio_source(args: &CliArgs) -> Result<(), CliError> {
    if !args.record && args.audio.is_none() {
        return Err(CliError::InvalidArguments(
            "Entweder Audiodatei oder --record verwenden".to_string(),
        ));
    }
    if args.record && args.audio.is_some() {
        return Err(CliError::InvalidArguments(
            "Audiodatei und --record schliessen sich aus".to_string(),
        ));
    }
    Ok(())
}

/// Resolve CLI arguments against the merged process + `.env` map (`CLI > ENV > default`).
pub fn resolve_cli_options(
    args: &CliArgs,
    env_map: &BTreeMap<String, String>,
) -> Result<ResolvedCliOptions, CliError> {
    let mode = match args.mode {
        Some(explicit) => explicit,
        None => match non_empty_env(env_map, MODE_ENV_VAR) {
            Some(raw) => raw.parse::<TranscriptionMode>().map_err(|_| {
                CliError::InvalidArguments(format!(
                    "Ungültiger Wert in {MODE_ENV_VAR}: '{raw}'. Unterstützt: openai, local, deepgram, groq, openai_compatible"
                ))
            })?,
            None => TranscriptionMode::Deepgram,
        },
    };

    let refine_provider = match args.refine_provider {
        Some(explicit) => Some(explicit),
        None => match non_empty_env(env_map, REFINE_PROVIDER_ENV_VAR) {
            Some(raw) => Some(raw.parse::<CliRefineProvider>().map_err(|_| {
                CliError::InvalidArguments(format!(
                    "Ungültiger Wert in {REFINE_PROVIDER_ENV_VAR}: '{raw}'. Unterstützt: openai, openrouter, groq, gemini"
                ))
            })?),
            None => None,
        },
    };

    let model = if mode == TranscriptionMode::Local {
        resolve_string_opt(args.model.as_deref(), env_map, LOCAL_MODEL_ENV_VAR)
            .or_else(|| resolve_string_opt(None, env_map, MODEL_ENV_VAR))
    } else {
        resolve_string_opt(args.model.as_deref(), env_map, MODEL_ENV_VAR)
    };
    let language = resolve_string_opt(args.language.as_deref(), env_map, LANGUAGE_ENV_VAR);
    let refine_model =
        resolve_string_opt(args.refine_model.as_deref(), env_map, REFINE_MODEL_ENV_VAR);

    // `--no-refine` always disables refinement (matching `maybe_refine_transcript`),
    // otherwise `--refine` or `PULSESCRIBE_REFINE` enables it.
    let refine = if args.no_refine {
        false
    } else if args.refine {
        true
    } else {
        parse_bool(env_map.get(REFINE_ENV_VAR).map(String::as_str)).unwrap_or(false)
    };

    let debug =
        args.debug || parse_bool(env_map.get(DEBUG_ENV_VAR).map(String::as_str)) == Some(true);

    Ok(ResolvedCliOptions {
        mode,
        model,
        language,
        debug,
        refine,
        refine_model,
        refine_provider,
    })
}

fn non_empty_env<'a>(env_map: &'a BTreeMap<String, String>, key: &str) -> Option<&'a str> {
    env_map
        .get(key)
        .map(String::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
}

fn resolve_string_opt(
    cli_value: Option<&str>,
    env_map: &BTreeMap<String, String>,
    env_key: &str,
) -> Option<String> {
    if let Some(value) = cli_value {
        let trimmed = value.trim();
        if !trimmed.is_empty() {
            return Some(trimmed.to_string());
        }
    }
    non_empty_env(env_map, env_key).map(ToOwned::to_owned)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(argv: &[&str]) -> CliArgs {
        CliArgs::try_parse_from(argv).unwrap()
    }

    #[test]
    fn resolves_default_mode_and_env_precedence() {
        let args = parse(&["pulsescribe", "sample.wav"]);
        let empty = BTreeMap::new();
        let resolved = resolve_cli_options(&args, &empty).unwrap();
        assert_eq!(resolved.mode, TranscriptionMode::Deepgram);
        assert!(!resolved.refine);
        assert!(!resolved.debug);

        let mut env = BTreeMap::new();
        env.insert(MODE_ENV_VAR.to_string(), "groq".to_string());
        env.insert(MODEL_ENV_VAR.to_string(), "whisper-large-v3".to_string());
        env.insert(REFINE_ENV_VAR.to_string(), "true".to_string());
        env.insert(DEBUG_ENV_VAR.to_string(), "1".to_string());

        let from_env = resolve_cli_options(&args, &env).unwrap();
        assert_eq!(from_env.mode, TranscriptionMode::Groq);
        assert_eq!(from_env.model.as_deref(), Some("whisper-large-v3"));
        assert!(from_env.refine);
        assert!(from_env.debug);

        // CLI --no-refine overrides PULSESCRIBE_REFINE=true, CLI --mode overrides PULSESCRIBE_MODE.
        let override_args = parse(&[
            "pulsescribe",
            "sample.wav",
            "--mode",
            "openai",
            "--no-refine",
        ]);
        let overridden = resolve_cli_options(&override_args, &env).unwrap();
        assert_eq!(overridden.mode, TranscriptionMode::Openai);
        assert!(!overridden.refine);

        // Local mode prioritizes PULSESCRIBE_LOCAL_MODEL over PULSESCRIBE_MODEL, with CLI highest.
        env.insert(LOCAL_MODEL_ENV_VAR.to_string(), "parakeet-v3".to_string());
        let local_args = parse(&["pulsescribe", "sample.wav", "--mode", "local"]);
        let local_resolved = resolve_cli_options(&local_args, &env).unwrap();
        assert_eq!(local_resolved.model.as_deref(), Some("parakeet-v3"));

        let local_cli_override = parse(&[
            "pulsescribe",
            "sample.wav",
            "--mode",
            "local",
            "--model",
            "canary-1b-v2",
        ]);
        let local_overridden = resolve_cli_options(&local_cli_override, &env).unwrap();
        assert_eq!(local_overridden.model.as_deref(), Some("canary-1b-v2"));
    }

    #[test]
    fn rejects_invalid_env_mode_and_conflicting_audio_sources() {
        let args = parse(&["pulsescribe", "sample.wav"]);
        let mut env = BTreeMap::new();
        env.insert(MODE_ENV_VAR.to_string(), "invalid".to_string());
        let err = resolve_cli_options(&args, &env).unwrap_err().to_string();
        assert!(err.contains("PULSESCRIBE_MODE"));
        assert!(err.contains("invalid"));

        let neither = parse(&["pulsescribe"]);
        assert!(validate_audio_source(&neither).is_err());

        let both = parse(&["pulsescribe", "sample.wav", "--record"]);
        assert!(validate_audio_source(&both).is_err());
    }
}
