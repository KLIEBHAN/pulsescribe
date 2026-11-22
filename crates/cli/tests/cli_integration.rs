//! End-to-end integration tests running the compiled `pulsescribe` CLI binary against local
//! HTTP/1.1 mock servers and isolated temporary `.env` directories.

use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};

use pulsescribe_audio::write_wav_i16;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::oneshot;

static TEMP_SEQ: AtomicU64 = AtomicU64::new(0);

struct TempDir {
    path: PathBuf,
}

impl TempDir {
    fn new(prefix: &str) -> Self {
        let id = TEMP_SEQ.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "pulsescribe_cli_test_{prefix}_{}_{}",
            std::process::id(),
            id
        ));
        std::fs::create_dir_all(&path).unwrap();
        Self { path }
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

fn bin_command(home: &std::path::Path, cwd: &std::path::Path) -> Command {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_pulsescribe"));
    cmd.current_dir(cwd)
        .env("HOME", home)
        .env("USERPROFILE", home)
        .env_remove("PULSESCRIBE_MODE")
        .env_remove("PULSESCRIBE_MODEL")
        .env_remove("PULSESCRIBE_LANGUAGE")
        .env_remove("PULSESCRIBE_DEBUG")
        .env_remove("PULSESCRIBE_REFINE")
        .env_remove("PULSESCRIBE_REFINE_MODEL")
        .env_remove("PULSESCRIBE_REFINE_PROVIDER")
        .env_remove("OPENAI_COMPATIBLE_BASE_URL")
        .env_remove("OPENAI_COMPATIBLE_API_KEY")
        .env_remove("OPENAI_API_KEY")
        .env_remove("DEEPGRAM_API_KEY")
        .env_remove("GROQ_API_KEY");
    cmd
}

async fn spawn_mock_json_server(
    body: &'static str,
) -> (String, oneshot::Receiver<(String, String, Vec<u8>)>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let (tx, rx) = oneshot::channel();

    tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let req = read_http_request(&mut socket).await;
        let _ = tx.send(req);

        let resp = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
        let _ = socket.write_all(resp.as_bytes()).await;
        let _ = socket.shutdown().await;
    });

    (format!("http://{addr}/v1"), rx)
}

async fn read_http_request(socket: &mut TcpStream) -> (String, String, Vec<u8>) {
    let mut raw = Vec::new();
    let mut chunk = [0u8; 4096];
    let header_end;

    loop {
        let n = socket.read(&mut chunk).await.unwrap();
        assert!(n > 0);
        raw.extend_from_slice(&chunk[..n]);
        if let Some(pos) = raw.windows(4).position(|w| w == b"\r\n\r\n") {
            header_end = pos + 4;
            break;
        }
    }

    let head = String::from_utf8_lossy(&raw[..header_end]);
    let mut lines = head.split("\r\n");
    let first = lines.next().unwrap();
    let mut parts = first.split_whitespace();
    let method = parts.next().unwrap().to_string();
    let target = parts.next().unwrap().to_string();

    let mut headers = HashMap::new();
    for line in lines {
        if let Some((k, v)) = line.split_once(':') {
            headers.insert(k.trim().to_ascii_lowercase(), v.trim().to_string());
        }
    }

    let content_length: usize = headers
        .get("content-length")
        .and_then(|v| v.parse().ok())
        .unwrap_or(0);
    let mut body = raw[header_end..].to_vec();
    while body.len() < content_length {
        let n = socket.read(&mut chunk).await.unwrap();
        if n == 0 {
            break;
        }
        body.extend_from_slice(&chunk[..n]);
    }

    (method, target, body)
}

#[test]
fn help_lists_expected_flags_and_env_variables() {
    let tmp = TempDir::new("help");
    let output = bin_command(&tmp.path, &tmp.path)
        .arg("--help")
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("Audio transkribieren"));
    assert!(stdout.contains("-r, --record"));
    assert!(stdout.contains("-c, --copy"));
    assert!(stdout.contains("--mode"));
    assert!(stdout.contains("PULSESCRIBE_MODE"));
    assert!(stdout.contains("--refine"));
    assert!(stdout.contains("PULSESCRIBE_REFINE"));
    assert!(stdout.contains("--no-refine"));
    assert!(stdout.contains("--format"));
    assert!(stdout.contains("--context"));
}

