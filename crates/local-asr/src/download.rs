//! Streaming model weight downloader with byte-level progress, cooperative cancellation,
//! and atomic `.part` file promotion.

use std::fmt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::Duration;

use futures_util::StreamExt;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use tokio::io::AsyncWriteExt;

use crate::cache::{verify_all_files_present, InstalledModel, ModelCache, ModelInstallSource};
use crate::catalog::{
    build_hf_resolve_url, validate_safe_filename, ModelSpec, DEFAULT_HF_BASE_URL,
};
use crate::LocalAsrError;

const DEFAULT_CONNECT_TIMEOUT: Duration = Duration::from_secs(20);
const DEFAULT_READ_TIMEOUT: Duration = Duration::from_secs(60);

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

/// Streaming HTTPS downloader for Hugging Face model weights.
#[derive(Clone)]
pub struct ModelDownloader {
    client: Client,
    hf_base_url: String,
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
            .map_err(|err| LocalAsrError::Download(format!("HTTP client init failed: {err}")))?;
        Ok(Self {
            client,
            hf_base_url: base_url.into(),
        })
    }

    /// Ensure all files for `spec` are cached on disk, downloading missing files into
    /// `cache.model_dir(spec)` with progress callbacks and cooperative cancellation.
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

        let target_dir = cache.model_dir(spec);
        tokio::fs::create_dir_all(&target_dir).await?;

        let total_files = spec.files.len();
        for (idx, &filename) in spec.files.iter().enumerate() {
            validate_safe_filename(filename)?;
            if cancel_flag.load(Ordering::Acquire) {
                return Err(LocalAsrError::Cancelled);
            }

            let final_path = target_dir.join(filename);
            if is_non_empty_file(&final_path).await {
                let len = tokio::fs::metadata(&final_path)
                    .await
                    .map(|m| m.len())
                    .unwrap_or(0);
                on_progress(DownloadProgress {
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
                &mut on_progress,
            )
            .await?;
        }

        let verify_dir = target_dir.clone();
        let files = spec.files;
        let total_bytes =
            tokio::task::spawn_blocking(move || verify_all_files_present(&verify_dir, files))
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
        let response = self.client.get(url).send().await.map_err(|err| {
            let reason = if err.is_timeout() {
                "timed out"
            } else if err.is_connect() {
                "connection failed"
            } else {
                "request failed"
            };
            LocalAsrError::Download(format!("{reason} while downloading '{filename}'"))
        })?;
        let status = response.status();
        if !status.is_success() {
            return Err(LocalAsrError::Download(format!(
                "HTTP {status} while downloading '{filename}'"
            )));
        }

        let total_bytes = response.content_length();
        let seq = PART_FILE_SEQ.fetch_add(1, Ordering::Relaxed);
        let part_path =
            final_path.with_file_name(format!(".{filename}.part.{}.{seq}", std::process::id()));

        let mut part_guard = PartFileGuard::new(part_path.clone());
        let mut file = tokio::fs::File::create(&part_path).await?;
        let mut stream = response.bytes_stream();
        let mut bytes_downloaded = 0_u64;

        on_progress(DownloadProgress {
            model_id: model_id.to_owned(),
            file: filename.to_owned(),
            file_index,
            total_files,
            bytes_downloaded: 0,
            total_bytes,
        });

        while let Some(chunk_res) = stream.next().await {
            if cancel_flag.load(Ordering::Acquire) {
                drop(file);
                part_guard.remove_async().await;
                return Err(LocalAsrError::Cancelled);
            }
            let chunk = chunk_res.map_err(|err| {
                LocalAsrError::Download(format!("stream read failed for '{filename}': {err}"))
            })?;
            file.write_all(&chunk).await?;
            bytes_downloaded = bytes_downloaded.saturating_add(chunk.len() as u64);
            on_progress(DownloadProgress {
                model_id: model_id.to_owned(),
                file: filename.to_owned(),
                file_index,
                total_files,
                bytes_downloaded,
                total_bytes,
            });
        }

        if bytes_downloaded == 0 {
            drop(file);
            part_guard.remove_async().await;
            return Err(LocalAsrError::Download(format!(
                "downloaded '{filename}' was empty"
            )));
        }
        if let Some(expected) = total_bytes {
            if bytes_downloaded != expected {
                drop(file);
                part_guard.remove_async().await;
                return Err(LocalAsrError::Download(format!(
                    "truncated download for '{filename}' ({bytes_downloaded}/{expected} bytes)"
                )));
            }
        }

        file.flush().await?;
        drop(file);

        // Windows cannot overwrite an existing file with `tokio::fs::rename`; remove stale target first.
        if cfg!(windows) && tokio::fs::try_exists(final_path).await.unwrap_or(false) {
            let _ = tokio::fs::remove_file(final_path).await;
        }
        tokio::fs::rename(&part_path, final_path).await?;
        part_guard.disarm();
        Ok(())
    }
}

async fn is_non_empty_file(path: &Path) -> bool {
    tokio::fs::metadata(path)
        .await
        .is_ok_and(|meta| meta.is_file() && meta.len() > 0)
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
                handle.spawn(async move {
                    let _ = tokio::fs::remove_file(&path).await;
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
                        .write_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
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
    async fn honors_pre_cancelled_flag_and_leaves_no_partial_files() {
        let dir = TempDir::new("cancel");
        let cache = ModelCache::new(dir.0.join("models"), None);
        let tiny = resolve_model_spec(Some("tiny")).unwrap();
        let downloader = ModelDownloader::with_base_url("http://127.0.0.1:1").unwrap();
        let cancel = AtomicBool::new(true);

        let err = downloader
            .ensure_downloaded(&cache, tiny, &cancel, |_| {})
            .await
            .unwrap_err();
        assert!(err.is_cancelled());
        assert!(cache.find_installed(tiny).is_none());
    }
}
