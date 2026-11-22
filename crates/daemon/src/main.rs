//! Headless `pulsescribe-daemon` executable wired to native platform adapters and
//! [`LiveDictationBackend`].

use std::io::{self, BufRead};
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::Arc;

use clap::Parser;
use pulsescribe_core::paths::AppPaths;
use pulsescribe_core::PreferencesStore;
use pulsescribe_daemon::{
    DaemonConfig, DaemonEvent, DaemonPlatform, DaemonRuntime, DaemonRuntimeOptions,
    LiveDictationBackend,
};
use tracing_subscriber::EnvFilter;

#[derive(Debug, Parser)]
#[command(
    name = "pulsescribe-daemon",
    version,
    about = "Headless PulseScribe dictation daemon"
)]
struct DaemonCliArgs {
    /// Override the PulseScribe config directory (defaults to `~/.pulsescribe`).
    #[arg(long)]
    config_dir: Option<PathBuf>,

    /// Enable verbose debug logging.
    #[arg(short, long)]
    debug: bool,

    /// Disable file-based IPC (`ipc_command.json` / `ipc_response.json`).
    #[arg(long)]
    no_ipc: bool,

    /// Disable automatic `.env` / `.reload` hot-reload polling.
    #[arg(long)]
    no_reload: bool,

    /// Do not restore previous clipboard contents after auto-paste.
    #[arg(long)]
    no_clipboard_restore: bool,
}

fn main() -> ExitCode {
    let args = DaemonCliArgs::parse();
    init_tracing(args.debug);

    let config_dir = match args.config_dir {
        Some(dir) => dir,
        None => {
            match AppPaths::default_user() {
                Some(paths) => paths.config_dir().to_path_buf(),
                None => {
                    eprintln!("Fehler: Home-Verzeichnis konnte nicht ermittelt werden (nutze --config-dir).");
                    return ExitCode::from(1);
                }
            }
        }
    };

    let runtime = match tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
    {
        Ok(rt) => rt,
        Err(err) => {
            eprintln!("Fehler: Tokio-Runtime konnte nicht gestartet werden: {err}");
            return ExitCode::from(1);
        }
    };

    match runtime.block_on(run_daemon(
        config_dir,
        !args.no_ipc,
        !args.no_reload,
        !args.no_clipboard_restore,
    )) {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            eprintln!("Fehler: {err}");
            ExitCode::from(1)
        }
    }
}

async fn run_daemon(
    config_dir: PathBuf,
    enable_ipc: bool,
    enable_reload_watcher: bool,
    restore_clipboard: bool,
) -> Result<(), String> {
    let store = Arc::new(PreferencesStore::new(&config_dir));
    let config = DaemonConfig::from_store_startup(&store);
    let platform = DaemonPlatform::native(restore_clipboard);
    let backend = LiveDictationBackend::new(&config_dir, Arc::clone(&platform.permissions))
        .map_err(|err| format!("Dictation-Backend konnte nicht initialisiert werden: {err}"))?;

    backend.sync_deepgram_warm_state(&config).await;

    let options = DaemonRuntimeOptions {
        enable_ipc,
        enable_reload_watcher,
        ..DaemonRuntimeOptions::default()
    };

    eprintln!(
        "pulsescribe-daemon gestartet (mode={}, config={}). Zum sauberen Beenden 'quit' eingeben oder stdin schließen.",
        config.mode.as_str(),
        config_dir.display()
    );

    let daemon =
        DaemonRuntime::spawn(store, config, platform, Arc::new(backend.clone()), options).await;
    let handle = daemon.handle();
    let mut events = handle.subscribe();
    let reload_backend = backend.clone();
    let reload_handle = handle.clone();

    let logger = tokio::spawn(async move {
        loop {
            match events.recv().await {
                Ok(DaemonEvent::StateChanged {
                    state,
                    detail,
                    error,
                    ..
                }) => {
                    if let Some(err) = error {
                        tracing::warn!(?state, ?err, "daemon entered error state");
                    } else {
                        tracing::info!(?state, ?detail, "daemon state changed");
                    }
                }
                Ok(DaemonEvent::TranscriptDelivered {
                    text,
                    target_app,
                    outcome,
                    ..
                }) => {
                    tracing::info!(
                        chars = text.chars().count(),
                        target_app = target_app.as_deref().unwrap_or(""),
                        ?outcome,
                        "transcript delivered"
                    );
                }
                Ok(DaemonEvent::ConfigReloaded) => {
                    let reloaded = reload_handle.config();
                    tracing::info!(mode = reloaded.mode.as_str(), "config reloaded");
                    reload_backend.sync_deepgram_warm_state(&reloaded).await;
                }
                Ok(_) => {}
                Err(tokio::sync::broadcast::error::RecvError::Lagged(skipped)) => {
                    tracing::debug!(
                        skipped,
                        "daemon event subscriber lagged; resyncing warm state"
                    );
                    let current = reload_handle.config();
                    reload_backend.sync_deepgram_warm_state(&current).await;
                }
                Err(tokio::sync::broadcast::error::RecvError::Closed) => break,
            }
        }
    });

    // Wait for `quit`/`exit` or EOF on stdin to perform a clean shutdown without extra signal crates.
    let _ = tokio::task::spawn_blocking(|| {
        let stdin = io::stdin();
        for line in stdin.lock().lines() {
            match line {
                Ok(text) => {
                    let cmd = text.trim();
                    if cmd.eq_ignore_ascii_case("quit") || cmd.eq_ignore_ascii_case("exit") {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
    })
    .await;

    daemon.shutdown().await;
    logger.abort();
    let _ = logger.await;
    backend.shutdown().await;
    Ok(())
}

fn init_tracing(debug: bool) {
    let default_level = if debug { "info" } else { "warn" };
    let filter =
        EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new(default_level));

    let _ = tracing_subscriber::fmt()
        .with_writer(io::stderr)
        .with_env_filter(filter)
        .with_target(false)
        .try_init();
}