#[test]
fn argument_validation_rejects_missing_conflicting_and_nonexistent_audio() {
    let tmp = TempDir::new("args");

    // Neither file nor --record
    let empty = bin_command(&tmp.path, &tmp.path).output().unwrap();
    assert!(!empty.status.success());
    assert!(String::from_utf8_lossy(&empty.stderr).contains("Audiodatei"));

    // Both file and --record
    let conflict = bin_command(&tmp.path, &tmp.path)
        .args(["sample.wav", "--record"])
        .output()
        .unwrap();
    assert!(!conflict.status.success());
    assert!(String::from_utf8_lossy(&conflict.stderr).contains("schliessen sich aus"));

    // Missing file fails cleanly with exit code 1
    let missing = bin_command(&tmp.path, &tmp.path)
        .arg(tmp.path.join("missing.wav"))
        .output()
        .unwrap();
    assert_eq!(missing.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&missing.stderr).contains("Datei nicht gefunden"));
}

#[tokio::test]
async fn end_to_end_dotenv_mode_transcribes_wav_and_writes_only_transcript_to_stdout() {
    let tmp = TempDir::new("e2e");
    let wav_path = tmp.path.join("clip.wav");
    write_wav_i16(&wav_path, &vec![0.0_f32; 1_600], 16_000, 1).unwrap();

    let (base_url, req_rx) = spawn_mock_json_server(r#"{"text":"Hallo aus Rust."}"#).await;

    // Put mode + base URL into local .env to verify runtime .env loading & precedence.
    std::fs::write(
        tmp.path.join(".env"),
        format!("PULSESCRIBE_MODE=openai_compatible\nOPENAI_COMPATIBLE_BASE_URL={base_url}\n"),
    )
    .unwrap();

    let tmp_path = tmp.path.clone();
    let wav_clone = wav_path.clone();
    let output = tokio::task::spawn_blocking(move || {
        bin_command(&tmp_path, &tmp_path)
            .args([
                wav_clone.to_str().unwrap(),
                "--language",
                "de",
                "--format",
                "json",
                "--refine",
            ])
            .output()
            .unwrap()
    })
    .await
    .unwrap();

    assert!(
        output.status.success(),
        "CLI failed: stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&output.stdout).trim(),
        "Hallo aus Rust."
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("--format wird im openai_compatible-Modus ignoriert"));
    assert!(stderr.contains("LLM-Nachbearbeitung wird für --format json übersprungen"));

    let (method, target, body) = req_rx.await.unwrap();
    assert_eq!(method, "POST");
    assert_eq!(target, "/v1/audio/transcriptions");
    let body_text = String::from_utf8_lossy(&body);
    assert!(body_text.contains("name=\"language\"\r\n\r\nde"));
}

#[test]
fn invalid_dotenv_mode_exits_with_clear_error() {
    let tmp = TempDir::new("bad_env");
    let wav_path = tmp.path.join("clip.wav");
    write_wav_i16(&wav_path, &vec![0.0_f32; 160], 16_000, 1).unwrap();
    std::fs::write(tmp.path.join(".env"), "PULSESCRIBE_MODE=invalid_backend\n").unwrap();

    let output = bin_command(&tmp.path, &tmp.path)
        .arg(&wav_path)
        .output()
        .unwrap();

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("PULSESCRIBE_MODE"));
    assert!(stderr.contains("invalid_backend"));
}

#[tokio::test]
async fn refine_missing_api_key_falls_back_to_raw_transcript_with_exit_zero() {
    let tmp = TempDir::new("refine_fallback");
    let wav_path = tmp.path.join("clip.wav");
    write_wav_i16(&wav_path, &vec![0.0_f32; 1_600], 16_000, 1).unwrap();

    let (base_url, _req_rx) =
        spawn_mock_json_server(r#"{"text":"Rohtext bleibt erhalten."}"#).await;

    let tmp_path = tmp.path.clone();
    let wav_clone = wav_path.clone();
    let output = tokio::task::spawn_blocking(move || {
        bin_command(&tmp_path, &tmp_path)
            .env("OPENAI_COMPATIBLE_BASE_URL", &base_url)
            .args([
                wav_clone.to_str().unwrap(),
                "--mode",
                "openai_compatible",
                "--refine",
            ])
            .output()
            .unwrap()
    })
    .await
    .unwrap();

    assert!(output.status.success());
    assert_eq!(
        String::from_utf8_lossy(&output.stdout).trim(),
        "Rohtext bleibt erhalten."
    );
    assert!(String::from_utf8_lossy(&output.stderr).contains("GROQ_API_KEY"));
}
