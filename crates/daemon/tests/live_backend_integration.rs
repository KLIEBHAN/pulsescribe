//! Integration tests for [`LiveDictationBackend`] wired into [`DaemonRuntime`].
//!
//! Uses deterministic synthetic PCM audio sources and local loopback mock HTTP/WebSocket servers
//! so the full audio -> resample -> provider -> LLM refinement -> delivery pipeline runs without
//! microphone hardware, API keys, or external network access.

use std::collections::VecDeque;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use futures_util::{SinkExt, StreamExt};
use pulsescribe_audio::AudioError;
use pulsescribe_core::{
    AppState, DaemonErrorCode, DaemonStatusError, HistoryStore, PreferencesStore,
    TranscriptClipboardBackend,
};
use pulsescribe_daemon::{
    AudioSource, AudioSourceFactory, DaemonConfig, DaemonEvent, DaemonMode, DaemonPlatform,
    DaemonRuntime, DaemonRuntimeOptions, LiveDictationBackend, LocalTranscriber,
    LocalTranscriptionRequest, NoopHotkeyBackend, ProviderEndpoints,
};
use pulsescribe_platform::{
    AppDetector, MicrophonePermissionStatus, NoopSoundPlayer, PermissionsChecker, PlatformKind,
};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::sync::broadcast;
use tokio_tungstenite::accept_async;
use tokio_tungstenite::tungstenite::Message;

static TEST_COUNTER: AtomicU64 = AtomicU64::new(0);

struct TempDir {
    path: PathBuf,
}

impl TempDir {
    fn new(tag: &str) -> Self {
        let seq = TEST_COUNTER.fetch_add(1, Ordering::Relaxed);
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "pulsescribe-live-e2e-{tag}-{}-{nanos}-{seq}",
            std::process::id()
        ));
        fs::create_dir_all(&path).unwrap();
        Self { path }
    }

    fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

type SharedClipboard = Arc<Mutex<dyn TranscriptClipboardBackend + Send>>;

struct RecordingClipboard {
    pasted: Arc<Mutex<Vec<String>>>,
}

impl RecordingClipboard {
    fn create() -> (Arc<Mutex<Vec<String>>>, SharedClipboard) {
        let pasted = Arc::new(Mutex::new(Vec::new()));
        let backend: SharedClipboard = Arc::new(Mutex::new(Self {
            pasted: Arc::clone(&pasted),
        }));
        (pasted, backend)
    }
}

impl TranscriptClipboardBackend for RecordingClipboard {
    fn paste_transcript(&mut self, text: &str) -> bool {
        self.pasted.lock().unwrap().push(text.to_owned());
        true
    }

    fn copy_transcript(&mut self, _text: &str) -> bool {
        true
    }
}

struct StaticAppDetector {
    app_id: Option<String>,
    app_label: Option<String>,
}

impl AppDetector for StaticAppDetector {
    fn frontmost_app(&self) -> Option<String> {
        self.app_id.clone()
    }

    fn frontmost_app_label(&self) -> Option<String> {
        self.app_label.clone()
    }
}

struct StaticPermissions {
    mic: MicrophonePermissionStatus,
}

impl PermissionsChecker for StaticPermissions {
    fn microphone_status(&self) -> MicrophonePermissionStatus {
        self.mic
    }

    fn has_accessibility(&self) -> bool {
        true
    }

    fn has_input_monitoring(&self) -> bool {
        true
    }
}

struct SyntheticAudioSource {
    sample_rate: u32,
    chunks: VecDeque<Vec<f32>>,
    last_rms: f32,
    dropped_counter: Arc<AtomicUsize>,
}

impl AudioSource for SyntheticAudioSource {
    fn sample_rate(&self) -> u32 {
        self.sample_rate
    }

    fn drain_into(&mut self, out: &mut Vec<f32>) -> usize {
        match self.chunks.pop_front() {
            Some(chunk) => {
                let len = chunk.len();
                self.last_rms = 0.25;
                out.extend_from_slice(&chunk);
                len
            }
            None => 0,
        }
    }

    fn latest_rms(&self) -> f32 {
        self.last_rms
    }

    fn take_error(&self) -> Option<String> {
        None
    }
}

impl Drop for SyntheticAudioSource {
    fn drop(&mut self) {
        self.dropped_counter.fetch_add(1, Ordering::SeqCst);
    }
}

struct SyntheticAudioFactory {
    sample_rate: u32,
    samples_per_chunk: usize,
    chunk_count: usize,
    opened: Arc<AtomicUsize>,
    dropped: Arc<AtomicUsize>,
}

