//! Streaming model weight downloader with byte-level progress, cooperative cancellation,
//! buffered async disk writes, per-model serialization, and atomic `.part` promotion.

use std::collections::HashMap;
use std::fmt;
use std::future::Future;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use futures_util::StreamExt;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use tokio::io::{AsyncWriteExt, BufWriter};
use tokio::sync::{broadcast, Mutex as AsyncMutex};

use crate::cache::{verify_all_files_present, InstalledModel, ModelCache, ModelInstallSource};
use crate::catalog::{
    build_hf_resolve_url, validate_safe_filename, validate_safe_rel_path, ModelSpec,
    DEFAULT_HF_BASE_URL,
};
use crate::LocalAsrError;

const DEFAULT_CONNECT_TIMEOUT: Duration = Duration::from_secs(20);
const DEFAULT_READ_TIMEOUT: Duration = Duration::from_secs(60);
const CANCEL_POLL_INTERVAL: Duration = Duration::from_millis(50);
const WRITE_BUFFER_BYTES: usize = 256 * 1024;
const PROGRESS_EMIT_MIN_INTERVAL: Duration = Duration::from_millis(50);
const PROGRESS_EMIT_MIN_BYTES: u64 = 64 * 1024;

static PART_FILE_SEQ: AtomicU64 = AtomicU64::new(0);

/// Progress snapshot emitted while downloading a multi-file local ASR model.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DownloadProgress {
    pub model_id: String,
    pub file: String,
    /// 1-based index of the file currently downloading.
    pub file_index: usize,
    pub total_files: usize,
    pub bytes_downloaded: u64,
    pub total_bytes: Option<u64>,
}

struct ModelDownloadSlot {
    lock: AsyncMutex<()>,
    progress_tx: broadcast::Sender<DownloadProgress>,
}

type DownloadLockMap = HashMap<(PathBuf, String), Arc<ModelDownloadSlot>>;

/// Streaming HTTPS downloader for Hugging Face model weights.
#[derive(Clone)]
pub struct ModelDownloader {
    client: Client,
    hf_base_url: String,
    in_flight: Arc<Mutex<DownloadLockMap>>,
}

impl fmt::Debug for ModelDownloader {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ModelDownloader")
            .field(
                "custom_base_url",
                &(self.hf_base_url != DEFAULT_HF_BASE_URL),
            )
            .finish_non_exhaustive()
    }
}

impl ModelDownloader {
    /// Construct a downloader targeting `https://huggingface.co` with pooled TLS connections.
    pub fn new() -> Result<Self, LocalAsrError> {
        Self::with_base_url(DEFAULT_HF_BASE_URL)
    }

    /// Construct a downloader targeting a custom base URL (e.g., local loopback mirror/tests).
    pub fn with_base_url(base_url: impl Into<String>) -> Result<Self, LocalAsrError> {
        let client = Client::builder()
            .connect_timeout(DEFAULT_CONNECT_TIMEOUT)
            .read_timeout(DEFAULT_READ_TIMEOUT)
            .user_agent(concat!("pulsescribe/", env!("CARGO_PKG_VERSION")))
            .build()
            .map_err(|_| LocalAsrError::Download("HTTP client initialization failed".to_owned()))?;
        Ok(Self {
            client,
            hf_base_url: base_url.into(),
            in_flight: Arc::new(Mutex::new(HashMap::new())),
        })
    }

    /// Ensure all files for `spec` are cached on disk, downloading missing files into
    /// `cache.model_dir(spec)` with progress callbacks and cooperative cancellation.
    ///
    /// Concurrent calls for the same `(models_dir, spec.id)` are serialized so duplicate
    /// downloads cannot race on temporary `.part` renames, while waiting callers still receive
    /// live progress updates broadcast by the active transfer.
    pub async fn ensure_downloaded<F>(
        &self,
        cache: &ModelCache,
        spec: &ModelSpec,
        cancel_flag: &AtomicBool,
        mut on_progress: F,
    ) -> Result<InstalledModel, LocalAsrError>
    where
        F: FnMut(DownloadProgress),
    {
        self.ensure_downloaded_observing(
            cache,
            spec,
            cancel_flag,
            || {},
            |progress, _from_peer| on_progress(progress),
        )
        .await
    }

