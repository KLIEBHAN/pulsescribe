//! `pulsescribe` CLI entrypoint.
//!
//! Loads `.env` before spawning the multi-threaded Tokio runtime so process environment setup
//! happens strictly while single-threaded, preserving `Process Env > ~/.pulsescribe/.env > ./.env`.

use std::collections::BTreeMap;
use std::io;
use std::process::ExitCode;

use clap::Parser;
use pulsescribe_cli::args::{resolve_cli_options, CliArgs};
use pulsescribe_cli::{clipboard, run_cli};
use pulsescribe_core::env::{current_process_env, resolve_environment};
use pulsescribe_core::paths::AppPaths;
use tracing_subscriber::EnvFilter;

fn main() -> ExitCode {
    if let Some(code) = clipboard::maybe_run_clipboard_daemon() {
        return code;
    }

    let args = CliArgs::parse();
    let env_map = load_dotenv_single_threaded();

    let debug_enabled = resolve_cli_options(&args, &env_map)
        .map(|resolved| resolved.debug)
        .unwrap_or(args.debug);
    init_tracing(debug_enabled);

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

    let mut stdout = io::stdout();
    let mut stderr = io::stderr();

    match runtime.block_on(run_cli(&args, &env_map, &mut stdout, &mut stderr)) {
        Ok(_) => ExitCode::SUCCESS,
        Err(err) => {
            let code = err.exit_code();
            let _ = io::Write::write_fmt(&mut stderr, format_args!("Fehler: {err}\n"));
            ExitCode::from(code as u8)
        }
    }
}

/// Resolve `.env` files (`~/.pulsescribe/.env` and `./.env`) and populate missing keys into
/// process env before spawning any worker threads.
fn load_dotenv_single_threaded() -> BTreeMap<String, String> {
    let process_env = current_process_env();
    let local_env = std::env::current_dir().ok().map(|dir| dir.join(".env"));
    let resolved = match AppPaths::default_user() {
        Some(paths) => resolve_environment(
            &process_env,
            paths.config_dir(),
            local_env.as_deref(),
            false,
        ),
        None => match local_env.as_deref() {
            Some(local) if local.exists() => {
                let mut merged = process_env.clone();
                for (k, v) in pulsescribe_core::env::read_dotenv_values(local, &process_env) {
                    merged.entry(k).or_insert(v);
                }
                merged
            }
            _ => process_env.clone(),
        },
    };

    for (key, value) in &resolved {
        if !process_env.contains_key(key) {
            std::env::set_var(key, value);
        }
    }
    resolved
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
