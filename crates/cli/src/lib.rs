//! Core orchestration for the `pulsescribe` CLI (`crates/cli`).
//!
//! Wires `pulsescribe-core` (`.env`, vocabulary, prompts), `pulsescribe-audio` (`--record`),
//! `pulsescribe-providers` (cloud transcription + LLM refinement), and `arboard` (`--copy`).

pub mod args;
pub mod clipboard;
pub mod record;

use std::collections::BTreeMap;
use std::io::Write;
use std::path::PathBuf;
use std::time::Instant;

use pulsescribe_audio::AudioError;
use pulsescribe_core::prompts::{PromptsConfig, PromptsStore};
use pulsescribe_core::vocabulary::VocabularyStore;
use pulsescribe_providers::{
    build_http_client, maybe_refine, CloudProvider, LlmRefineProvider, ProviderError,
    RefineRequest, TranscriptionRequest,
};
use thiserror::Error;

use crate::args::{
    resolve_cli_options, validate_audio_source, CliArgs, CliContext, CliRefineProvider,
    CliResponseFormat, ResolvedCliOptions, TranscriptionMode,
};
use crate::record::{record_interactive_wav, TempRecordingGuard};

/// Errors surfaced by the CLI orchestration layer.
#[derive(Debug, Error)]
pub enum CliError {
    #[error("{0}")]
    InvalidArguments(String),

    #[error("Datei nicht gefunden: {0}")]
    FileNotFound(PathBuf),

