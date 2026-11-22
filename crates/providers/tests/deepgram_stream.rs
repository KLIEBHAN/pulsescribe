use std::future::Future;
use std::sync::Arc;
use std::time::{Duration, Instant};

use bytes::Bytes;
use futures_util::{SinkExt, StreamExt};
use pulsescribe_providers::{
    DeepgramStreamClient, DeepgramStreamConfig, DeepgramStreamEvent, DeepgramStreamTimeouts,
    ProviderError,
};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::{mpsc, Mutex};
use tokio_tungstenite::tungstenite::handshake::server::{Request, Response};
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{accept_hdr_async, WebSocketStream};

#[derive(Debug, Clone)]
struct HandshakeCapture {
    path_and_query: String,
    authorization: Option<String>,
}

fn results_json(transcript: &str, is_final: bool, from_finalize: bool) -> String {
    serde_json::json!({
        "type": "Results",
        "is_final": is_final,
        "from_finalize": from_finalize,
        "channel": { "alternatives": [{ "transcript": transcript }] },
    })
    .to_string()
}

#[allow(clippy::result_large_err)]
async fn spawn_ws_server<F, Fut>(
    handler: F,
) -> (
    String,
    Arc<Mutex<Option<HandshakeCapture>>>,
    tokio::task::JoinHandle<()>,
)
where
    F: FnOnce(WebSocketStream<TcpStream>) -> Fut + Send + 'static,
    Fut: Future<Output = ()> + Send + 'static,
{
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let capture = Arc::new(Mutex::new(None));
    let capture_bg = Arc::clone(&capture);

    let join = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut captured = None;
        let ws = accept_hdr_async(stream, |req: &Request, resp: Response| {
            captured = Some(HandshakeCapture {
                path_and_query: req
                    .uri()
                    .path_and_query()
                    .map(|pq| pq.as_str().to_string())
                    .unwrap_or_default(),
                authorization: req
                    .headers()
                    .get("authorization")
                    .and_then(|v| v.to_str().ok())
                    .map(ToOwned::to_owned),
            });
            Ok(resp)
        })
        .await
        .unwrap();
        *capture_bg.lock().await = captured;
        handler(ws).await;
    });

    (format!("ws://{addr}/v1/listen"), capture, join)
}

fn fast_timeouts() -> DeepgramStreamTimeouts {
    DeepgramStreamTimeouts {
        connect: Duration::from_secs(2),
        send_media: Duration::from_secs(2),
        finalize: Duration::from_millis(500),
        empty_finalize_grace: Duration::from_millis(200),
        tail_padding: Duration::from_millis(50),
        finalize_send: Duration::from_millis(300),
        close_stream_send: Duration::from_millis(300),
        close: Duration::from_millis(200),
        keepalive_interval: Duration::from_millis(40),
        keepalive_send: Duration::from_millis(300),
    }
}