    /// Like [`Self::ensure_downloaded`], invoking `on_own_transfer_start` only when this call
    /// acquires the per-model slot and begins downloading missing files itself (not when waiting
    /// on a concurrent peer transfer that finishes the install), and tagging each progress event
    /// with `from_peer == true` while waiting on a peer transfer.
    pub async fn ensure_downloaded_observing<S, F>(
        &self,
        cache: &ModelCache,
        spec: &ModelSpec,
        cancel_flag: &AtomicBool,
        on_own_transfer_start: S,
        mut on_progress: F,
    ) -> Result<InstalledModel, LocalAsrError>
    where
        S: FnOnce(),
        F: FnMut(DownloadProgress, bool),
    {
        validate_safe_filename(spec.id)?;
        if cancel_flag.load(Ordering::Acquire) {
            return Err(LocalAsrError::Cancelled);
        }

        let slot = self.slot_for(cache.models_dir(), spec.id);
        let mut peer_rx = slot.progress_tx.subscribe();
        let _permit = acquire_slot_forwarding_progress(
            cancel_flag,
            &slot.lock,
            &mut peer_rx,
            &mut |progress| on_progress(progress, true),
        )
        .await?;
        drop(peer_rx);

        if cancel_flag.load(Ordering::Acquire) {
            return Err(LocalAsrError::Cancelled);
        }

        let cache_probe = cache.clone();
        let spec_copy = *spec;
        if let Some(installed) =
            tokio::task::spawn_blocking(move || cache_probe.find_installed(&spec_copy))
                .await
                .map_err(|err| LocalAsrError::Download(format!("cache probe failed: {err}")))?
        {
            return Ok(installed);
        }

        if cancel_flag.load(Ordering::Acquire) {
            return Err(LocalAsrError::Cancelled);
        }

        on_own_transfer_start();

        let target_dir = cache.model_dir(spec);
        ensure_safe_managed_dir(&target_dir).await?;

        let mut emit_progress = |progress: DownloadProgress| {
            if slot.progress_tx.receiver_count() > 0 {
                let _ = slot.progress_tx.send(progress.clone());
            }
            on_progress(progress, false);
        };

        let total_files = spec.files.len();
        for (idx, &filename) in spec.files.iter().enumerate() {
            validate_safe_rel_path(filename)?;
            if cancel_flag.load(Ordering::Acquire) {
                return Err(LocalAsrError::Cancelled);
            }

            ensure_safe_managed_parent_dirs(&target_dir, filename).await?;
            let final_path = target_dir.join(filename);
            if let Some(len) = existing_regular_file_len(&final_path).await {
                emit_progress(DownloadProgress {
                    model_id: spec.id.to_owned(),
                    file: filename.to_owned(),
                    file_index: idx + 1,
                    total_files,
                    bytes_downloaded: len,
                    total_bytes: Some(len),
                });
                continue;
            }

            let url = build_hf_resolve_url(&self.hf_base_url, spec, filename)?;
            self.download_single_file(
                url,
                &final_path,
                spec.id,
                filename,
                idx + 1,
                total_files,
                cancel_flag,
                &mut emit_progress,
            )
            .await?;
        }

        let verify_dir = target_dir.clone();
        let files = spec.files;
        let total_bytes = tokio::task::spawn_blocking(move || {
            verify_all_files_present(&verify_dir, files, false)
        })
        .await
        .map_err(|err| LocalAsrError::Download(format!("verification task failed: {err}")))?
        .ok_or_else(|| {
            LocalAsrError::Download(format!(
                "downloaded model '{}' is incomplete on disk",
                spec.id
            ))
        })?;

        Ok(InstalledModel {
            model_id: spec.id.to_owned(),
            dir: target_dir,
            source: ModelInstallSource::PulseScribeCache,
            total_bytes,
        })
    }

    fn slot_for(&self, models_dir: &Path, model_id: &str) -> Arc<ModelDownloadSlot> {
        let key = (models_dir.to_path_buf(), model_id.to_owned());
        let mut guard = self.in_flight.lock().unwrap_or_else(|e| e.into_inner());
        Arc::clone(guard.entry(key).or_insert_with(|| {
            let (progress_tx, _) = broadcast::channel(32);
            Arc::new(ModelDownloadSlot {
                lock: AsyncMutex::new(()),
                progress_tx,
            })
        }))
    }

