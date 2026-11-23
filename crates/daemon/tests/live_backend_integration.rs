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
    DaemonRuntime, DaemonRuntimeOptions, LiveDictationBackend, LocalPreloadObserver,
    LocalTranscriber, LocalTranscriptionRequest, NoopHotkeyBackend, ProviderEndpoints,
};
use pulsescribe_local_asr::{
    resolve_model_spec, DownloadProgress, LocalAsrError, LocalStreamingSession, ModelCache,
    ModelDownloader, ModelSpec,
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

type PreloadRecordList = Arc<Mutex<Vec<(Option<String>, Option<String>)>>>;

struct TrackingPreloadEngine {
    preloads: PreloadRecordList,
    unloads: Arc<AtomicUsize>,
    preview_unloads: Arc<AtomicUsize>,
    block_model: Option<String>,
    block_release: Option<Arc<std::sync::atomic::AtomicBool>>,
    blocked_started: Arc<std::sync::atomic::AtomicBool>,
    blocked_observed_cancel: Arc<std::sync::atomic::AtomicBool>,
    blocked_finished: Arc<std::sync::atomic::AtomicBool>,
}

impl LocalTranscriber for TrackingPreloadEngine {
    fn transcribe(&self, _request: LocalTranscriptionRequest) -> Result<String, DaemonStatusError> {
        Ok("ok".to_owned())
    }

    fn preload(
        &self,
        model: Option<&str>,
        language: Option<&str>,
        cancel_flag: &std::sync::atomic::AtomicBool,
    ) -> Result<bool, DaemonStatusError> {
        self.preloads.lock().unwrap().push((
            model.map(ToOwned::to_owned),
            language.map(ToOwned::to_owned),
        ));
        if self.block_model.as_deref() == model {
            self.blocked_started.store(true, Ordering::SeqCst);
            let deadline = std::time::Instant::now() + Duration::from_secs(3);
            while std::time::Instant::now() < deadline {
                let cancelled = cancel_flag.load(Ordering::Acquire);
                let released = self
                    .block_release
                    .as_ref()
                    .is_none_or(|gate| gate.load(Ordering::Acquire));
                if cancelled && released {
                    break;
                }
                std::thread::sleep(Duration::from_millis(5));
            }
            self.blocked_observed_cancel
                .store(cancel_flag.load(Ordering::Acquire), Ordering::SeqCst);
            self.blocked_finished.store(true, Ordering::SeqCst);
            return Ok(false);
        }
        Ok(true)
    }

    fn unload(&self) {
        self.unloads.fetch_add(1, Ordering::SeqCst);
    }

    fn unload_streaming_preview(&self) {
        self.preview_unloads.fetch_add(1, Ordering::SeqCst);
    }
}

#[tokio::test]
async fn local_preload_coalesces_cancels_on_switch_and_unloads_on_cloud_mode() {
    let dir = TempDir::new("local-preload");
    let preloads = Arc::new(Mutex::new(Vec::new()));
    let unloads = Arc::new(AtomicUsize::new(0));
    let preview_unloads = Arc::new(AtomicUsize::new(0));
    let blocked_started = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let blocked_observed_cancel = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let blocked_finished = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let block_release = Arc::new(std::sync::atomic::AtomicBool::new(true));

    let permissions = Arc::new(StaticPermissions {
        mic: MicrophonePermissionStatus::Authorized,
    });
    let backend = LiveDictationBackend::with_components(
        dir.path(),
        permissions,
        Arc::new(SyntheticAudioFactory::new(16_000, 320, 1)),
        Some(Arc::new(TrackingPreloadEngine {
            preloads: Arc::clone(&preloads),
            unloads: Arc::clone(&unloads),
            preview_unloads: Arc::clone(&preview_unloads),
            block_model: Some("large-v3-turbo".to_owned()),
            block_release: Some(Arc::clone(&block_release)),
            blocked_started: Arc::clone(&blocked_started),
            blocked_observed_cancel: Arc::clone(&blocked_observed_cancel),
            blocked_finished: Arc::clone(&blocked_finished),
        })),
        ProviderEndpoints::default(),
    )
    .unwrap();

    // 1. Initial local config preloads once and coalesces duplicate sync calls.
    let parakeet_cfg = DaemonConfig {
        mode: DaemonMode::Local,
        model: Some("parakeet-v3".to_owned()),
        language: Some("de".to_owned()),
        streaming: true,
        ..DaemonConfig::default()
    };
    backend.sync_local_preload_state(&parakeet_cfg);
    tokio::time::timeout(Duration::from_secs(2), async {
        while preloads.lock().unwrap().is_empty() {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();
    // Give the background task a brief moment to record `ready = true`.
    tokio::time::sleep(Duration::from_millis(25)).await;
    backend.sync_local_preload_state(&parakeet_cfg);
    tokio::time::sleep(Duration::from_millis(25)).await;
    assert_eq!(
        *preloads.lock().unwrap(),
        vec![(Some("parakeet-v3".to_owned()), Some("de".to_owned()))]
    );
    assert_eq!(preview_unloads.load(Ordering::SeqCst), 0);

    // 2. Disabling streaming unloads only the streaming preview without reloading offline weights.
    let mut no_streaming_cfg = parakeet_cfg.clone();
    no_streaming_cfg.streaming = false;
    backend.sync_local_preload_state(&no_streaming_cfg);
    assert_eq!(preview_unloads.load(Ordering::SeqCst), 1);
    assert_eq!(preloads.lock().unwrap().len(), 1);

    // 3. Switching to a slow model then leaving Local cancels in-flight preload & unloads.
    let turbo_cfg = DaemonConfig {
        mode: DaemonMode::Local,
        model: Some("large-v3-turbo".to_owned()),
        streaming: true,
        ..DaemonConfig::default()
    };
    backend.sync_local_preload_state(&turbo_cfg);
    tokio::time::timeout(Duration::from_secs(2), async {
        while !blocked_started.load(Ordering::SeqCst) {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();

    let cloud_cfg = DaemonConfig {
        mode: DaemonMode::Deepgram,
        ..DaemonConfig::default()
    };
    backend.sync_local_preload_state(&cloud_cfg);
    tokio::time::timeout(Duration::from_secs(2), async {
        while !blocked_finished.load(Ordering::SeqCst) {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();
    assert!(blocked_observed_cancel.load(Ordering::SeqCst));
    assert!(unloads.load(Ordering::SeqCst) >= 1);

    // 4. `unload_local_model_if_matching` matches aliases (`"turbo"` -> `"large-v3-turbo"`).
    let alias_cfg = DaemonConfig {
        mode: DaemonMode::Local,
        model: Some("turbo".to_owned()),
        ..DaemonConfig::default()
    };
    backend.sync_local_preload_state(&alias_cfg);
    assert!(!backend.unload_local_model_if_matching("parakeet-v3").await);
    assert!(
        backend
            .unload_local_model_if_matching("large-v3-turbo")
            .await
    );

    // 5. Superseded in-flight preload is still awaited when deleting the previous model.
    blocked_started.store(false, Ordering::SeqCst);
    blocked_finished.store(false, Ordering::SeqCst);
    block_release.store(false, Ordering::SeqCst);
    backend.sync_local_preload_state(&turbo_cfg);
    tokio::time::timeout(Duration::from_secs(2), async {
        while !blocked_started.load(Ordering::SeqCst) {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();
    // Switch to `parakeet-v3` while `large-v3-turbo` is still inside its native loader.
    backend.sync_local_preload_state(&parakeet_cfg);
    let release_clone = Arc::clone(&block_release);
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(30)).await;
        release_clone.store(true, Ordering::Release);
    });
    assert!(
        backend
            .unload_local_model_if_matching("large-v3-turbo")
            .await
    );
    assert!(blocked_finished.load(Ordering::SeqCst));

    backend.shutdown().await;
}

struct FakeStreamingPreviewSession {
    samples_seen: usize,
}

impl LocalStreamingSession for FakeStreamingPreviewSession {
    fn feed(&mut self, samples_16k: &[f32]) -> Result<(), LocalAsrError> {
        self.samples_seen += samples_16k.len();
        Ok(())
    }

    fn text(&self) -> Result<String, LocalAsrError> {
        if self.samples_seen >= 960 {
            Ok("Hallo Welt".to_owned())
        } else if self.samples_seen > 0 {
            Ok("Hallo".to_owned())
        } else {
            Ok(String::new())
        }
    }
}

struct PreviewCapableLocalEngine {
    preview_opens: Arc<AtomicUsize>,
    preview_mode: PreviewMode,
    final_transcript: String,
}

#[derive(Clone, Copy)]
enum PreviewMode {
    Live,
    Error,
}

impl LocalTranscriber for PreviewCapableLocalEngine {
    fn transcribe(&self, request: LocalTranscriptionRequest) -> Result<String, DaemonStatusError> {
        assert!(!request.samples_16k.is_empty());
        Ok(self.final_transcript.clone())
    }

    fn open_streaming_preview(
        &self,
        _language: Option<&str>,
        _cancel_flag: &std::sync::atomic::AtomicBool,
    ) -> Result<Option<Box<dyn LocalStreamingSession>>, LocalAsrError> {
        self.preview_opens.fetch_add(1, Ordering::SeqCst);
        match self.preview_mode {
            PreviewMode::Live => Ok(Some(Box::new(FakeStreamingPreviewSession {
                samples_seen: 0,
            }))),
            PreviewMode::Error => Err(LocalAsrError::MissingRuntime(
                "libsherpa-onnx-c-api missing".to_owned(),
            )),
        }
    }
}

#[tokio::test]
async fn local_mode_streaming_preview_emits_interim_text_and_final_offline_transcript() {
    let dir = TempDir::new("local-live-preview");

    // 1. Local mode with `streaming = true`: emits live `InterimText` during capture, then final offline transcript.
    {
        let store = Arc::new(PreferencesStore::new(dir.path()));
        let (pasted, clipboard) = RecordingClipboard::create();
        let preview_opens = Arc::new(AtomicUsize::new(0));
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
            Arc::new(SyntheticAudioFactory::new(16_000, 640, 3)),
            Some(Arc::new(PreviewCapableLocalEngine {
                preview_opens: Arc::clone(&preview_opens),
                preview_mode: PreviewMode::Live,
                final_transcript: "Hallo Welt!".to_owned(),
            })),
            ProviderEndpoints::default(),
        )
        .unwrap();
        let runtime = DaemonRuntime::spawn(
            store,
            DaemonConfig {
                mode: DaemonMode::Local,
                streaming: true,
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
        let interim = next_matching_event(
            &mut events,
            |e| matches!(e, DaemonEvent::InterimText { text, .. } if text.contains("Hallo")),
        )
        .await;
        assert!(matches!(interim, DaemonEvent::InterimText { .. }));
        assert_eq!(preview_opens.load(Ordering::SeqCst), 1);

        handle.stop_recording();
        let delivered = next_matching_event(&mut events, |e| {
            matches!(e, DaemonEvent::TranscriptDelivered { .. })
        })
        .await;
        match delivered {
            DaemonEvent::TranscriptDelivered { text, .. } => assert_eq!(text, "Hallo Welt!"),
            other => panic!("unexpected event: {other:?}"),
        }
        assert_eq!(*pasted.lock().unwrap(), vec!["Hallo Welt!".to_owned()]);

        runtime.shutdown().await;
        backend.shutdown().await;
    }

    // 2. Local mode with `streaming = false` skips opening preview completely.
    {
        let store = Arc::new(PreferencesStore::new(dir.path()));
        let (_, clipboard) = RecordingClipboard::create();
        let preview_opens = Arc::new(AtomicUsize::new(0));
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
            Arc::new(SyntheticAudioFactory::new(16_000, 640, 2)),
            Some(Arc::new(PreviewCapableLocalEngine {
                preview_opens: Arc::clone(&preview_opens),
                preview_mode: PreviewMode::Live,
                final_transcript: "Offline only".to_owned(),
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
        let delivered = next_matching_event(&mut events, |e| {
            matches!(e, DaemonEvent::TranscriptDelivered { .. })
        })
        .await;
        match delivered {
            DaemonEvent::TranscriptDelivered { text, .. } => assert_eq!(text, "Offline only"),
            other => panic!("unexpected event: {other:?}"),
        }
        assert_eq!(preview_opens.load(Ordering::SeqCst), 0);

        runtime.shutdown().await;
        backend.shutdown().await;
    }

    // 3. Preview runtime error never aborts offline dictation.
    {
        let store = Arc::new(PreferencesStore::new(dir.path()));
        let (_, clipboard) = RecordingClipboard::create();
        let preview_opens = Arc::new(AtomicUsize::new(0));
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
            Arc::new(SyntheticAudioFactory::new(16_000, 640, 2)),
            Some(Arc::new(PreviewCapableLocalEngine {
                preview_opens: Arc::clone(&preview_opens),
                preview_mode: PreviewMode::Error,
                final_transcript: "Fallback ok".to_owned(),
            })),
            ProviderEndpoints::default(),
        )
        .unwrap();
        let runtime = DaemonRuntime::spawn(
            store,
            DaemonConfig {
                mode: DaemonMode::Local,
                streaming: true,
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
        let delivered = next_matching_event(&mut events, |e| {
            matches!(e, DaemonEvent::TranscriptDelivered { .. })
        })
        .await;
        match delivered {
            DaemonEvent::TranscriptDelivered { text, .. } => assert_eq!(text, "Fallback ok"),
            other => panic!("unexpected event: {other:?}"),
        }
        assert_eq!(preview_opens.load(Ordering::SeqCst), 1);

        runtime.shutdown().await;
        backend.shutdown().await;
    }
}

struct AutoDownloadLocalEngine {
    cache: ModelCache,
    downloader: ModelDownloader,
    preloads: Arc<AtomicUsize>,
}

impl LocalTranscriber for AutoDownloadLocalEngine {
    fn transcribe(&self, request: LocalTranscriptionRequest) -> Result<String, DaemonStatusError> {
        let spec = resolve_model_spec(request.model.as_deref()).expect("curated model");
        assert!(
            self.cache.find_installed(spec).is_some(),
            "model must be downloaded before transcribe"
        );
        Ok(format!("transcribed:{}", spec.id))
    }

    fn missing_download_spec(
        &self,
        model: Option<&str>,
    ) -> Result<Option<&'static ModelSpec>, DaemonStatusError> {
        let spec = resolve_model_spec(model).expect("curated model");
        if self.cache.find_installed(spec).is_some() {
            Ok(None)
        } else {
            Ok(Some(spec))
        }
    }

    fn auto_downloader(&self) -> Option<(ModelCache, ModelDownloader)> {
        Some((self.cache.clone(), self.downloader.clone()))
    }

    fn preload(
        &self,
        model: Option<&str>,
        _language: Option<&str>,
        _cancel_flag: &std::sync::atomic::AtomicBool,
    ) -> Result<bool, DaemonStatusError> {
        let spec = resolve_model_spec(model).expect("curated model");
        assert!(
            self.cache.find_installed(spec).is_some(),
            "model must be downloaded before preload"
        );
        self.preloads.fetch_add(1, Ordering::SeqCst);
        Ok(true)
    }
}

struct RecordingPreloadObserver {
    loading_events: Arc<Mutex<Vec<Option<String>>>>,
    progress_count: Arc<AtomicUsize>,
    finished: Arc<Mutex<Vec<(String, bool)>>>,
}

impl LocalPreloadObserver for RecordingPreloadObserver {
    fn on_loading_status(&self, detail: Option<String>) {
        self.loading_events.lock().unwrap().push(detail);
    }

    fn on_download_progress(&self, _progress: DownloadProgress) {
        self.progress_count.fetch_add(1, Ordering::SeqCst);
    }

    fn on_download_finished(&self, model_id: &str, result: Result<(), &str>) {
        self.finished
            .lock()
            .unwrap()
            .push((model_id.to_owned(), result.is_ok()));
    }
}

#[tokio::test]
async fn local_mode_auto_downloads_missing_model_on_preload_and_on_demand() {
    let dir = TempDir::new("local-auto-download");

    // Loopback HTTP server serving synthetic weights for every curated model file path.
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let http_hits = Arc::new(AtomicUsize::new(0));
    let http_hits_server = Arc::clone(&http_hits);
    let server = tokio::spawn(async move {
        while let Ok((mut stream, _)) = listener.accept().await {
            let hits = Arc::clone(&http_hits_server);
            tokio::spawn(async move {
                let mut req_buf = [0u8; 2048];
                let _ = stream.read(&mut req_buf).await;
                hits.fetch_add(1, Ordering::SeqCst);
                let body = b"synthetic-model-weight-bytes";
                let resp = format!(
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                );
                let _ = stream.write_all(resp.as_bytes()).await;
                let _ = stream.write_all(body).await;
                let _ = stream.shutdown().await;
            });
        }
    });

    let cache = ModelCache::new(dir.path().join("models"), None);
    let downloader = ModelDownloader::with_base_url(format!("http://{addr}")).unwrap();
    let preloads = Arc::new(AtomicUsize::new(0));

    let permissions = Arc::new(StaticPermissions {
        mic: MicrophonePermissionStatus::Authorized,
    });
    let backend = LiveDictationBackend::with_components(
        dir.path(),
        Arc::clone(&permissions) as Arc<dyn PermissionsChecker>,
        Arc::new(SyntheticAudioFactory::new(16_000, 640, 2)),
        Some(Arc::new(AutoDownloadLocalEngine {
            cache: cache.clone(),
            downloader: downloader.clone(),
            preloads: Arc::clone(&preloads),
        })),
        ProviderEndpoints::default(),
    )
    .unwrap();

    let loading_events = Arc::new(Mutex::new(Vec::new()));
    let progress_count = Arc::new(AtomicUsize::new(0));
    let finished = Arc::new(Mutex::new(Vec::new()));
    backend.set_local_preload_observer(Arc::new(RecordingPreloadObserver {
        loading_events: Arc::clone(&loading_events),
        progress_count: Arc::clone(&progress_count),
        finished: Arc::clone(&finished),
    }));

    // 1. Background preload auto-downloads all 3 Canary 1B v2 files, emits loading progress, then preloads.
    let canary_cfg = DaemonConfig {
        mode: DaemonMode::Local,
        model: Some("canary-1b-v2".to_owned()),
        language: Some("de".to_owned()),
        streaming: false,
        ..DaemonConfig::default()
    };
    backend.sync_local_preload_state(&canary_cfg);
    tokio::time::timeout(Duration::from_secs(5), async {
        while preloads.load(Ordering::SeqCst) == 0 {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    // Wait briefly for `on_loading_status(None)` cleanup to record.
    tokio::time::timeout(Duration::from_secs(2), async {
        while loading_events.lock().unwrap().last() != Some(&None) {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();

    let canary_spec = resolve_model_spec(Some("canary-1b-v2")).unwrap();
    assert!(cache.find_installed(canary_spec).is_some());
    assert_eq!(http_hits.load(Ordering::SeqCst), canary_spec.files.len());
    assert!(progress_count.load(Ordering::SeqCst) >= canary_spec.files.len());
    assert_eq!(
        *finished.lock().unwrap(),
        vec![("canary-1b-v2".to_owned(), true)]
    );
    let statuses = loading_events.lock().unwrap().clone();
    assert!(statuses.iter().any(|s| s
        .as_deref()
        .is_some_and(|t| t.starts_with("Downloading canary-1b-v2"))));
    assert!(statuses
        .iter()
        .any(|s| s.as_deref() == Some("Loading canary-1b-v2")));
    assert_eq!(statuses.last(), Some(&None));
    assert_eq!(backend.active_local_download_model_id(), None);

    // 2. Dictating with an un-cached model (`parakeet-v3`, no preload called) auto-downloads on demand.
    let store = Arc::new(PreferencesStore::new(dir.path()));
    let (_, clipboard) = RecordingClipboard::create();
    let platform = DaemonPlatform {
        permissions: Arc::clone(&permissions) as Arc<dyn PermissionsChecker>,
        ..DaemonPlatform::noop(clipboard)
    };
    let runtime = DaemonRuntime::spawn(
        store,
        DaemonConfig {
            mode: DaemonMode::Local,
            model: Some("parakeet-v3".to_owned()),
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
    let progress_before_on_demand = progress_count.load(Ordering::SeqCst);
    handle.stop_recording();
    let mut saw_download_detail = false;
    let mut saw_transcribing_reset = false;
    let delivered = tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            let evt = events.recv().await.unwrap();
            match &evt {
                DaemonEvent::StateChanged {
                    state: AppState::Transcribing,
                    detail: Some(d),
                    ..
                } if d.starts_with("Downloading parakeet-v3") => {
                    saw_download_detail = true;
                }
                DaemonEvent::StateChanged {
                    state: AppState::Transcribing,
                    detail: None,
                    ..
                } if saw_download_detail => {
                    saw_transcribing_reset = true;
                }
                DaemonEvent::TranscriptDelivered { .. } => return evt,
                _ => {}
            }
        }
    })
    .await
    .unwrap();
    match delivered {
        DaemonEvent::TranscriptDelivered { text, .. } => {
            assert_eq!(text, "transcribed:parakeet-v3");
        }
        other => panic!("unexpected event: {other:?}"),
    }
    let parakeet_spec = resolve_model_spec(Some("parakeet-v3")).unwrap();
    assert!(cache.find_installed(parakeet_spec).is_some());
    assert!(
        saw_download_detail,
        "expected Transcribing overlay detail showing on-demand download progress"
    );
    assert!(
        saw_transcribing_reset,
        "expected Transcribing overlay detail to reset to None before offline inference"
    );
    assert!(
        progress_count.load(Ordering::SeqCst)
            >= progress_before_on_demand + parakeet_spec.files.len(),
        "expected dictation-started download to forward progress to Settings observer"
    );
    assert_eq!(
        *finished.lock().unwrap(),
        vec![
            ("canary-1b-v2".to_owned(), true),
            ("parakeet-v3".to_owned(), true),
        ]
    );
    assert_eq!(backend.active_local_download_model_id(), None);

    runtime.shutdown().await;
    backend.shutdown().await;
    server.abort();
}
