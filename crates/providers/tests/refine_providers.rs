//! End-to-end integration tests for LLM transcript refinement (`Groq`, `OpenRouter`,
//! `OpenAI` Responses API, `Gemini`) against a local in-process HTTP/1.1 mock server.

use std::collections::HashMap;

use pulsescribe_core::prompts::PromptsConfig;
use pulsescribe_providers::{
    build_http_client, maybe_refine, LlmRefineProvider, OpenRouterRouting, RefineProviderKind,
    RefineRequest,
};
use serde_json::Value;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::oneshot;

#[derive(Debug)]
struct CapturedRequest {
    method: String,
    target: String,
    headers: HashMap<String, String>,
    body: String,
}

async fn spawn_json_mock(
    status: u16,
    response_body: impl Into<String>,
) -> (String, oneshot::Receiver<CapturedRequest>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let body = response_body.into();
    let (tx, rx) = oneshot::channel();

    tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let captured = read_http_request(&mut socket).await;
        let _ = tx.send(captured);

        let reason = if status == 200 { "OK" } else { "Error" };
        let resp = format!(
            "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
        let _ = socket.write_all(resp.as_bytes()).await;
        let _ = socket.shutdown().await;
    });

    (format!("http://{addr}/v1"), rx)
}

async fn read_http_request(socket: &mut TcpStream) -> CapturedRequest {
    let mut raw = Vec::new();
    let mut chunk = [0u8; 4096];
    let header_end;

    loop {
        let n = socket.read(&mut chunk).await.unwrap();
        assert!(n > 0, "unexpected EOF reading request headers");
        raw.extend_from_slice(&chunk[..n]);
        if let Some(pos) = raw.windows(4).position(|w| w == b"\r\n\r\n") {
            header_end = pos + 4;
            break;
        }
    }

    let head = String::from_utf8_lossy(&raw[..header_end]);
    let mut lines = head.split("\r\n");
    let req_line = lines.next().unwrap();
    let mut req_parts = req_line.split_whitespace();
    let method = req_parts.next().unwrap().to_string();
    let target = req_parts.next().unwrap().to_string();

    let mut headers = HashMap::new();
    for line in lines {
        if let Some((name, value)) = line.split_once(':') {
            headers.insert(name.trim().to_ascii_lowercase(), value.trim().to_string());
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

    CapturedRequest {
        method,
        target,
        headers,
        body: String::from_utf8(body).unwrap(),
    }
}

#[tokio::test]
async fn groq_refine_posts_chat_completions_and_extracts_segmented_content() {
    let (base_url, rx) = spawn_json_mock(
        200,
        r#"{"choices":[{"message":{"content":[{"type":"text","text":" Hallo"},{"type":"text","text":" Welt. "}]}}]}"#,
    )
    .await;

    let client = build_http_client().unwrap();
    let provider =
        LlmRefineProvider::with_base_url(client, RefineProviderKind::Groq, base_url, "gsk-test")
            .unwrap();
    let prompts = PromptsConfig::defaults();
    let req = RefineRequest::new("ähm hallo welt").with_context("email");

    let refined = provider.refine(&req, &prompts).await.unwrap();
    assert_eq!(refined, "Hallo Welt.");

    let captured = rx.await.unwrap();
    assert_eq!(captured.method, "POST");
    assert_eq!(captured.target, "/v1/chat/completions");
    assert_eq!(
        captured.headers.get("authorization").map(String::as_str),
        Some("Bearer gsk-test")
    );
    let body: Value = serde_json::from_str(&captured.body).unwrap();
    assert_eq!(body["model"], "openai/gpt-oss-120b");
    let content = body["messages"][0]["content"].as_str().unwrap();
    assert!(content.contains("E-Mail"));
    assert!(content.ends_with("\n\nTranskript:\nähm hallo welt"));
}

#[tokio::test]
async fn openrouter_refine_forwards_provider_routing_payload() {
    let (base_url, rx) = spawn_json_mock(
        200,
        r#"{"choices":[{"message":{"content":"  Glatter Satz.  "}}]}"#,
    )
    .await;

    let client = build_http_client().unwrap();
    let provider = LlmRefineProvider::with_base_url(
        client,
        RefineProviderKind::OpenRouter,
        base_url,
        "or-key",
    )
    .unwrap()
    .with_openrouter_routing(OpenRouterRouting::parse(" openai, , groq ", false));

    let refined = provider
        .refine_with_prompt("rohtext", "Bitte glätten", None)
        .await
        .unwrap();
    assert_eq!(refined, "Glatter Satz.");

    let captured = rx.await.unwrap();
    assert_eq!(captured.target, "/v1/chat/completions");
    let body: Value = serde_json::from_str(&captured.body).unwrap();
    assert_eq!(
        body["provider"]["order"],
        serde_json::json!(["openai", "groq"])
    );
    assert_eq!(body["provider"]["allow_fallbacks"], false);
}

#[tokio::test]
async fn openai_gpt5_uses_responses_api_with_minimal_reasoning() {
    let (base_url, rx) = spawn_json_mock(
        200,
        r#"{"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":" Fertig. "}]}]}"#,
    )
    .await;

    let client = build_http_client().unwrap();
    let provider =
        LlmRefineProvider::with_base_url(client, RefineProviderKind::OpenAi, base_url, "sk-test")
            .unwrap();
    let prompts = PromptsConfig::defaults();
    let req = RefineRequest::new("roh")
        .with_model("gpt-5-mini")
        .with_prompt("Nur korrigieren");

    let refined = provider.refine(&req, &prompts).await.unwrap();
    assert_eq!(refined, "Fertig.");

    let captured = rx.await.unwrap();
    assert_eq!(captured.target, "/v1/responses");
    let body: Value = serde_json::from_str(&captured.body).unwrap();
    assert_eq!(body["model"], "gpt-5-mini");
    assert_eq!(body["reasoning"]["effort"], "minimal");
    assert_eq!(body["input"], "Nur korrigieren\n\nTranskript:\nroh");

    // GPT-5.1 rejects "minimal" reasoning effort and must omit it.
    let (base_url_51, rx_51) = spawn_json_mock(200, r#"{"output_text":"Bereit."}"#).await;
    let provider_51 = LlmRefineProvider::with_base_url(
        build_http_client().unwrap(),
        RefineProviderKind::OpenAi,
        base_url_51,
        "sk-test",
    )
    .unwrap();
    let req_51 = RefineRequest::new("roh")
        .with_model("gpt-5.1")
        .with_prompt("Nur korrigieren");
    assert_eq!(
        provider_51.refine(&req_51, &prompts).await.unwrap(),
        "Bereit."
    );
    let body_51: Value = serde_json::from_str(&rx_51.await.unwrap().body).unwrap();
    assert_eq!(body_51["model"], "gpt-5.1");
    assert!(body_51.get("reasoning").is_none());
}

#[tokio::test]
async fn gemini_uses_header_auth_thinking_level_and_skips_thought_parts() {
    let (base_url, rx) = spawn_json_mock(
        200,
        r#"{"candidates":[{"content":{"parts":[{"text":"thinking...","thought":true},{"text":" Sauber. "}]}}]}"#,
    )
    .await;

    let client = build_http_client().unwrap();
    let provider = LlmRefineProvider::with_base_url(
        client,
        RefineProviderKind::Gemini,
        base_url,
        "gem-secret",
    )
    .unwrap();

    let refined = provider
        .refine_with_prompt("roh", "Prompt", Some("gemini-3-pro"))
        .await
        .unwrap();
    assert_eq!(refined, "Sauber.");

    let captured = rx.await.unwrap();
    assert_eq!(captured.target, "/v1/models/gemini-3-pro:generateContent");
    assert!(!captured.target.contains("gem-secret"));
    assert_eq!(
        captured.headers.get("x-goog-api-key").map(String::as_str),
        Some("gem-secret")
    );
    let body: Value = serde_json::from_str(&captured.body).unwrap();
    assert_eq!(
        body["generationConfig"]["thinkingConfig"]["thinkingLevel"],
        "LOW"
    );
    assert_eq!(
        body["contents"][0]["parts"][0]["text"],
        "Prompt\n\nTranskript:\nroh"
    );

    // Gemini 2.5 models must use thinkingBudget instead of thinkingLevel.
    let (base_url_25, rx_25) = spawn_json_mock(
        200,
        r#"{"candidates":[{"content":{"parts":[{"text":"Schnell."}]}}]}"#,
    )
    .await;
    let provider_25 = LlmRefineProvider::with_base_url(
        build_http_client().unwrap(),
        RefineProviderKind::Gemini,
        base_url_25,
        "gem-secret",
    )
    .unwrap();
    assert_eq!(
        provider_25
            .refine_with_prompt("roh", "Prompt", Some("gemini-2.5-flash"))
            .await
            .unwrap(),
        "Schnell."
    );
    let body_25: Value = serde_json::from_str(&rx_25.await.unwrap().body).unwrap();
    let thinking_25 = &body_25["generationConfig"]["thinkingConfig"];
    assert_eq!(thinking_25["thinkingBudget"], 0);
    assert!(thinking_25.get("thinkingLevel").is_none());
}

#[tokio::test]
async fn maybe_refine_returns_original_on_error_empty_response_or_blank_transcript() {
    let (base_url, _rx) = spawn_json_mock(500, r#"{"error":"upstream failed"}"#).await;
    let client = build_http_client().unwrap();
    let provider =
        LlmRefineProvider::with_base_url(client, RefineProviderKind::Groq, base_url, "gsk-test")
            .unwrap();
    let prompts = PromptsConfig::defaults();

    // Blank transcript skips HTTP call completely.
    let blank_req = RefineRequest::new("   \n ");
    assert_eq!(
        maybe_refine(Some(&provider), &blank_req, &prompts, true).await,
        "   \n "
    );

    // HTTP 500 falls back cleanly to original transcript.
    let req = RefineRequest::new("Originaltext");
    assert_eq!(
        maybe_refine(Some(&provider), &req, &prompts, true).await,
        "Originaltext"
    );

    // Blank LLM output also falls back cleanly to original transcript.
    let (empty_url, _rx2) =
        spawn_json_mock(200, r#"{"choices":[{"message":{"content":"   "}}]}"#).await;
    let empty_provider = LlmRefineProvider::with_base_url(
        build_http_client().unwrap(),
        RefineProviderKind::Groq,
        empty_url,
        "gsk-test",
    )
    .unwrap();
    assert_eq!(
        maybe_refine(Some(&empty_provider), &req, &prompts, true).await,
        "Originaltext"
    );

    // Disabled refine returns original even without provider.
    assert_eq!(
        maybe_refine(None, &req, &prompts, false).await,
        "Originaltext"
    );
}