    #[allow(clippy::too_many_arguments)]
    async fn download_single_file<F>(
        &self,
        url: url::Url,
        final_path: &Path,
        model_id: &str,
        filename: &str,
        file_index: usize,
        total_files: usize,
        cancel_flag: &AtomicBool,
        on_progress: &mut F,
    ) -> Result<(), LocalAsrError>
    where
        F: FnMut(DownloadProgress),
    {
        let response = race_with_cancel(cancel_flag, self.client.get(url).send())
            .await?
            .map_err(|err| {
                LocalAsrError::Download(format!(
                    "{} while downloading '{filename}'",
                    classify_reqwest_error(&err)
                ))
            })?;

        let status = response.status();
        if !status.is_success() {
            return Err(LocalAsrError::Download(format!(
                "HTTP {status} while downloading '{filename}'"
            )));
        }

        let total_bytes = response.content_length();
        let seq = PART_FILE_SEQ.fetch_add(1, Ordering::Relaxed);
        let leaf_name = final_path
            .file_name()
            .and_then(|n| n.to_str())
            .ok_or_else(|| {
                LocalAsrError::InvalidModel(format!("invalid target path '{filename}'"))
            })?;
        let part_path =
            final_path.with_file_name(format!(".{leaf_name}.part.{}.{seq}", std::process::id()));

        let mut part_guard = PartFileGuard::new(part_path.clone());
        let file = tokio::fs::File::create(&part_path).await?;
        let mut writer = BufWriter::with_capacity(WRITE_BUFFER_BYTES, file);
        let mut stream = response.bytes_stream();
        let mut bytes_downloaded = 0_u64;
        let mut last_emitted_bytes = 0_u64;
        let mut last_emitted_at = Instant::now();

        on_progress(DownloadProgress {
            model_id: model_id.to_owned(),
            file: filename.to_owned(),
            file_index,
            total_files,
            bytes_downloaded: 0,
            total_bytes,
        });

        let write_result: Result<(), LocalAsrError> = async {
            while let Some(chunk_res) = race_with_cancel(cancel_flag, stream.next()).await? {
                if cancel_flag.load(Ordering::Acquire) {
                    return Err(LocalAsrError::Cancelled);
                }
                let chunk = chunk_res.map_err(|err| {
                    LocalAsrError::Download(format!(
                        "stream {} while downloading '{filename}'",
                        classify_reqwest_error(&err)
                    ))
                })?;
                writer.write_all(&chunk).await?;
                bytes_downloaded = bytes_downloaded.saturating_add(chunk.len() as u64);

                let finished_expected = total_bytes.is_some_and(|exp| bytes_downloaded >= exp);
                let enough_bytes =
                    bytes_downloaded.saturating_sub(last_emitted_bytes) >= PROGRESS_EMIT_MIN_BYTES;
                let enough_time = last_emitted_at.elapsed() >= PROGRESS_EMIT_MIN_INTERVAL;
                if finished_expected || enough_bytes || enough_time {
                    last_emitted_bytes = bytes_downloaded;
                    last_emitted_at = Instant::now();
                    on_progress(DownloadProgress {
                        model_id: model_id.to_owned(),
                        file: filename.to_owned(),
                        file_index,
                        total_files,
                        bytes_downloaded,
                        total_bytes,
                    });
                }
            }

            // Re-check cancellation after stream EOF (e.g. set inside the final progress callback).
            if cancel_flag.load(Ordering::Acquire) {
                return Err(LocalAsrError::Cancelled);
            }
            if bytes_downloaded == 0 {
                return Err(LocalAsrError::Download(format!(
                    "downloaded '{filename}' was empty"
                )));
            }
            if let Some(expected) = total_bytes {
                if bytes_downloaded != expected {
                    return Err(LocalAsrError::Download(format!(
                        "truncated download for '{filename}' ({bytes_downloaded}/{expected} bytes)"
                    )));
                }
            }
            if bytes_downloaded != last_emitted_bytes {
                on_progress(DownloadProgress {
                    model_id: model_id.to_owned(),
                    file: filename.to_owned(),
                    file_index,
                    total_files,
                    bytes_downloaded,
                    total_bytes,
                });
                if cancel_flag.load(Ordering::Acquire) {
                    return Err(LocalAsrError::Cancelled);
                }
            }

            writer.flush().await?;
            Ok(())
        }
        .await;

        // Always finish closing the underlying file handle before renaming or removing `.part`.
        let mut inner_file = writer.into_inner();
        let shutdown_res = inner_file.shutdown().await;
        drop(inner_file);

        if let Err(err) = write_result {
            part_guard.remove_async().await;
            return Err(err);
        }
        if let Err(err) = shutdown_res {
            part_guard.remove_async().await;
            return Err(LocalAsrError::Io(err));
        }

        // Rust's `rename` uses `ReplaceIfExists` on Windows and atomic `rename(2)` on Unix.
        if let Err(err) = tokio::fs::rename(&part_path, final_path).await {
            part_guard.remove_async().await;
            return Err(LocalAsrError::Io(err));
        }
        part_guard.disarm();
        Ok(())
    }
}