impl SyntheticAudioFactory {
    fn new(sample_rate: u32, samples_per_chunk: usize, chunk_count: usize) -> Self {
        Self {
            sample_rate,
            samples_per_chunk,
            chunk_count,
            opened: Arc::new(AtomicUsize::new(0)),
            dropped: Arc::new(AtomicUsize::new(0)),
        }
    }
}

impl AudioSourceFactory for SyntheticAudioFactory {
    fn open(&self, _device_name: Option<&str>) -> Result<Box<dyn AudioSource>, AudioError> {
        self.opened.fetch_add(1, Ordering::SeqCst);
        let mut chunks = VecDeque::with_capacity(self.chunk_count);
        for _ in 0..self.chunk_count {
            chunks.push_back(vec![0.25_f32; self.samples_per_chunk]);
        }
        Ok(Box::new(SyntheticAudioSource {
            sample_rate: self.sample_rate,
            chunks,
            last_rms: 0.0,
            dropped_counter: Arc::clone(&self.dropped),
        }))
    }
}

struct MockLocalEngine {
    seen_sample_counts: Arc<Mutex<Vec<usize>>>,
    response: String,
}

impl LocalTranscriber for MockLocalEngine {
    fn transcribe(&self, request: LocalTranscriptionRequest) -> Result<String, DaemonStatusError> {
        self.seen_sample_counts
            .lock()
            .unwrap()
            .push(request.samples_16k.len());
        Ok(self.response.clone())
    }
}

async fn next_matching_event<F>(
    rx: &mut broadcast::Receiver<DaemonEvent>,
    predicate: F,
) -> DaemonEvent
where
    F: Fn(&DaemonEvent) -> bool,
{
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            match rx.recv().await {
                Ok(ev) if predicate(&ev) => return ev,
                Ok(_) => continue,
                Err(broadcast::error::RecvError::Lagged(_)) => continue,
                Err(err) => panic!("event bus closed unexpectedly: {err}"),
            }
        }
    })
    .await
    .expect("timed out waiting for expected daemon event")
}

/// Minimal single-request HTTP/1.1 mock responder returning a fixed JSON body and capturing the
/// raw HTTP request string.
async fn spawn_http_mock(
    response_body: &'static str,
) -> (String, tokio::sync::oneshot::Receiver<String>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let (req_tx, req_rx) = tokio::sync::oneshot::channel();

    tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let mut buf = Vec::new();
        let mut temp = [0_u8; 4096];
        let mut expected_total: Option<usize> = None;

        loop {
            let n = socket.read(&mut temp).await.unwrap();
            if n == 0 {
                break;
            }
            buf.extend_from_slice(&temp[..n]);
            if expected_total.is_none() {
                if let Some(pos) = find_header_end(&buf) {
                    let headers = String::from_utf8_lossy(&buf[..pos]);
                    let content_len = headers
                        .lines()
                        .find_map(|line| {
                            let (k, v) = line.split_once(':')?;
                            k.eq_ignore_ascii_case("content-length")
                                .then(|| v.trim().parse::<usize>().ok())
                                .flatten()
                        })
                        .unwrap_or(0);
                    expected_total = Some(pos + 4 + content_len);
                }
            }
            if let Some(total) = expected_total {
                if buf.len() >= total {
                    break;
                }
            }
        }

        let _ = req_tx.send(String::from_utf8_lossy(&buf).into_owned());

        let reply = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            response_body.len(),
            response_body
        );
        let _ = socket.write_all(reply.as_bytes()).await;
        let _ = socket.shutdown().await;
    });

    (format!("http://{addr}/v1"), req_rx)
}

fn find_header_end(buf: &[u8]) -> Option<usize> {
    buf.windows(4).position(|w| w == b"\r\n\r\n")
}