    #[error("{0}")]
    UnsupportedMode(String),

    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    #[error(transparent)]
    Audio(#[from] AudioError),

    #[error(transparent)]
    Provider(#[from] ProviderError),
}

impl CliError {
    /// Exit status code matching `transcribe.py` conventions (2 for CLI arg errors, 1 for runtime).
    pub const fn exit_code(&self) -> i32 {
        match self {
            Self::InvalidArguments(_) => 2,
            Self::FileNotFound(_)
            | Self::UnsupportedMode(_)
            | Self::Io(_)
            | Self::Audio(_)
            | Self::Provider(_) => 1,
        }
    }
}

/// Execute the complete CLI transcription + optional refinement + optional clipboard pipeline.
///
/// Writes only the final transcript to `stdout`; warnings and status updates go to `stderr`.
pub async fn run_cli(
    args: &CliArgs,
    env_map: &BTreeMap<String, String>,
    stdout: &mut dyn Write,
    stderr: &mut dyn Write,
) -> Result<String, CliError> {
    let started = Instant::now();
    validate_audio_source(args)?;
    let resolved = resolve_cli_options(args, env_map)?;

    tracing::debug!(
        mode = resolved.mode.as_str(),
        model = resolved.model.as_deref().unwrap_or("<default>"),
        record = args.record,
        refine = resolved.refine,
        "resolved CLI options"
    );

    if resolved.mode == TranscriptionMode::Local {
        return Err(CliError::UnsupportedMode(
            "Lokaler Modus (--mode local) wird erst in Phase 5 (crates/local-asr) ergänzt. Verfügbare Cloud-Modi: deepgram, openai, groq, openai_compatible"
                .to_string(),
        ));
    }

    let (audio_path, _temp_guard) = resolve_audio_source(args).await?;
    let transcript =
        transcribe_and_maybe_refine(audio_path, _temp_guard, args, &resolved, stderr).await?;

    writeln!(stdout, "{transcript}")?;
    stdout.flush()?;

    if args.copy {
        match clipboard::copy_to_clipboard(&transcript) {
            Ok(()) => {
                let _ = writeln!(stderr, "In Zwischenablage kopiert!");
            }
            Err(err) => {
                tracing::warn!(error = %err, "clipboard copy unavailable");
                let _ = writeln!(stderr, "Hinweis: Zwischenablage nicht verfügbar ({err})");
            }
        }
    }

    tracing::info!(
        elapsed_ms = started.elapsed().as_millis(),
        chars = transcript.chars().count(),
        "CLI pipeline completed"
    );
    Ok(transcript)
}

async fn resolve_audio_source(
    args: &CliArgs,
) -> Result<(PathBuf, Option<TempRecordingGuard>), CliError> {
    if args.record {
        let guard = tokio::task::spawn_blocking(record_interactive_wav)
            .await
            .map_err(|err| CliError::Io(std::io::Error::other(err.to_string())))??;
        let path = guard.path().to_path_buf();
        return Ok((path, Some(guard)));
    }

    let path = args.audio.clone().ok_or_else(|| {
        CliError::InvalidArguments("Entweder Audiodatei oder --record verwenden".to_string())
    })?;
    if !path.is_file() {
        return Err(CliError::FileNotFound(path));
    }
    Ok((path, None))
}

async fn transcribe_and_maybe_refine(
    audio_path: PathBuf,
    temp_guard: Option<TempRecordingGuard>,
    args: &CliArgs,
    resolved: &ResolvedCliOptions,
    stderr: &mut dyn Write,
) -> Result<String, CliError> {
    let http_client = build_http_client()?;
    let provider = CloudProvider::from_mode_env(resolved.mode.as_str(), http_client.clone())?;

    if resolved.mode != TranscriptionMode::Openai && args.response_format != CliResponseFormat::Text
    {
        let _ = writeln!(
            stderr,
            "Hinweis: --format wird im {}-Modus ignoriert",
            resolved.mode.as_str()
        );
    }

    let keywords = VocabularyStore::default_user()
        .map(|store| store.load_keywords())
        .unwrap_or_default();

    let mut request = TranscriptionRequest::new(audio_path)
        .with_response_format(args.response_format.into_provider_format())
        .with_keywords(keywords);
    if let Some(model) = &resolved.model {
        request = request.with_model(model.clone());
    }
    if let Some(language) = &resolved.language {
        request = request.with_language(language.clone());
    }

    let transcription_result = provider.transcribe(&request).await;
    // Drop temporary recording immediately after transcription, preserving primary error.
    drop(temp_guard);
    let transcript = transcription_result?;

    if should_skip_refine_for_format(resolved.mode, args.response_format) {
        if resolved.refine {
            let _ = writeln!(
                stderr,
                "Hinweis: LLM-Nachbearbeitung wird für --format {} übersprungen",
                args.response_format.as_str()
            );
        }
        return Ok(transcript);
    }

    if !resolved.refine || transcript.trim().is_empty() {
        return Ok(transcript);
    }

    let prompts = PromptsStore::default_user()
        .map(|store| store.load())
        .unwrap_or_else(PromptsConfig::defaults);

    let refine_provider = match LlmRefineProvider::from_env(
        http_client,
        resolved.refine_provider.map(CliRefineProvider::as_str),
        resolved.refine_model.as_deref(),
    ) {
        Ok(provider) => Some(provider),
        Err(err) => {
            tracing::warn!(error = %err, "LLM-Nachbearbeitung übersprungen");
            let _ = writeln!(stderr, "Hinweis: LLM-Nachbearbeitung übersprungen: {err}");
            None
        }
    };

    let mut refine_req = RefineRequest::new(transcript);
    if let Some(context) = args.context.map(CliContext::as_str) {
        refine_req = refine_req.with_context(context);
    }
    if let Some(model) = &resolved.refine_model {
        refine_req = refine_req.with_model(model.clone());
    }

    Ok(maybe_refine(refine_provider.as_ref(), &refine_req, &prompts, true).await)
}

/// Only OpenAI produces structured output (`json`, `srt`, `vtt`); all other providers ignore
/// `--format` and return plain text, so refinement stays active for them.
pub const fn should_skip_refine_for_format(
    mode: TranscriptionMode,
    response_format: CliResponseFormat,
) -> bool {
    matches!(mode, TranscriptionMode::Openai) && !matches!(response_format, CliResponseFormat::Text)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn refine_skips_only_when_effective_provider_output_is_structured() {
        assert!(should_skip_refine_for_format(
            TranscriptionMode::Openai,
            CliResponseFormat::Json
        ));
        assert!(should_skip_refine_for_format(
            TranscriptionMode::Openai,
            CliResponseFormat::Srt
        ));
        assert!(!should_skip_refine_for_format(
            TranscriptionMode::Openai,
            CliResponseFormat::Text
        ));
        assert!(!should_skip_refine_for_format(
            TranscriptionMode::OpenaiCompatible,
            CliResponseFormat::Json
        ));
        assert!(!should_skip_refine_for_format(
            TranscriptionMode::Deepgram,
            CliResponseFormat::Srt
        ));
        assert!(!should_skip_refine_for_format(
            TranscriptionMode::Groq,
            CliResponseFormat::Vtt
        ));
    }
}