fn classify_reqwest_error(err: &reqwest::Error) -> &'static str {
    if err.is_timeout() {
        "timed out"
    } else if err.is_connect() {
        "connection failed"
    } else if err.is_body() || err.is_decode() {
        "read failed"
    } else {
        "request failed"
    }
}

async fn acquire_slot_forwarding_progress<'a, F>(
    cancel_flag: &AtomicBool,
    lock: &'a AsyncMutex<()>,
    peer_rx: &mut broadcast::Receiver<DownloadProgress>,
    on_progress: &mut F,
) -> Result<tokio::sync::MutexGuard<'a, ()>, LocalAsrError>
where
    F: FnMut(DownloadProgress),
{
    let lock_fut = lock.lock();
    tokio::pin!(lock_fut);
    let mut interval = tokio::time::interval(CANCEL_POLL_INTERVAL);
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    interval.tick().await;
    let mut peer_closed = false;

    loop {
        if cancel_flag.load(Ordering::Acquire) {
            return Err(LocalAsrError::Cancelled);
        }
        tokio::select! {
            biased;
            guard = &mut lock_fut => return Ok(guard),
            recv = peer_rx.recv(), if !peer_closed => {
                match recv {
                    Ok(progress) => on_progress(progress),
                    Err(broadcast::error::RecvError::Lagged(_)) => {}
                    Err(broadcast::error::RecvError::Closed) => peer_closed = true,
                }
            }
            _ = interval.tick() => {}
        }
    }
}

async fn race_with_cancel<F, T>(cancel_flag: &AtomicBool, fut: F) -> Result<T, LocalAsrError>
where
    F: Future<Output = T>,
{
    if cancel_flag.load(Ordering::Acquire) {
        return Err(LocalAsrError::Cancelled);
    }
    tokio::pin!(fut);
    let mut ticker = tokio::time::interval(CANCEL_POLL_INTERVAL);
    ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    ticker.tick().await;
    loop {
        tokio::select! {
            biased;
            res = &mut fut => return Ok(res),
            _ = ticker.tick() => {
                if cancel_flag.load(Ordering::Acquire) {
                    return Err(LocalAsrError::Cancelled);
                }
            }
        }
    }
}

/// Ensure `target_dir` exists as a real (non-symlink) directory inside the managed model cache.
async fn ensure_safe_managed_dir(target_dir: &Path) -> Result<(), LocalAsrError> {
    if let Some(parent) = target_dir.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    match tokio::fs::symlink_metadata(target_dir).await {
        Ok(meta) => {
            if meta.file_type().is_symlink() || !meta.is_dir() {
                return Err(LocalAsrError::Download(format!(
                    "refusing to write through non-directory or symlink '{}'",
                    target_dir.display()
                )));
            }
        }
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
            tokio::fs::create_dir(target_dir).await?;
        }
        Err(err) => return Err(LocalAsrError::Io(err)),
    }
    Ok(())
}

/// Validate and create any intermediate subdirectories for a relative model file path (`rel_path`)
/// without following symlinks, so downloads can never write outside the managed model directory.
async fn ensure_safe_managed_parent_dirs(
    target_dir: &Path,
    rel_path: &str,
) -> Result<(), LocalAsrError> {
    let segments: Vec<&str> = rel_path.split('/').collect();
    if segments.len() <= 1 {
        return Ok(());
    }
    let mut cursor = target_dir.to_path_buf();
    for segment in &segments[..segments.len() - 1] {
        cursor.push(segment);
        match tokio::fs::symlink_metadata(&cursor).await {
            Ok(meta) => {
                if meta.file_type().is_symlink() || !meta.is_dir() {
                    return Err(LocalAsrError::Download(format!(
                        "refusing to write through non-directory or symlink '{}'",
                        cursor.display()
                    )));
                }
            }
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                tokio::fs::create_dir(&cursor).await?;
            }
            Err(err) => return Err(LocalAsrError::Io(err)),
        }
    }
    Ok(())
}