#[tokio::test]
async fn streaming_session_sends_pcm_tail_padding_finalize_and_close_stream() {
    let observed_controls = Arc::new(Mutex::new(Vec::<String>::new()));
    let observed_audio_bytes = Arc::new(Mutex::new(0usize));
    let controls_bg = Arc::clone(&observed_controls);
    let bytes_bg = Arc::clone(&observed_audio_bytes);

    let (ws_url, capture, server) = spawn_ws_server(move |mut ws| async move {
        let mut sent_initial = false;
        while let Some(Ok(msg)) = ws.next().await {
            match msg {
                Message::Binary(data) => {
                    *bytes_bg.lock().await += data.len();
                    if !sent_initial {
                        sent_initial = true;
                        ws.send(Message::text(results_json("Hallo", false, false)))
                            .await
                            .unwrap();
                        ws.send(Message::text(results_json("Hallo", true, false)))
                            .await
                            .unwrap();
                    }
                }
                Message::Text(text) => {
                    controls_bg.lock().await.push(text.to_string());
                    if text.contains("Finalize") {
                        ws.send(Message::text(results_json("Welt.", true, true)))
                            .await
                            .unwrap();
                    } else if text.contains("CloseStream") {
                        let _ = ws.close(None).await;
                        break;
                    }
                }
                Message::Close(_) => break,
                _ => {}
            }
        }
    })
    .await;

    let client = DeepgramStreamClient::with_ws_url(&ws_url, "dg-stream-key").unwrap();
    let config = DeepgramStreamConfig::default()
        .with_model("nova-3")
        .with_language("auto")
        .with_keywords(vec!["PulseScribe".to_string()])
        .with_timeouts(fast_timeouts());

    let (audio_tx, audio_rx) = mpsc::channel(4);
    let (event_tx, mut event_rx) = mpsc::channel(8);

    audio_tx.send(Bytes::from(vec![1u8; 3_200])).await.unwrap();
    drop(audio_tx);

    let result = client
        .transcribe_stream(&config, audio_rx, Some(event_tx))
        .await
        .unwrap();
    server.await.unwrap();

    assert_eq!(result.transcript, "Hallo Welt.");
    assert!(!result.used_prewarmed_connection);
    assert!(!result.used_interim_fallback);
    assert!(result.finalize_acknowledged);
    // 3200 PCM bytes + 50ms linear16 16kHz mono silence (1600 bytes) = 4800 bytes
    assert_eq!(result.bytes_sent, 4_800);
    assert_eq!(*observed_audio_bytes.lock().await, 4_800);
    assert_eq!(
        *observed_controls.lock().await,
        vec![r#"{"type":"Finalize"}"#, r#"{"type":"CloseStream"}"#]
    );

    let handshake = capture.lock().await.clone().unwrap();
    assert_eq!(
        handshake.authorization.as_deref(),
        Some("Token dg-stream-key")
    );
    assert!(handshake.path_and_query.contains("language=multi"));
    assert!(handshake.path_and_query.contains("encoding=linear16"));
    assert!(handshake.path_and_query.contains("sample_rate=16000"));
    assert!(handshake.path_and_query.contains("keyterm=PulseScribe"));

    let mut events = Vec::new();
    while let Ok(ev) = event_rx.try_recv() {
        events.push(ev);
    }
    assert!(events.contains(&DeepgramStreamEvent::Interim("Hallo".to_string())));
    assert!(events.contains(&DeepgramStreamEvent::Final {
        text: "Hallo".to_string(),
        from_finalize: false,
    }));
    assert!(events.contains(&DeepgramStreamEvent::Final {
        text: "Welt.".to_string(),
        from_finalize: true,
    }));
}

#[tokio::test]
async fn empty_finalize_ack_waits_for_late_final_and_exits_grace_early() {
    let (ws_url, _capture, server) = spawn_ws_server(|mut ws| async move {
        while let Some(Ok(msg)) = ws.next().await {
            if let Message::Text(text) = msg {
                if text.contains("Finalize") {
                    ws.send(Message::text(results_json("", true, true)))
                        .await
                        .unwrap();
                    tokio::time::sleep(Duration::from_millis(40)).await;
                    ws.send(Message::text(results_json("Spätes Wort.", true, false)))
                        .await
                        .unwrap();
                } else if text.contains("CloseStream") {
                    let _ = ws.close(None).await;
                    break;
                }
            }
        }
    })
    .await;

    let mut timeouts = fast_timeouts();
    timeouts.tail_padding = Duration::ZERO;
    timeouts.empty_finalize_grace = Duration::from_secs(2);

    let client = DeepgramStreamClient::with_ws_url(&ws_url, "dg-key").unwrap();
    let config = DeepgramStreamConfig::default().with_timeouts(timeouts);

    let (audio_tx, audio_rx) = mpsc::channel(2);
    audio_tx
        .send(Bytes::from_static(&[0, 1, 2, 3]))
        .await
        .unwrap();
    drop(audio_tx);

    let started = Instant::now();
    let result = client
        .transcribe_stream(&config, audio_rx, None)
        .await
        .unwrap();
    let elapsed = started.elapsed();
    server.await.unwrap();

    assert_eq!(result.transcript, "Spätes Wort.");
    assert!(result.finalize_acknowledged);
    assert!(
        elapsed < Duration::from_millis(600),
        "empty-finalize grace did not exit early: {elapsed:?}"
    );
}

#[tokio::test]
async fn falls_back_to_last_interim_transcript_when_no_final_arrives() {
    let (ws_url, _capture, server) = spawn_ws_server(|mut ws| async move {
        let mut sent = false;
        while let Some(Ok(msg)) = ws.next().await {
            match msg {
                Message::Binary(_) if !sent => {
                    sent = true;
                    ws.send(Message::text(results_json("Kurz", false, false)))
                        .await
                        .unwrap();
                }
                Message::Text(text) if text.contains("Finalize") => {
                    ws.send(Message::text(results_json("", true, true)))
                        .await
                        .unwrap();
                }
                Message::Text(text) if text.contains("CloseStream") => {
                    let _ = ws.close(None).await;
                    break;
                }
                _ => {}
            }
        }
    })
    .await;

    let mut timeouts = fast_timeouts();
    timeouts.tail_padding = Duration::ZERO;
    timeouts.empty_finalize_grace = Duration::from_millis(40);

    let client = DeepgramStreamClient::with_ws_url(&ws_url, "dg-key").unwrap();
    let config = DeepgramStreamConfig::default().with_timeouts(timeouts);

    let (audio_tx, audio_rx) = mpsc::channel(2);
    audio_tx
        .send(Bytes::from_static(&[1, 2, 3, 4]))
        .await
        .unwrap();
    drop(audio_tx);

    let result = client
        .transcribe_stream(&config, audio_rx, None)
        .await
        .unwrap();
    server.await.unwrap();

    assert_eq!(result.transcript, "Kurz");
    assert!(result.used_interim_fallback);
}

#[tokio::test]
async fn unresponsive_close_handshake_is_bounded_by_adr001_close_timeout() {
    let (release_tx, release_rx) = tokio::sync::oneshot::channel::<()>();

    let (ws_url, _capture, server) = spawn_ws_server(move |mut ws| async move {
        let mut release = Some(release_rx);
        while let Some(Ok(msg)) = ws.next().await {
            if let Message::Text(text) = msg {
                if text.contains("Finalize") {
                    ws.send(Message::text(results_json("Schnell fertig", true, true)))
                        .await
                        .unwrap();
                } else if text.contains("CloseStream") {
                    // Deliberately stall and never send a WebSocket Close reply.
                    if let Some(rx) = release.take() {
                        let _ = rx.await;
                    }
                    break;
                }
            }
        }
    })
    .await;

    let mut timeouts = fast_timeouts();
    timeouts.tail_padding = Duration::ZERO;
    timeouts.close = Duration::from_millis(120);

    let client = DeepgramStreamClient::with_ws_url(&ws_url, "dg-key").unwrap();
    let config = DeepgramStreamConfig::default().with_timeouts(timeouts);

    let (audio_tx, audio_rx) = mpsc::channel(2);
    audio_tx.send(Bytes::from_static(&[0, 1])).await.unwrap();
    drop(audio_tx);

    let started = Instant::now();
    let result = client
        .transcribe_stream(&config, audio_rx, None)
        .await
        .unwrap();
    let elapsed = started.elapsed();

    let _ = release_tx.send(());
    server.await.unwrap();

    assert_eq!(result.transcript, "Schnell fertig");
    assert!(
        elapsed < Duration::from_millis(600),
        "close handshake hung past bounded ADR-001 close_timeout: {elapsed:?}"
    );
}

#[tokio::test]
async fn server_error_frame_returns_websocket_error_without_waiting_for_finalize() {
    let (ws_url, _capture, server) = spawn_ws_server(|mut ws| async move {
        if let Some(Ok(_)) = ws.next().await {
            let err =
                r#"{"type":"Error","err_code":"INVALID_AUTH","err_msg":"Invalid credentials"}"#;
            let _ = ws.send(Message::text(err)).await;
        }
    })
    .await;

    let client = DeepgramStreamClient::with_ws_url(&ws_url, "bad-key").unwrap();
    let config = DeepgramStreamConfig::default().with_timeouts(fast_timeouts());

    let (audio_tx, audio_rx) = mpsc::channel(2);
    audio_tx.send(Bytes::from_static(&[1, 2])).await.unwrap();
    drop(audio_tx);

    let err = client
        .transcribe_stream(&config, audio_rx, None)
        .await
        .unwrap_err();
    server.await.unwrap();

    match err {
        ProviderError::WebSocket { provider, detail } => {
            assert_eq!(provider, "deepgram");
            assert!(detail.contains("Invalid credentials"));
        }
        other => panic!("expected ProviderError::WebSocket, got {other:?}"),
    }
}