#[tokio::test]
async fn batch_openai_plus_groq_refinement_routes_email_context_and_delivers() {
    let dir = TempDir::new("openai-refine");
    let store = Arc::new(PreferencesStore::new(dir.path()));

    let (openai_url, openai_req_rx) = spawn_http_mock(r#"{"text":"ähm hallo welt"}"#).await;
    let (refine_url, refine_req_rx) =
        spawn_http_mock(r#"{"choices":[{"message":{"content":"Hallo Welt."}}]}"#).await;

    let (pasted, clipboard) = RecordingClipboard::create();
    let permissions = Arc::new(StaticPermissions {
        mic: MicrophonePermissionStatus::Authorized,
    });
    let platform = DaemonPlatform {
        sound: Arc::new(NoopSoundPlayer),
        clipboard,
        app_detector: Arc::new(StaticAppDetector {
            app_id: Some("Mail".to_owned()),
            app_label: Some("Apple Mail".to_owned()),
        }),
        permissions: Arc::clone(&permissions) as Arc<dyn PermissionsChecker>,
        hotkeys: Arc::new(NoopHotkeyBackend),
        platform_kind: PlatformKind::current(),
    };

    let audio_factory = Arc::new(SyntheticAudioFactory::new(48_000, 2_880, 1));
    let endpoints = ProviderEndpoints {
        openai_base_url: Some(openai_url),
        refine_base_url: Some(refine_url),
        ..ProviderEndpoints::default()
    };
    let backend = LiveDictationBackend::with_components(
        dir.path(),
        permissions,
        Arc::clone(&audio_factory) as Arc<dyn AudioSourceFactory>,
        None,
        endpoints,
    )
    .unwrap();

    let config = DaemonConfig {
        mode: DaemonMode::OpenAi,
        streaming: false,
        refine: true,
        refine_provider: Some("groq".to_owned()),
        ..DaemonConfig::default()
    }
    .with_provider_env("OPENAI_API_KEY", "sk-mock-openai")
    .with_provider_env("GROQ_API_KEY", "gsk-mock-groq");

    let runtime = DaemonRuntime::spawn(
        store,
        config,
        platform,
        Arc::new(backend.clone()),
        DaemonRuntimeOptions {
            enable_ipc: false,
            enable_reload_watcher: false,
            ..DaemonRuntimeOptions::default()
        },
    )
    .await;
    let handle = runtime.handle();
    let mut events = handle.subscribe();

    handle.start_recording();
    next_matching_event(&mut events, |e| {
        matches!(
            e,
            DaemonEvent::StateChanged {
                state: AppState::Recording,
                ..
            }
        )
    })
    .await;

    handle.stop_recording();

    next_matching_event(&mut events, |e| {
        matches!(
            e,
            DaemonEvent::StateChanged {
                state: AppState::Refining,
                ..
            }
        )
    })
    .await;

    let delivered = next_matching_event(&mut events, |e| {
        matches!(e, DaemonEvent::TranscriptDelivered { .. })
    })
    .await;
    match delivered {
        DaemonEvent::TranscriptDelivered {
            text, target_app, ..
        } => {
            assert_eq!(text, "Hallo Welt.");
            assert_eq!(target_app.as_deref(), Some("Apple Mail"));
        }
        other => panic!("unexpected event: {other:?}"),
    }

    let openai_req = openai_req_rx.await.unwrap();
    assert!(openai_req.starts_with("POST /v1/audio/transcriptions HTTP/1.1"));
    assert!(openai_req.contains("RIFF"));

    // Verify `Mail` frontmost app triggered the email refinement prompt.
    let refine_req = refine_req_rx.await.unwrap();
    assert!(refine_req.starts_with("POST /v1/chat/completions HTTP/1.1"));
    assert!(refine_req.contains("E-Mail"));
    assert!(refine_req.contains("hallo welt"));

    assert_eq!(pasted.lock().unwrap().as_slice(), &["Hallo Welt."]);
    assert_eq!(audio_factory.dropped.load(Ordering::SeqCst), 1);

    runtime.shutdown().await;
    backend.shutdown().await;

    let recent = HistoryStore::new(dir.path()).get_recent_transcripts(5);
    assert_eq!(recent.len(), 1);
    assert_eq!(recent[0].text, "Hallo Welt.");
    assert_eq!(recent[0].app.as_deref(), Some("Mail"));
}

#[tokio::test]
async fn deepgram_streaming_forwards_interims_handles_finalize_and_reuses_warm_socket() {
    let dir = TempDir::new("deepgram-stream");
    let store = Arc::new(PreferencesStore::new(dir.path()));

    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let ws_url = format!("ws://{addr}/v1/listen");
    let connections = Arc::new(AtomicUsize::new(0));
    let conn_counter = Arc::clone(&connections);

    let server_task = tokio::spawn(async move {
        // Handle up to 3 connections: initial warm-up, re-warm for run 2, and post-run-2 re-warm.
        for turn in 0..3 {
            let Ok((stream, _)) = listener.accept().await else {
                break;
            };
            conn_counter.fetch_add(1, Ordering::SeqCst);
            let mut ws = accept_async(stream).await.unwrap();

            let mut saw_binary = false;
            while let Some(Ok(msg)) = ws.next().await {
                match msg {
                    Message::Binary(bytes) if !bytes.is_empty() => {
                        if !saw_binary {
                            saw_binary = true;
                            let interim = serde_json::json!({
                                "type": "Results",
                                "is_final": false,
                                "from_finalize": false,
                                "channel": { "alternatives": [{ "transcript": format!("teil {turn}") }] }
                            });
                            let _ = ws.send(Message::Text(interim.to_string().into())).await;
                        }
                    }
                    Message::Text(txt) => {
                        if txt.contains("Finalize") {
                            let final_msg = serde_json::json!({
                                "type": "Results",
                                "is_final": true,
                                "from_finalize": true,
                                "channel": { "alternatives": [{ "transcript": format!("fertig {turn}") }] }
                            });
                            let _ = ws.send(Message::Text(final_msg.to_string().into())).await;
                        } else if txt.contains("CloseStream") {
                            let _ = ws.close(None).await;
                            break;
                        }
                    }
                    Message::Close(_) => break,
                    _ => {}
                }
            }
        }
    });

    let (pasted, clipboard) = RecordingClipboard::create();
    let permissions = Arc::new(StaticPermissions {
        mic: MicrophonePermissionStatus::Authorized,
    });
    let platform = DaemonPlatform {
        sound: Arc::new(NoopSoundPlayer),
        clipboard,
        app_detector: Arc::new(StaticAppDetector {
            app_id: None,
            app_label: None,
        }),
        permissions: Arc::clone(&permissions) as Arc<dyn PermissionsChecker>,
        hotkeys: Arc::new(NoopHotkeyBackend),
        platform_kind: PlatformKind::current(),
    };

    let audio_factory = Arc::new(SyntheticAudioFactory::new(16_000, 480, 6));
    let endpoints = ProviderEndpoints {
        deepgram_ws_url: Some(ws_url),
        ..ProviderEndpoints::default()
    };
    let backend = LiveDictationBackend::with_components(
        dir.path(),
        permissions,
        Arc::clone(&audio_factory) as Arc<dyn AudioSourceFactory>,
        None,
        endpoints,
    )
    .unwrap();

    let mut config = DaemonConfig {
        mode: DaemonMode::Deepgram,
        streaming: true,
        deepgram_warm_websocket: true,
        ..DaemonConfig::default()
    }
    .with_provider_env("DEEPGRAM_API_KEY", "dg-mock-key")
    .with_provider_env("PULSESCRIBE_DEEPGRAM_TAIL_PADDING_SECONDS", "0");
    config.timings.done_display_hold = Duration::from_millis(50);

    // Prewarm before first dictation -> connection #1.
    backend.warm_up_deepgram(&config).await.unwrap();
    assert_eq!(connections.load(Ordering::SeqCst), 1);

    let runtime = DaemonRuntime::spawn(
        store,
        config,
        platform,
        Arc::new(backend.clone()),
        DaemonRuntimeOptions {
            enable_ipc: false,
            enable_reload_watcher: false,
            ..DaemonRuntimeOptions::default()
        },
    )
    .await;
    let handle = runtime.handle();
    let mut events = handle.subscribe();

    handle.start_recording();
    next_matching_event(
        &mut events,
        |e| matches!(e, DaemonEvent::InterimText { text, .. } if text == "teil 0"),
    )
    .await;

    handle.stop_recording();
    next_matching_event(
        &mut events,
        |e| matches!(e, DaemonEvent::TranscriptDelivered { text, .. } if text == "fertig 0"),
    )
    .await;

    // Wait for Done -> Idle before running second dictation (uses background re-warmed connection #2).
    next_matching_event(&mut events, |e| {
        matches!(
            e,
            DaemonEvent::StateChanged {
                state: AppState::Idle,
                ..
            }
        )
    })
    .await;

    handle.start_recording();
    next_matching_event(
        &mut events,
        |e| matches!(e, DaemonEvent::InterimText { text, .. } if text == "teil 1"),
    )
    .await;
    handle.stop_recording();
    next_matching_event(
        &mut events,
        |e| matches!(e, DaemonEvent::TranscriptDelivered { text, .. } if text == "fertig 1"),
    )
    .await;

    assert_eq!(pasted.lock().unwrap().as_slice(), &["fertig 0", "fertig 1"]);

    runtime.shutdown().await;
    backend.shutdown().await;
    server_task.abort();
}

#[tokio::test]
async fn local_transcriber_missing_key_mic_permission_and_cancel_paths() {
    let dir = TempDir::new("local-and-errors");

    // 1. Missing API key fast-fails BEFORE opening the microphone.
    {
        let store = Arc::new(PreferencesStore::new(dir.path()));
        let (_, clipboard) = RecordingClipboard::create();
        let audio_factory = Arc::new(SyntheticAudioFactory::new(16_000, 480, 4));
        let permissions = Arc::new(StaticPermissions {
            mic: MicrophonePermissionStatus::Authorized,
        });
        let platform = DaemonPlatform {
            permissions: Arc::clone(&permissions) as Arc<dyn PermissionsChecker>,
            ..DaemonPlatform::noop(clipboard)
        };
        let backend = LiveDictationBackend::with_components(
            dir.path(),
            permissions,
            Arc::clone(&audio_factory) as Arc<dyn AudioSourceFactory>,
            None,
            ProviderEndpoints::default(),
        )
        .unwrap();
        let runtime = DaemonRuntime::spawn(
            store,
            DaemonConfig {
                mode: DaemonMode::OpenAi,
                streaming: false,
                ..DaemonConfig::default()
            },
            platform,
            Arc::new(backend.clone()),
            DaemonRuntimeOptions {
                enable_ipc: false,
                enable_reload_watcher: false,
                ..DaemonRuntimeOptions::default()
            },
        )
        .await;
        let handle = runtime.handle();
        let mut events = handle.subscribe();

        handle.start_recording();
        let err_ev = next_matching_event(&mut events, |e| {
            matches!(
                e,
                DaemonEvent::StateChanged {
                    state: AppState::Error,
                    ..
                }
            )
        })
        .await;
        match err_ev {
            DaemonEvent::StateChanged { error, .. } => {
                assert_eq!(error.unwrap().code, DaemonErrorCode::MissingApiKey);
            }
            other => panic!("unexpected event: {other:?}"),
        }
        assert_eq!(audio_factory.opened.load(Ordering::SeqCst), 0);

        runtime.shutdown().await;
        backend.shutdown().await;
    }

    // 2. Denied microphone permission fails cleanly without opening audio device.
    {
        let store = Arc::new(PreferencesStore::new(dir.path()));
        let (_, clipboard) = RecordingClipboard::create();
        let audio_factory = Arc::new(SyntheticAudioFactory::new(16_000, 480, 4));
        let permissions = Arc::new(StaticPermissions {
            mic: MicrophonePermissionStatus::Denied,
        });
        let platform = DaemonPlatform {
            permissions: Arc::clone(&permissions) as Arc<dyn PermissionsChecker>,
            ..DaemonPlatform::noop(clipboard)
        };
        let backend = LiveDictationBackend::with_components(
            dir.path(),
            permissions,
            Arc::clone(&audio_factory) as Arc<dyn AudioSourceFactory>,
            Some(Arc::new(MockLocalEngine {
                seen_sample_counts: Arc::new(Mutex::new(Vec::new())),
                response: "unused".to_owned(),
            })),
            ProviderEndpoints::default(),
        )
        .unwrap();
        let runtime = DaemonRuntime::spawn(
            store,
            DaemonConfig {
                mode: DaemonMode::Local,
                streaming: false,
                ..DaemonConfig::default()
            },
            platform,
            Arc::new(backend.clone()),
            DaemonRuntimeOptions {
                enable_ipc: false,
                enable_reload_watcher: false,
                ..DaemonRuntimeOptions::default()
            },
        )
        .await;
        let handle = runtime.handle();
        let mut events = handle.subscribe();

        handle.start_recording();
        let err_ev = next_matching_event(&mut events, |e| {
            matches!(
                e,
                DaemonEvent::StateChanged {
                    state: AppState::Error,
                    ..
                }
            )
        })
        .await;
        match err_ev {
            DaemonEvent::StateChanged { error, .. } => {
                assert_eq!(error.unwrap().code, DaemonErrorCode::MicrophonePermission);
            }
            other => panic!("unexpected event: {other:?}"),
        }
        assert_eq!(audio_factory.opened.load(Ordering::SeqCst), 0);

        runtime.shutdown().await;
        backend.shutdown().await;
    }

    // 3. Local mode resamples 48 kHz -> 16 kHz and delegates to LocalTranscriber, plus mid-capture cancel drops mic.
    {
        let store = Arc::new(PreferencesStore::new(dir.path()));
        let (_, clipboard) = RecordingClipboard::create();
        let audio_factory = Arc::new(SyntheticAudioFactory::new(48_000, 2_880, 1));
        let seen_counts = Arc::new(Mutex::new(Vec::new()));
        let permissions = Arc::new(StaticPermissions {
            mic: MicrophonePermissionStatus::Authorized,
        });
        let platform = DaemonPlatform {
            permissions: Arc::clone(&permissions) as Arc<dyn PermissionsChecker>,
            ..DaemonPlatform::noop(clipboard)
        };
        let backend = LiveDictationBackend::with_components(
            dir.path(),
            permissions,
            Arc::clone(&audio_factory) as Arc<dyn AudioSourceFactory>,
            Some(Arc::new(MockLocalEngine {
                seen_sample_counts: Arc::clone(&seen_counts),
                response: "Lokales Transkript".to_owned(),
            })),
            ProviderEndpoints::default(),
        )
        .unwrap();
        let mut config = DaemonConfig {
            mode: DaemonMode::Local,
            streaming: false,
            ..DaemonConfig::default()
        };
        config.timings.done_display_hold = Duration::from_millis(50);

        let runtime = DaemonRuntime::spawn(
            store,
            config,
            platform,
            Arc::new(backend.clone()),
            DaemonRuntimeOptions {
                enable_ipc: false,
                enable_reload_watcher: false,
                ..DaemonRuntimeOptions::default()
            },
        )
        .await;
        let handle = runtime.handle();
        let mut events = handle.subscribe();

        handle.start_recording();
        next_matching_event(&mut events, |e| {
            matches!(
                e,
                DaemonEvent::StateChanged {
                    state: AppState::Recording,
                    ..
                }
            )
        })
        .await;
        handle.stop_recording();
        next_matching_event(&mut events, |e| {
            matches!(
                e,
                DaemonEvent::TranscriptDelivered { text, .. }
                if text == "Lokales Transkript"
            )
        })
        .await;
        // 6 chunks * 480 samples at 48 kHz = 2880 samples -> ~960 samples at 16 kHz.
        let counts = seen_counts.lock().unwrap().clone();
        assert_eq!(counts.len(), 1);
        assert!(
            (900..=1000).contains(&counts[0]),
            "expected ~960 resampled 16 kHz samples, got {}",
            counts[0]
        );

        next_matching_event(&mut events, |e| {
            matches!(
                e,
                DaemonEvent::StateChanged {
                    state: AppState::Idle,
                    ..
                }
            )
        })
        .await;

        // Mid-capture cancel drops audio source without calling LocalTranscriber.
        handle.start_recording();
        next_matching_event(&mut events, |e| {
            matches!(
                e,
                DaemonEvent::StateChanged {
                    state: AppState::Recording,
                    ..
                }
            )
        })
        .await;
        handle.cancel();
        next_matching_event(&mut events, |e| {
            matches!(
                e,
                DaemonEvent::StateChanged {
                    state: AppState::Idle,
                    ..
                }
            )
        })
        .await;
        assert_eq!(seen_counts.lock().unwrap().len(), 1);

        runtime.shutdown().await;
        backend.shutdown().await;
        assert_eq!(audio_factory.dropped.load(Ordering::SeqCst), 2);
    }
}

struct SilentAudioSource;

impl AudioSource for SilentAudioSource {
    fn sample_rate(&self) -> u32 {
        16_000
    }

    fn drain_into(&mut self, out: &mut Vec<f32>) -> usize {
        out.extend_from_slice(&[0.0_f32; 320]);
        320
    }

    fn latest_rms(&self) -> f32 {
        0.0
    }

    fn take_error(&self) -> Option<String> {
        None
    }
}

struct SilentAudioFactory;

impl AudioSourceFactory for SilentAudioFactory {
    fn open(&self, _device_name: Option<&str>) -> Result<Box<dyn AudioSource>, AudioError> {
        Ok(Box::new(SilentAudioSource))
    }
}

struct BlockingLocalEngine {
    started: Arc<std::sync::atomic::AtomicBool>,
    observed_cancel: Arc<std::sync::atomic::AtomicBool>,
    finished: Arc<std::sync::atomic::AtomicBool>,
}

impl LocalTranscriber for BlockingLocalEngine {
    fn transcribe(&self, request: LocalTranscriptionRequest) -> Result<String, DaemonStatusError> {
        self.started.store(true, Ordering::SeqCst);
        let deadline = std::time::Instant::now() + Duration::from_secs(3);
        while std::time::Instant::now() < deadline {
            if request.cancel_flag.load(Ordering::Acquire) {
                self.observed_cancel.store(true, Ordering::SeqCst);
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        self.finished.store(true, Ordering::SeqCst);
        Ok(String::new())
    }
}

struct ContinuousAudioSource {
    dropped_counter: Arc<AtomicUsize>,
}

impl AudioSource for ContinuousAudioSource {
    fn sample_rate(&self) -> u32 {
        16_000
    }

    fn drain_into(&mut self, out: &mut Vec<f32>) -> usize {
        out.extend_from_slice(&[0.25_f32; 320]);
        320
    }

    fn latest_rms(&self) -> f32 {
        0.25
    }

    fn take_error(&self) -> Option<String> {
        None
    }
}

impl Drop for ContinuousAudioSource {
    fn drop(&mut self) {
        self.dropped_counter.fetch_add(1, Ordering::SeqCst);
    }
}

struct ContinuousAudioFactory {
    dropped: Arc<AtomicUsize>,
}

impl ContinuousAudioFactory {
    fn new() -> Self {
        Self {
            dropped: Arc::new(AtomicUsize::new(0)),
        }
    }
}

impl AudioSourceFactory for ContinuousAudioFactory {
    fn open(&self, _device_name: Option<&str>) -> Result<Box<dyn AudioSource>, AudioError> {
        Ok(Box::new(ContinuousAudioSource {
            dropped_counter: Arc::clone(&self.dropped),
        }))
    }
}

#[tokio::test]
async fn premature_deepgram_close_silent_batch_and_local_cancel_drain() {
    let dir = TempDir::new("edge-cases");

    // 1. Clean Deepgram WebSocket close before user stops surfaces `Connection` error instead of wedging,
    // even with a continuous live mic where `capture_handle` observes closed `audio_tx` first.
    {
        let store = Arc::new(PreferencesStore::new(dir.path()));
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let ws_url = format!("ws://{addr}/v1/listen");

        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut ws = accept_async(stream).await.unwrap();
            if let Some(Ok(Message::Binary(_))) = ws.next().await {
                let _ = ws.close(None).await;
            }
        });

        let (_, clipboard) = RecordingClipboard::create();
        let audio_factory = Arc::new(ContinuousAudioFactory::new());
        let permissions = Arc::new(StaticPermissions {
            mic: MicrophonePermissionStatus::Authorized,
        });
        let platform = DaemonPlatform {
            permissions: Arc::clone(&permissions) as Arc<dyn PermissionsChecker>,
            ..DaemonPlatform::noop(clipboard)
        };
        let backend = LiveDictationBackend::with_components(
            dir.path(),
            permissions,
            Arc::clone(&audio_factory) as Arc<dyn AudioSourceFactory>,
            None,
            ProviderEndpoints {
                deepgram_ws_url: Some(ws_url),
                ..ProviderEndpoints::default()
            },
        )
        .unwrap();
        let config = DaemonConfig {
            mode: DaemonMode::Deepgram,
            streaming: true,
            deepgram_warm_websocket: false,
            ..DaemonConfig::default()
        }
        .with_provider_env("DEEPGRAM_API_KEY", "dg-mock");

        let runtime = DaemonRuntime::spawn(
            store,
            config,
            platform,
            Arc::new(backend.clone()),
            DaemonRuntimeOptions {
                enable_ipc: false,
                enable_reload_watcher: false,
                ..DaemonRuntimeOptions::default()
            },
        )
        .await;
        let handle = runtime.handle();
        let mut events = handle.subscribe();

        handle.start_recording();
        let err_ev = next_matching_event(&mut events, |e| {
            matches!(
                e,
                DaemonEvent::StateChanged {
                    state: AppState::Error,
                    ..
                }
            )
        })
        .await;
        match err_ev {
            DaemonEvent::StateChanged { error, .. } => {
                assert_eq!(error.unwrap().code, DaemonErrorCode::Connection);
            }
            other => panic!("unexpected event: {other:?}"),
        }

        runtime.shutdown().await;
        backend.shutdown().await;
        assert_eq!(audio_factory.dropped.load(Ordering::SeqCst), 1);
        server.abort();
    }

    // 2. Pure silence in batch mode transitions to `NoSpeech` without invoking local ASR.
    {
        let store = Arc::new(PreferencesStore::new(dir.path()));
        let (_, clipboard) = RecordingClipboard::create();
        let seen_counts = Arc::new(Mutex::new(Vec::new()));
        let permissions = Arc::new(StaticPermissions {
            mic: MicrophonePermissionStatus::Authorized,
        });
        let platform = DaemonPlatform {
            permissions: Arc::clone(&permissions) as Arc<dyn PermissionsChecker>,
            ..DaemonPlatform::noop(clipboard)
        };
        let backend = LiveDictationBackend::with_components(
            dir.path(),
            permissions,
            Arc::new(SilentAudioFactory),
            Some(Arc::new(MockLocalEngine {
                seen_sample_counts: Arc::clone(&seen_counts),
                response: "should not be called".to_owned(),
            })),
            ProviderEndpoints::default(),
        )
        .unwrap();
        let runtime = DaemonRuntime::spawn(
            store,
            DaemonConfig {
                mode: DaemonMode::Local,
                streaming: false,
                ..DaemonConfig::default()
            },
            platform,
            Arc::new(backend.clone()),
            DaemonRuntimeOptions {
                enable_ipc: false,
                enable_reload_watcher: false,
                ..DaemonRuntimeOptions::default()
            },
        )
        .await;
        let handle = runtime.handle();
        let mut events = handle.subscribe();

        handle.start_recording();
        next_matching_event(&mut events, |e| {
            matches!(
                e,
                DaemonEvent::StateChanged {
                    state: AppState::Listening,
                    ..
                }
            )
        })
        .await;
        handle.stop_recording();
        next_matching_event(&mut events, |e| {
            matches!(
                e,
                DaemonEvent::StateChanged {
                    state: AppState::NoSpeech,
                    ..
                }
            )
        })
        .await;
        assert!(seen_counts.lock().unwrap().is_empty());

        runtime.shutdown().await;
        backend.shutdown().await;
    }

    // 3. Cancellation mid-local-inference sets `cancel_flag` and `backend.shutdown()` awaits teardown.
    {
        let store = Arc::new(PreferencesStore::new(dir.path()));
        let (_, clipboard) = RecordingClipboard::create();
        let started = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let observed_cancel = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let finished = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let permissions = Arc::new(StaticPermissions {
            mic: MicrophonePermissionStatus::Authorized,
        });
        let platform = DaemonPlatform {
            permissions: Arc::clone(&permissions) as Arc<dyn PermissionsChecker>,
            ..DaemonPlatform::noop(clipboard)
        };
        let backend = LiveDictationBackend::with_components(
            dir.path(),
            permissions,
            Arc::new(SyntheticAudioFactory::new(16_000, 960, 1)),
            Some(Arc::new(BlockingLocalEngine {
                started: Arc::clone(&started),
                observed_cancel: Arc::clone(&observed_cancel),
                finished: Arc::clone(&finished),
            })),
            ProviderEndpoints::default(),
        )
        .unwrap();
        let runtime = DaemonRuntime::spawn(
            store,
            DaemonConfig {
                mode: DaemonMode::Local,
                streaming: false,
                ..DaemonConfig::default()
            },
            platform,
            Arc::new(backend.clone()),
            DaemonRuntimeOptions {
                enable_ipc: false,
                enable_reload_watcher: false,
                ..DaemonRuntimeOptions::default()
            },
        )
        .await;
        let handle = runtime.handle();
        let mut events = handle.subscribe();

        handle.start_recording();
        next_matching_event(&mut events, |e| {
            matches!(
                e,
                DaemonEvent::StateChanged {
                    state: AppState::Recording,
                    ..
                }
            )
        })
        .await;
        handle.stop_recording();
        next_matching_event(&mut events, |e| {
            matches!(
                e,
                DaemonEvent::StateChanged {
                    state: AppState::Transcribing,
                    ..
                }
            )
        })
        .await;
        // Wait until the blocking local engine has entered `transcribe`.
        tokio::time::timeout(Duration::from_secs(2), async {
            while !started.load(Ordering::SeqCst) {
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .unwrap();

        handle.cancel();
        next_matching_event(&mut events, |e| {
            matches!(
                e,
                DaemonEvent::StateChanged {
                    state: AppState::Idle,
                    ..
                }
            )
        })
        .await;

        runtime.shutdown().await;
        backend.shutdown().await;
        assert!(observed_cancel.load(Ordering::SeqCst));
        assert!(finished.load(Ordering::SeqCst));
    }
}