async fn existing_regular_file_len(path: &Path) -> Option<u64> {
    let meta = tokio::fs::symlink_metadata(path).await.ok()?;
    (!meta.file_type().is_symlink() && meta.is_file() && meta.len() > 0).then_some(meta.len())
}

struct PartFileGuard {
    path: Option<PathBuf>,
}

impl PartFileGuard {
    fn new(path: PathBuf) -> Self {
        Self { path: Some(path) }
    }

    fn disarm(&mut self) {
        self.path = None;
    }

    async fn remove_async(mut self) {
        if let Some(path) = self.path.take() {
            let _ = tokio::fs::remove_file(&path).await;
        }
    }
}

impl Drop for PartFileGuard {
    fn drop(&mut self) {
        if let Some(path) = self.path.take() {
            if let Ok(handle) = tokio::runtime::Handle::try_current() {
                handle.spawn_blocking(move || {
                    let _ = std::fs::remove_file(&path);
                });
            } else {
                let _ = std::fs::remove_file(&path);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::catalog::resolve_model_spec;
    use std::time::{SystemTime, UNIX_EPOCH};
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;

    static DL_TEST_SEQ: AtomicU64 = AtomicU64::new(0);

    struct TempDir(PathBuf);

    impl TempDir {
        fn new(tag: &str) -> Self {
            let seq = DL_TEST_SEQ.fetch_add(1, Ordering::Relaxed);
            let nanos = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let path = std::env::temp_dir().join(format!(
                "pulsescribe-local-dl-{tag}-{}-{nanos}-{seq}",
                std::process::id()
            ));
            std::fs::create_dir_all(&path).unwrap();
            Self(path)
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    async fn spawn_file_server(payloads: Vec<(&'static str, Vec<u8>)>) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move {
            for _ in 0..payloads.len() {
                let Ok((mut socket, _)) = listener.accept().await else {
                    break;
                };
                let mut req_buf = [0_u8; 4096];
                let n = socket.read(&mut req_buf).await.unwrap_or(0);
                let req = String::from_utf8_lossy(&req_buf[..n]);
                let path = req
                    .lines()
                    .next()
                    .and_then(|l| l.split_whitespace().nth(1))
                    .unwrap_or("");
                if let Some((_, body)) = payloads.iter().find(|(suffix, _)| path.ends_with(suffix))
                {
                    let header = format!(
                        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                        body.len()
                    );
                    let _ = socket.write_all(header.as_bytes()).await;
                    let _ = socket.write_all(body).await;
                } else {
                    let _ = socket
                        .write_all(
                            b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
                        )
                        .await;
                }
                let _ = socket.shutdown().await;
            }
        });
        format!("http://{addr}")
    }

    #[tokio::test]
    async fn downloads_multi_file_model_reports_progress_and_caches_atomically() {
        let dir = TempDir::new("canary");
        let cache = ModelCache::new(dir.0.join("models"), None);
        let canary = resolve_model_spec(Some("canary-1b-v2")).unwrap();

        let base_url = spawn_file_server(vec![
            ("encoder.int8.onnx", vec![1_u8; 1024]),
            ("decoder.int8.onnx", vec![2_u8; 512]),
            ("tokens.txt", b"0 <blank>\n1 a\n".to_vec()),
        ])
        .await;

        let downloader = ModelDownloader::with_base_url(base_url).unwrap();
        let cancel = AtomicBool::new(false);
        let mut seen_files = Vec::new();

        let installed = downloader
            .ensure_downloaded(&cache, canary, &cancel, |p| {
                if p.bytes_downloaded == p.total_bytes.unwrap_or(0) && p.bytes_downloaded > 0 {
                    seen_files.push((p.file, p.file_index, p.total_files));
                }
            })
            .await
            .unwrap();

        assert_eq!(installed.source, ModelInstallSource::PulseScribeCache);
        assert_eq!(installed.total_bytes, 1024 + 512 + 14);
        assert_eq!(seen_files.len(), 3);
        assert_eq!(seen_files[0].0, "encoder.int8.onnx");
        assert_eq!(seen_files[2], ("tokens.txt".to_owned(), 3, 3));

        // Second call hits cache without touching network.
        let cached = downloader
            .ensure_downloaded(&cache, canary, &cancel, |_| {
                panic!("cached model should not emit download progress");
            })
            .await
            .unwrap();
        assert_eq!(cached, installed);
    }

    #[tokio::test]
    async fn downloads_nested_subdirectory_model_files_atomically() {
        let dir = TempDir::new("qwen3-nested");
        let cache = ModelCache::new(dir.0.join("models"), None);
        let qwen3 = resolve_model_spec(Some("qwen3-asr-0.6b")).unwrap();

        let base_url = spawn_file_server(vec![
            ("conv_frontend.onnx", vec![1_u8; 64]),
            ("encoder.int8.onnx", vec![2_u8; 128]),
            ("decoder.int8.onnx", vec![3_u8; 256]),
            ("tokenizer/merges.txt", b"a b\n".to_vec()),
            ("tokenizer/tokenizer_config.json", b"{}".to_vec()),
            ("tokenizer/vocab.json", b"{\"a\":0}".to_vec()),
        ])
        .await;

        let downloader = ModelDownloader::with_base_url(base_url).unwrap();
        let cancel = AtomicBool::new(false);
        let installed = downloader
            .ensure_downloaded(&cache, qwen3, &cancel, |_| {})
            .await
            .unwrap();

        assert_eq!(installed.source, ModelInstallSource::PulseScribeCache);
        assert_eq!(installed.total_bytes, 64 + 128 + 256 + 4 + 2 + 7);
        assert!(installed.file_path("tokenizer/vocab.json").is_file());
        assert_eq!(cache.find_installed(qwen3), Some(installed));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn download_rejects_symlinked_subdirectory_without_writing_outside_cache() {
        let dir = TempDir::new("qwen3-symlink-guard");
        let cache = ModelCache::new(dir.0.join("models"), None);
        let qwen3 = resolve_model_spec(Some("qwen3-asr-0.6b")).unwrap();

        let external = dir.0.join("external_target");
        std::fs::create_dir_all(&external).unwrap();
        let model_dir = cache.model_dir(qwen3);
        std::fs::create_dir_all(&model_dir).unwrap();
        std::os::unix::fs::symlink(&external, model_dir.join("tokenizer")).unwrap();

        let base_url = spawn_file_server(vec![
            ("conv_frontend.onnx", vec![1_u8; 64]),
            ("encoder.int8.onnx", vec![2_u8; 128]),
            ("decoder.int8.onnx", vec![3_u8; 256]),
            ("tokenizer/merges.txt", b"a b\n".to_vec()),
            ("tokenizer/tokenizer_config.json", b"{}".to_vec()),
            ("tokenizer/vocab.json", b"{\"a\":0}".to_vec()),
        ])
        .await;

        let downloader = ModelDownloader::with_base_url(base_url).unwrap();
        let cancel = AtomicBool::new(false);
        let err = downloader
            .ensure_downloaded(&cache, qwen3, &cancel, |_| {})
            .await
            .unwrap_err();
        assert!(matches!(err, LocalAsrError::Download(_)));
        assert_eq!(std::fs::read_dir(&external).unwrap().count(), 0);
    }

    #[tokio::test]
    async fn honors_pre_cancelled_and_final_callback_cancel_without_promoting_or_leaking_parts() {
        let dir = TempDir::new("cancel");
        let cache = ModelCache::new(dir.0.join("models"), None);
        let tiny = resolve_model_spec(Some("tiny")).unwrap();

        // 1. Pre-cancelled.
        let downloader = ModelDownloader::with_base_url("http://127.0.0.1:1").unwrap();
        let pre_cancel = AtomicBool::new(true);
        let err = downloader
            .ensure_downloaded(&cache, tiny, &pre_cancel, |_| {})
            .await
            .unwrap_err();
        assert!(err.is_cancelled());

        // 2. Cancelled inside final progress callback -> must NOT promote `.part` or leave leftovers.
        let base_url = spawn_file_server(vec![("ggml-tiny.bin", vec![9_u8; 2048])]).await;
        let downloader = ModelDownloader::with_base_url(base_url).unwrap();
        let cancel = Arc::new(AtomicBool::new(false));
        let cancel_cb = Arc::clone(&cancel);

        let err = downloader
            .ensure_downloaded(&cache, tiny, &cancel, move |p| {
                if p.bytes_downloaded > 0 {
                    cancel_cb.store(true, Ordering::Release);
                }
            })
            .await
            .unwrap_err();
        assert!(err.is_cancelled());
        assert!(cache.find_installed(tiny).is_none());

        let leftovers: Vec<_> = std::fs::read_dir(cache.model_dir(tiny))
            .unwrap()
            .filter_map(Result::ok)
            .collect();
        assert!(
            leftovers.is_empty(),
            "expected no promoted or .part files after cancel, found {leftovers:?}"
        );
    }

    #[tokio::test]
    async fn concurrent_waiter_receives_peer_progress_without_starting_second_transfer() {
        let dir = TempDir::new("peer-progress");
        let cache = ModelCache::new(dir.0.join("models"), None);
        let tiny = resolve_model_spec(Some("tiny")).unwrap();

        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let request_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let request_count_srv = Arc::clone(&request_count);
        let first_req_started = Arc::new(tokio::sync::Notify::new());
        let first_req_notify = Arc::clone(&first_req_started);
        let release_second_chunk = Arc::new(tokio::sync::Notify::new());
        let release_srv = Arc::clone(&release_second_chunk);

        let server = tokio::spawn(async move {
            let Ok((mut socket, _)) = listener.accept().await else {
                return;
            };
            request_count_srv.fetch_add(1, Ordering::SeqCst);
            let mut req_buf = [0_u8; 4096];
            let _ = socket.read(&mut req_buf).await;
            let chunk1 = vec![7_u8; 64 * 1024];
            let chunk2 = vec![8_u8; 64 * 1024];
            let header = format!(
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                chunk1.len() + chunk2.len()
            );
            let _ = socket.write_all(header.as_bytes()).await;
            let _ = socket.write_all(&chunk1).await;
            let _ = socket.flush().await;
            first_req_notify.notify_one();
            release_srv.notified().await;
            let _ = socket.write_all(&chunk2).await;
            let _ = socket.shutdown().await;
        });

        let downloader = ModelDownloader::with_base_url(format!("http://{addr}")).unwrap();
        let cancel1 = Arc::new(AtomicBool::new(false));
        let cancel2 = Arc::new(AtomicBool::new(false));

        let owner_started = Arc::new(AtomicBool::new(false));
        let owner_started_cb = Arc::clone(&owner_started);
        let dl1 = downloader.clone();
        let cache1 = cache.clone();
        let c1 = Arc::clone(&cancel1);
        let task1 = tokio::spawn(async move {
            dl1.ensure_downloaded_observing(
                &cache1,
                tiny,
                &c1,
                move || {
                    owner_started_cb.store(true, Ordering::Release);
                },
                |_p, from_peer| {
                    assert!(!from_peer, "owner must receive from_peer == false");
                },
            )
            .await
        });

        first_req_started.notified().await;

        let waiter_started = Arc::new(AtomicBool::new(false));
        let waiter_started_cb = Arc::clone(&waiter_started);
        let peer_ticks = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let peer_ticks_cb = Arc::clone(&peer_ticks);
        let dl2 = downloader.clone();
        let cache2 = cache.clone();
        let c2 = Arc::clone(&cancel2);
        let task2 = tokio::spawn(async move {
            dl2.ensure_downloaded_observing(
                &cache2,
                tiny,
                &c2,
                move || {
                    waiter_started_cb.store(true, Ordering::Release);
                },
                |_p, from_peer| {
                    assert!(from_peer, "waiter must receive from_peer == true");
                    peer_ticks_cb.fetch_add(1, Ordering::SeqCst);
                },
            )
            .await
        });

        // Wait until task2 has subscribed to the slot's progress broadcast channel.
        let slot = downloader.slot_for(cache.models_dir(), tiny.id);
        tokio::time::timeout(Duration::from_secs(2), async {
            while slot.progress_tx.receiver_count() == 0 {
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .unwrap();

        release_second_chunk.notify_one();

        let (res1, res2) = tokio::join!(task1, task2);
        let installed1 = res1.unwrap().unwrap();
        let installed2 = res2.unwrap().unwrap();
        assert_eq!(installed1, installed2);
        assert_eq!(request_count.load(Ordering::SeqCst), 1);
        assert!(owner_started.load(Ordering::Acquire));
        assert!(
            !waiter_started.load(Ordering::Acquire),
            "peer waiter should reuse completed install without starting a second transfer"
        );
        assert!(
            peer_ticks.load(Ordering::SeqCst) >= 1,
            "peer waiter should receive forwarded progress ticks while waiting on the slot lock"
        );
        let _ = server.await;
    }
}
