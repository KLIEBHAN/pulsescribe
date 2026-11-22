//! Cross-platform clipboard export via `arboard` for `-c/--copy`.
//!
//! On macOS and Windows, `arboard::Clipboard::set_text` hands content directly to the OS
//! clipboard service. On Linux (X11/Wayland), the process setting the selection must stay alive
//! to serve paste requests until another owner replaces it, so we spawn a detached self-helper
//! passing the transcript privately over `stdin` (`arboard::SetExtLinux::wait`).

use std::process::ExitCode;

#[cfg(target_os = "linux")]
const CLIPBOARD_DAEMON_ARG: &str = "__pulsescribe_serve_clipboard";

/// If invoked as the internal Linux clipboard helper, serve the clipboard until replaced and
/// return `Some(ExitCode)` so `main()` exits immediately. No-op on macOS and Windows.
#[cfg(target_os = "linux")]
pub fn maybe_run_clipboard_daemon() -> Option<ExitCode> {
    if std::env::args_os().nth(1).as_deref() != Some(std::ffi::OsStr::new(CLIPBOARD_DAEMON_ARG)) {
        return None;
    }
    Some(run_linux_clipboard_daemon())
}

#[cfg(not(target_os = "linux"))]
pub fn maybe_run_clipboard_daemon() -> Option<ExitCode> {
    None
}

/// Copy UTF-8 text (including umlauts and multi-line transcripts) into the system clipboard.
#[cfg(not(target_os = "linux"))]
pub fn copy_to_clipboard(text: &str) -> Result<(), String> {
    let mut clipboard = arboard::Clipboard::new().map_err(|err| err.to_string())?;
    clipboard
        .set_text(text.to_owned())
        .map_err(|err| err.to_string())
}

/// Copy UTF-8 text on Linux via a detached helper process so clipboard contents survive CLI exit.
#[cfg(target_os = "linux")]
pub fn copy_to_clipboard(text: &str) -> Result<(), String> {
    use std::io::{BufRead, BufReader, Read, Write};
    use std::process::{Command, Stdio};

    let exe = std::env::current_exe().map_err(|err| err.to_string())?;
    let mut child = Command::new(exe)
        .arg(CLIPBOARD_DAEMON_ARG)
        .current_dir("/")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|err| err.to_string())?;

    if let Some(mut stdin) = child.stdin.take() {
        stdin
            .write_all(text.as_bytes())
            .map_err(|err| err.to_string())?;
    }

    let mut status_line = String::new();
    if let Some(stdout) = child.stdout.take() {
        let mut reader = BufReader::new(stdout);
        reader
            .read_line(&mut status_line)
            .map_err(|err| err.to_string())?;
    }

    if status_line.trim() == "ok" {
        return Ok(());
    }

    let mut err_detail = String::new();
    if let Some(mut stderr) = child.stderr.take() {
        let _ = stderr.read_to_string(&mut err_detail);
    }
    let _ = child.wait();
    let msg = err_detail.trim();
    if msg.is_empty() {
        Err("Linux clipboard helper exited before acquiring clipboard ownership".to_string())
    } else {
        Err(msg.to_string())
    }
}

#[cfg(target_os = "linux")]
fn run_linux_clipboard_daemon() -> ExitCode {
    use std::io::{self, Read, Write};

    use arboard::SetExtLinux;

    let mut text = String::new();
    if let Err(err) = io::stdin().read_to_string(&mut text) {
        let _ = writeln!(io::stderr(), "{err}");
        return ExitCode::FAILURE;
    }

    let mut clipboard = match arboard::Clipboard::new() {
        Ok(cb) => cb,
        Err(err) => {
            let _ = writeln!(io::stderr(), "{err}");
            return ExitCode::FAILURE;
        }
    };

    {
        let mut stdout = io::stdout();
        let _ = writeln!(stdout, "ok");
        let _ = stdout.flush();
    }

    match clipboard.set().wait().text(text) {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            let _ = writeln!(io::stderr(), "{err}");
            ExitCode::FAILURE
        }
    }
}
