//! End-to-end integration tests for cloud REST transcription providers against a local
//! in-process HTTP/1.1 mock server (no external mock crates required).

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use pulsescribe_providers::{
    build_http_client, list_server_models, CloudProvider, DeepgramRestProvider, GroqProvider,
    OpenAiCompatibleProvider, OpenAiProvider, ProviderError, ResponseFormat, TranscriptionRequest,
};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::oneshot;

static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);

struct TempAudioFile {
    path: PathBuf,
}

impl TempAudioFile {
    fn new(ext: &str, bytes: &[u8]) -> Self {
        let id = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "pulsescribe_provider_test_{}_{}.{}",
            std::process::id(),
            id,
            ext
        ));
        std::fs::write(&path, bytes).unwrap();
        Self { path }
    }
}

impl Drop for TempAudioFile {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}

#[derive(Debug)]
struct CapturedRequest {
    method: String,
    target: String,
    headers: HashMap<String, String>,
    body: Vec<u8>,
}

struct MockResponse {
    status: u16,
    content_type: &'static str,
    body: String,
    delay: Duration,
}

impl MockResponse {
    fn ok_json(body: impl Into<String>) -> Self {
        Self {
            status: 200,
            content_type: "application/json",
            body: body.into(),
            delay: Duration::ZERO,
        }
    }

    fn ok_text(body: impl Into<String>) -> Self {
        Self {
            status: 200,
            content_type: "text/plain; charset=utf-8",
            body: body.into(),
            delay: Duration::ZERO,
        }
    }

    fn error(status: u16, body: impl Into<String>) -> Self {
        Self {
            status,
            content_type: "application/json",
            body: body.into(),
            delay: Duration::ZERO,
        }
    }
}

async fn spawn_mock_server(response: MockResponse) -> (String, oneshot::Receiver<CapturedRequest>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let (tx, rx) = oneshot::channel();

    tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let captured = read_http_request(&mut socket).await;
        let _ = tx.send(captured);

        if !response.delay.is_zero() {
            tokio::time::sleep(response.delay).await;
        }
        let payload = response.body.as_bytes();
        let reply = format!(
            "HTTP/1.1 {} OK\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
            response.status,
            response.content_type,
            payload.len()
        );
        let _ = socket.write_all(reply.as_bytes()).await;
        let _ = socket.write_all(payload).await;
        let _ = socket.shutdown().await;
    });

    (format!("http://{addr}/v1"), rx)
}

async fn read_http_request(socket: &mut TcpStream) -> CapturedRequest {
    let mut raw = Vec::new();
    let mut buf = [0_u8; 4096];
    let header_end = loop {
        let n = socket.read(&mut buf).await.unwrap();
        assert!(n > 0, "connection closed before headers completed");
        raw.extend_from_slice(&buf[..n]);
        if let Some(pos) = raw.windows(4).position(|w| w == b"\r\n\r\n") {
            break pos;
        }
    };

    let header_text = String::from_utf8(raw[..header_end].to_vec()).unwrap();
    let mut lines = header_text.split("\r\n");
    let request_line = lines.next().unwrap();
    let mut parts = request_line.split_whitespace();
    let method = parts.next().unwrap().to_string();
    let target = parts.next().unwrap().to_string();

    let mut headers = HashMap::new();
    for line in lines {
        if let Some((k, v)) = line.split_once(':') {
            headers.insert(k.trim().to_ascii_lowercase(), v.trim().to_string());
        }
    }

    let mut body = raw[header_end + 4..].to_vec();
    let is_chunked = headers
        .get("transfer-encoding")
        .is_some_and(|v| v.to_ascii_lowercase().contains("chunked"));

    if is_chunked {
        body = read_chunked_body(socket, body).await;
    } else if let Some(len) = headers
        .get("content-length")
        .and_then(|v| v.parse::<usize>().ok())
    {
        while body.len() < len {
            let n = socket.read(&mut buf).await.unwrap();
            if n == 0 {
                break;
            }
            body.extend_from_slice(&buf[..n]);
        }
        body.truncate(len);
    }

    CapturedRequest {
        method,
        target,
        headers,
        body,
    }
}

async fn read_chunked_body(socket: &mut TcpStream, mut buffered: Vec<u8>) -> Vec<u8> {
    let mut decoded = Vec::new();
    let mut buf = [0_u8; 4096];
    let mut cursor = 0_usize;

    loop {
        let line_end = loop {
            if let Some(rel) = buffered[cursor..].windows(2).position(|w| w == b"\r\n") {
                break cursor + rel;
            }
            let n = socket.read(&mut buf).await.unwrap();
            assert!(n > 0, "unexpected EOF in chunked body");
            buffered.extend_from_slice(&buf[..n]);
        };

        let size_str = std::str::from_utf8(&buffered[cursor..line_end])
            .unwrap()
            .split(';')
            .next()
            .unwrap()
            .trim();
        let chunk_size = usize::from_str_radix(size_str, 16).unwrap();
        cursor = line_end + 2;

        if chunk_size == 0 {
            break;
        }

        let needed = cursor + chunk_size + 2;
        while buffered.len() < needed {
            let n = socket.read(&mut buf).await.unwrap();
            assert!(n > 0, "unexpected EOF reading chunk payload");
            buffered.extend_from_slice(&buf[..n]);
        }
        decoded.extend_from_slice(&buffered[cursor..cursor + chunk_size]);
        cursor += chunk_size + 2;
    }

    decoded
}

