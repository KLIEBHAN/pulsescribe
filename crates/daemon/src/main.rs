//! Headless `pulsescribe-daemon` executable wired to native platform adapters and
//! [`LiveDictationBackend`].

use std::io::{self, BufRead, IsTerminal};
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::Arc;

use clap::Parser;
use pulsescribe_core::paths::AppPaths;
use pulsescribe_core::PreferencesStore;
use pulsescribe_daemon::{
    DaemonConfig, DaemonEvent, DaemonMode, DaemonPlatform, DaemonRuntime, DaemonRuntimeOptions,
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

    /// Exit cleanly when stdin reaches EOF (for supervised parent pipes; enabled automatically on a TTY).
    #[arg(long)]
    exit_on_stdin_eof: bool,
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
        args.exit_on_stdin_eof,
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
    exit_on_stdin_eof: bool,
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

    let watch_stdin = io::stdin().is_terminal() || exit_on_stdin_eof;
    if watch_stdin {
        eprintln!(
            "pulsescribe-daemon gestartet (mode={}, config={}). Zum sauberen Beenden Ctrl+C drücken oder 'quit' eingeben.",
            config.mode.as_str(),
            config_dir.display()
        );
    } else {
        eprintln!(
            "pulsescribe-daemon gestartet (mode={}, config={}). Zum sauberen Beenden SIGINT/SIGTERM senden.",
            config.mode.as_str(),
            config_dir.display()
        );
    }

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
                    if reloaded.mode != DaemonMode::Local {
                        reload_backend.unload_local_model();
                    }
                    reload_backend.sync_deepgram_warm_state(&reloaded).await;
                }
                Ok(_) => {}
                Err(tokio::sync::broadcast::error::RecvError::Lagged(skipped)) => {
                    tracing::debug!(
                        skipped,
                        "daemon event subscriber lagged; resyncing warm state"
                    );
                    let current = reload_handle.config();
                    if current.mode != DaemonMode::Local {
                        reload_backend.unload_local_model();
                    }
                    reload_backend.sync_deepgram_warm_state(&current).await;
                }
                Err(tokio::sync::broadcast::error::RecvError::Closed) => break,
            }
        }
    });

    // Wait for termination signal (Ctrl+C / SIGTERM), or optional interactive stdin `quit`/`exit`.
    // Only monitor stdin on a TTY or when explicitly requested (`--exit-on-stdin-eof`) so headless
    // launches with stdin attached to `/dev/null` / `NUL` do not exit immediately on startup.
    let stdin_shutdown = if watch_stdin {
        let (stdin_tx, stdin_rx) = tokio::sync::oneshot::channel::<()>();
        std::thread::spawn(move || {
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
            let _ = stdin_tx.send(());
        });
        Some(stdin_rx)
    } else {
        None
    };

    wait_for_shutdown_signal(stdin_shutdown).await;

    daemon.shutdown().await;
    logger.abort();
    let _ = logger.await;
    backend.shutdown().await;
    Ok(())
}

async fn wait_for_shutdown_signal(stdin_rx: Option<tokio::sync::oneshot::Receiver<()>>) {
    let stdin_fut = async {
        match stdin_rx {
            Some(rx) => {
                let _ = rx.await;
            }
            None => std::future::pending::<()>().await,
        }
    };

    #[cfg(unix)]
    {
        let mut sigterm =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).ok();
        let sigterm_fut = async {
            match sigterm.as_mut() {
                Some(sig) => {
                    let _ = sig.recv().await;
                }
                None => std::future::pending::<()>().await,
            }
        };
        tokio::select! {
            res = tokio::signal::ctrl_c() => {
                if let Err(err) = res {
                    tracing::warn!(error = %err, "ctrl_c signal handler failed");
                }
            }
            _ = sigterm_fut => {}
            _ = stdin_fut => {}
        }
    }

    #[cfg(not(unix))]
    {
        tokio::select! {
            res = tokio::signal::ctrl_c() => {
                if let Err(err) = res {
                    tracing::warn!(error = %err, "ctrl_c signal handler failed");
                }
            }
            _ = stdin_fut => {}
        }
    }
}

fn init_tracing(debug: bool) {
    let default_level = if debug { "debug" } else { "warn" };
    let filter =
        EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new(default_level));

    let _ = tracing_subscriber::fmt()
        .with_writer(io::stderr)
        .with_env_filter(filter)
        .with_target(false)
        .try_init();
}
