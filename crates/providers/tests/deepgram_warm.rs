use std::future::Future;
use std::sync::Arc;
use std::time::{Duration, Instant};

use bytes::Bytes;
use futures_util::{SinkExt, StreamExt};
use pulsescribe_providers::{
    DeepgramStreamClient, DeepgramStreamConfig, DeepgramStreamTimeouts,
    DeepgramWarmConnectionManager,
};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::{mpsc, Mutex};
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{accept_async, WebSocketStream};

fn final_results_json(transcript: &str) -> String {
    serde_json::json!({
        "type": "Results",
        "is_final": true,
        "from_finalize": true,
        "channel": { "alternatives": [{ "transcript": transcript }] },
    })
    .to_string()
}

async fn spawn_multi_ws_server<F, Fut>(
    max_connections: usize,
    handler: F,
) -> (String, Arc<Mutex<usize>>, tokio::task::JoinHandle<()>)
where
    F: Fn(usize, WebSocketStream<TcpStream>) -> Fut + Send + Sync + 'static,
    Fut: Future<Output = ()> + Send + 'static,
{
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let accepted = Arc::new(Mutex::new(0usize));
    let accepted_bg = Arc::clone(&accepted);
    let handler = Arc::new(handler);

    let join = tokio::spawn(async move {
        let mut handles = Vec::new();
        for idx in 0..max_connections {
            let Ok((stream, _)) = listener.accept().await else {
                break;
            };
            *accepted_bg.lock().await += 1;
            let handler_conn = Arc::clone(&handler);
            handles.push(tokio::spawn(async move {
                let ws = accept_async(stream).await.unwrap();
                handler_conn(idx, ws).await;
            }));
        }
        for handle in handles {
            let _ = handle.await;
        }
    });

    (format!("ws://{addr}/v1/listen"), accepted, join)
}

fn fast_warm_timeouts() -> DeepgramStreamTimeouts {
    DeepgramStreamTimeouts {
        connect: Duration::from_secs(2),
        send_media: Duration::from_secs(2),
        finalize: Duration::from_millis(500),
        empty_finalize_grace: Duration::from_millis(100),
        tail_padding: Duration::ZERO,
        finalize_send: Duration::from_millis(300),
        close_stream_send: Duration::from_millis(300),
        close: Duration::from_millis(200),
        keepalive_interval: Duration::from_millis(35),
        keepalive_send: Duration::from_millis(300),
    }
}

#[tokio::test]
async fn warm_manager_sends_keepalive_claims_once_and_falls_back_on_mismatch() {
    let keepalives = Arc::new(Mutex::new(0usize));
    let keepalives_bg = Arc::clone(&keepalives);

    let (ws_url, accepted, server) = spawn_multi_ws_server(3, move |_idx, mut ws| {
        let ka = Arc::clone(&keepalives_bg);
        async move {
            while let Some(Ok(msg)) = ws.next().await {
                match msg {
                    Message::Text(text) if text.contains("KeepAlive") => {
                        *ka.lock().await += 1;
                    }
                    Message::Text(text) if text.contains("Finalize") => {
                        let _ = ws.send(Message::text(final_results_json("Warm OK"))).await;
                    }
                    Message::Text(text) if text.contains("CloseStream") => {
                        let _ = ws.close(None).await;
                        break;
                    }
                    Message::Close(_) => break,
                    _ => {}
                }
            }
        }
    })
    .await;

    let client = DeepgramStreamClient::with_ws_url(&ws_url, "dg-warm-key").unwrap();
    let manager = DeepgramWarmConnectionManager::new(client);

    let cfg_de = DeepgramStreamConfig::default()
        .with_language("de")
        .with_timeouts(fast_warm_timeouts());

    // 1. Prewarm connection and verify KeepAlive ticks while idle.
    manager.warm_up(&cfg_de).await.unwrap();
    assert!(manager.has_ready_connection().await);

    let deadline = Instant::now() + Duration::from_secs(1);
    while *keepalives.lock().await == 0 && Instant::now() < deadline {
        tokio::time::sleep(Duration::from_millis(15)).await;
    }
    assert!(
        *keepalives.lock().await >= 1,
        "expected at least one KeepAlive frame from warm connection manager"
    );

    // 2. Claim the prewarmed socket once (conn #0); slot becomes empty afterwards.
    let (tx1, rx1) = mpsc::channel(2);
    tx1.send(Bytes::from_static(&[1, 2, 3, 4])).await.unwrap();
    drop(tx1);
    let res1 = manager.transcribe_stream(&cfg_de, rx1, None).await.unwrap();
    assert_eq!(res1.transcript, "Warm OK");
    assert!(res1.used_prewarmed_connection);
    assert!(!manager.has_ready_connection().await);

    // 3. Prewarm for `de` (conn #1), then transcribe with `en` -> discard & open fresh (conn #2).
    manager.warm_up(&cfg_de).await.unwrap();
    let cfg_en = DeepgramStreamConfig::default()
        .with_language("en")
        .with_timeouts(fast_warm_timeouts());
    let (tx2, rx2) = mpsc::channel(2);
    tx2.send(Bytes::from_static(&[5, 6])).await.unwrap();
    drop(tx2);
    let res2 = manager.transcribe_stream(&cfg_en, rx2, None).await.unwrap();
    assert_eq!(res2.transcript, "Warm OK");
    assert!(!res2.used_prewarmed_connection);

    manager.invalidate().await;
    server.await.unwrap();
    assert_eq!(*accepted.lock().await, 3);
}

#[tokio::test]
async fn warm_manager_detects_server_close_and_falls_back_to_fresh_socket() {
    let (ws_url, accepted, server) = spawn_multi_ws_server(2, |idx, mut ws| async move {
        if idx == 0 {
            let _ = ws.close(None).await;
            return;
        }
        while let Some(Ok(msg)) = ws.next().await {
            if let Message::Text(text) = msg {
                if text.contains("Finalize") {
                    let _ = ws
                        .send(Message::text(final_results_json("Recovered")))
                        .await;
                } else if text.contains("CloseStream") {
                    let _ = ws.close(None).await;
                    break;
                }
            }
        }
    })
    .await;

    let client = DeepgramStreamClient::with_ws_url(&ws_url, "dg-key").unwrap();
    let manager = DeepgramWarmConnectionManager::new(client);
    let cfg = DeepgramStreamConfig::default().with_timeouts(fast_warm_timeouts());

    manager.warm_up(&cfg).await.unwrap();
    let deadline = Instant::now() + Duration::from_secs(1);
    while manager.has_ready_connection().await && Instant::now() < deadline {
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    assert!(!manager.has_ready_connection().await);

    let (tx, rx) = mpsc::channel(2);
    tx.send(Bytes::from_static(&[1, 2, 3, 4])).await.unwrap();
    drop(tx);

    let res = manager.transcribe_stream(&cfg, rx, None).await.unwrap();
    server.await.unwrap();

    assert_eq!(res.transcript, "Recovered");
    assert!(!res.used_prewarmed_connection);
    assert_eq!(*accepted.lock().await, 2);
}