#[tokio::test]
async fn openai_gpt4o_requests_json_and_extracts_plain_text() {
    let (base_url, req_rx) =
        spawn_mock_server(MockResponse::ok_json(r#"{"text":"Hallo aus OpenAI"}"#)).await;
    let audio = TempAudioFile::new("wav", b"RIFF-fake-wav-bytes");
    let client = build_http_client().unwrap();
    let provider = OpenAiProvider::with_base_url(client, base_url, "sk-test-123").unwrap();

    let request = TranscriptionRequest::new(&audio.path).with_language("de");
    let transcript = provider.transcribe(&request).await.unwrap();
    assert_eq!(transcript, "Hallo aus OpenAI");

    let captured = req_rx.await.unwrap();
    assert_eq!(captured.method, "POST");
    assert_eq!(captured.target, "/v1/audio/transcriptions");
    assert_eq!(
        captured.headers.get("authorization").map(String::as_str),
        Some("Bearer sk-test-123")
    );
    let body_text = String::from_utf8_lossy(&captured.body);
    assert!(body_text.contains("name=\"model\"\r\n\r\ngpt-4o-transcribe"));
    assert!(body_text.contains("name=\"response_format\"\r\n\r\njson"));
    assert!(body_text.contains("name=\"language\"\r\n\r\nde"));
    assert!(body_text.contains("RIFF-fake-wav-bytes"));
}

#[tokio::test]
async fn openai_whisper1_supports_srt_and_pretty_json_output() {
    let srt_payload = "1\n00:00:00,000 --> 00:00:01,500\nHallo Welt\n";
    let (base_url, req_rx) = spawn_mock_server(MockResponse::ok_text(srt_payload)).await;
    let audio = TempAudioFile::new("mp3", b"ID3-fake-mp3");
    let client = build_http_client().unwrap();
    let provider = OpenAiProvider::with_base_url(client.clone(), base_url, "sk-test").unwrap();

    let req = TranscriptionRequest::new(&audio.path)
        .with_model("whisper-1")
        .with_language("auto")
        .with_response_format(ResponseFormat::Srt);
    let out = provider.transcribe(&req).await.unwrap();
    assert_eq!(out, srt_payload);

    let captured = req_rx.await.unwrap();
    let body_text = String::from_utf8_lossy(&captured.body);
    assert!(body_text.contains("name=\"response_format\"\r\n\r\nsrt"));
    assert!(!body_text.contains("name=\"language\""));

    // Pretty-printed JSON output when ResponseFormat::Json is requested.
    let (json_url, _) = spawn_mock_server(MockResponse::ok_json(r#"{"text":"Hi"}"#)).await;
    let json_provider = OpenAiProvider::with_base_url(client, json_url, "sk-test").unwrap();
    let json_req =
        TranscriptionRequest::new(&audio.path).with_response_format(ResponseFormat::Json);
    let pretty = json_provider.transcribe(&json_req).await.unwrap();
    assert_eq!(pretty, "{\n  \"text\": \"Hi\"\n}");
}

#[tokio::test]
async fn groq_sends_temperature_zero_and_text_response_format() {
    let (base_url, req_rx) = spawn_mock_server(MockResponse::ok_text("Schnelles Transkript")).await;
    let audio = TempAudioFile::new("wav", b"groq-audio-payload");
    let client = build_http_client().unwrap();
    let provider =
        CloudProvider::Groq(GroqProvider::with_base_url(client, base_url, "gsk-secret").unwrap());

    let req = TranscriptionRequest::new(&audio.path).with_language("en");
    let transcript = provider.transcribe(&req).await.unwrap();
    assert_eq!(transcript, "Schnelles Transkript");

    let captured = req_rx.await.unwrap();
    assert_eq!(captured.target, "/v1/audio/transcriptions");
    assert_eq!(
        captured.headers.get("authorization").map(String::as_str),
        Some("Bearer gsk-secret")
    );
    let body_text = String::from_utf8_lossy(&captured.body);
    assert!(body_text.contains("name=\"model\"\r\n\r\nwhisper-large-v3"));
    assert!(body_text.contains("name=\"response_format\"\r\n\r\ntext"));
    assert!(body_text.contains("name=\"temperature\"\r\n\r\n0.0"));
    assert!(body_text.contains("name=\"language\"\r\n\r\nen"));
    assert!(body_text.contains("groq-audio-payload"));
}

#[tokio::test]
async fn deepgram_rest_streams_raw_audio_with_token_auth_and_keyterms() {
    let dg_json =
        r#"{"results":{"channels":[{"alternatives":[{"transcript":"PulseScribe läuft"}]}]}}"#;
    let (base_url, req_rx) = spawn_mock_server(MockResponse::ok_json(dg_json)).await;
    let payload = b"deepgram-raw-wav-stream";
    let audio = TempAudioFile::new("wav", payload);
    let client = build_http_client().unwrap();
    let provider = DeepgramRestProvider::with_base_url(client, base_url, "dg-token-xyz").unwrap();

    let req = TranscriptionRequest::new(&audio.path)
        .with_language("auto")
        .with_keywords(vec!["PulseScribe".to_string(), "Rust".to_string()]);
    let transcript = provider.transcribe(&req).await.unwrap();
    assert_eq!(transcript, "PulseScribe läuft");

    let captured = req_rx.await.unwrap();
    assert_eq!(captured.method, "POST");
    assert!(captured.target.starts_with("/v1/listen?"));
    assert!(captured.target.contains("model=nova-3"));
    assert!(captured.target.contains("smart_format=true"));
    assert!(captured.target.contains("punctuate=true"));
    assert!(captured.target.contains("detect_language=true"));
    assert!(captured.target.contains("keyterm=PulseScribe"));
    assert!(captured.target.contains("keyterm=Rust"));
    assert_eq!(
        captured.headers.get("authorization").map(String::as_str),
        Some("Token dg-token-xyz")
    );
    assert_eq!(
        captured.headers.get("content-type").map(String::as_str),
        Some("audio/wav")
    );
    assert_eq!(captured.body, payload);
}

#[tokio::test]
async fn openai_compatible_transcribes_with_placeholder_key_and_lists_deduped_models() {
    let (base_url, req_rx) =
        spawn_mock_server(MockResponse::ok_json(r#"{"text":"Lokal vom LAN-Server"}"#)).await;
    let audio = TempAudioFile::new("flac", b"fLaC-fake");
    let client = build_http_client().unwrap();
    let provider = OpenAiCompatibleProvider::new(
        client.clone(),
        base_url,
        None,
        Some("faster-whisper-large-v3".to_string()),
    )
    .unwrap();

    let transcript = provider
        .transcribe(&TranscriptionRequest::new(&audio.path))
        .await
        .unwrap();
    assert_eq!(transcript, "Lokal vom LAN-Server");

    let captured = req_rx.await.unwrap();
    assert_eq!(
        captured.headers.get("authorization").map(String::as_str),
        Some("Bearer not-needed")
    );
    let body_text = String::from_utf8_lossy(&captured.body);
    assert!(body_text.contains("name=\"model\"\r\n\r\nfaster-whisper-large-v3"));
    assert!(body_text.contains("name=\"response_format\"\r\n\r\njson"));

    let (models_url, models_rx) = spawn_mock_server(MockResponse::ok_json(
        r#"{"data":[{"id":"whisper-1"},{"id":"parakeet-v3"},{"id":"whisper-1"},{"id":""}]}"#,
    ))
    .await;
    let models = list_server_models(
        &client,
        &models_url,
        Some("lan-key"),
        Duration::from_secs(2),
    )
    .await
    .unwrap();
    assert_eq!(models, vec!["whisper-1", "parakeet-v3"]);
    let models_req = models_rx.await.unwrap();
    assert_eq!(models_req.method, "GET");
    assert_eq!(models_req.target, "/v1/models");
    assert_eq!(
        models_req.headers.get("authorization").map(String::as_str),
        Some("Bearer lan-key")
    );

    // Tolerate custom servers that return JSON body without application/json Content-Type.
    let (plain_json_url, _) =
        spawn_mock_server(MockResponse::ok_text(r#"{"text":"Ohne JSON-Header"}"#)).await;
    let plain_provider = OpenAiCompatibleProvider::new(client, plain_json_url, None, None).unwrap();
    let plain_transcript = plain_provider
        .transcribe(&TranscriptionRequest::new(&audio.path))
        .await
        .unwrap();
    assert_eq!(plain_transcript, "Ohne JSON-Header");
}

#[tokio::test]
async fn api_error_status_and_list_models_timeout_return_typed_errors() {
    let (err_url, _) = spawn_mock_server(MockResponse::error(
        401,
        r#"{"error":{"message":"Invalid API key"}}"#,
    ))
    .await;
    let audio = TempAudioFile::new("wav", b"data");
    let client = build_http_client().unwrap();
    let provider = GroqProvider::with_base_url(client.clone(), err_url, "bad-key").unwrap();

    let err = provider
        .transcribe(&TranscriptionRequest::new(&audio.path))
        .await
        .unwrap_err();
    assert!(matches!(
        err,
        ProviderError::ApiStatus {
            provider: "groq",
            status: 401,
            ..
        }
    ));

    let (slow_url, _) = spawn_mock_server(MockResponse {
        status: 200,
        content_type: "application/json",
        body: r#"{"data":[]}"#.to_string(),
        delay: Duration::from_millis(250),
    })
    .await;
    let timeout_err = list_server_models(&client, &slow_url, None, Duration::from_millis(40))
        .await
        .unwrap_err();
    assert!(matches!(timeout_err, ProviderError::Http(ref e) if e.is_timeout()));
}
