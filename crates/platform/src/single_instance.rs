//! Per-config-dir OS single-instance lock with second-launch activation.
//!
//! - **Unix (macOS/Linux):** Binds a `0600` Unix domain socket (`pulsescribe.sock`, falling back
//!   to `$TMPDIR` when `config_dir` approaches macOS's 104-byte `SUN_LEN` limit). A second launch
//!   connects, sends `"open\n"` to focus the running instance's window, and exits cleanly.
//! - **Windows:** Acquires a per-config-dir named kernel mutex (`Local\PulseScribeSingleInstance_<hash>`)
//!   paired with a latched auto-reset activation event (`Local\PulseScribeSingleInstanceActivate_<hash>`).
//!   A second launch signals the activation event and exits cleanly; the kernel preserves early signals
//!   until the primary instance starts listening and releases all objects automatically on crash.

use std::path::Path;

/// Result of attempting to acquire the desktop single-instance lock.
pub enum SingleInstanceOutcome {
    /// Lock acquired; keep the guard alive for the lifetime of the desktop process.
    Acquired(SingleInstanceGuard),
    /// Another PulseScribe instance already owns this `config_dir` and has been signaled to activate.
    AlreadyRunning,
}

/// Deterministic 64-bit FNV-1a hash used to scope single-instance names to `config_dir`.
pub(crate) fn hash_config_dir(config_dir: &Path) -> u64 {
    let normalized = std::fs::canonicalize(config_dir).unwrap_or_else(|_| config_dir.to_path_buf());
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in normalized.as_os_str().as_encoded_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash
}

#[cfg(unix)]
mod imp {
    use std::ffi::OsString;
    use std::fs::{self, OpenOptions};
    use std::io::{self, Read, Write};
    use std::os::unix::fs::{FileTypeExt, MetadataExt, OpenOptionsExt, PermissionsExt};
    use std::os::unix::net::{UnixListener, UnixStream};
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;
    use std::thread::{self, JoinHandle};
    use std::time::{Duration, SystemTime};

    use super::{hash_config_dir, SingleInstanceOutcome};
    use crate::PlatformError;

    /// Conservative ceiling below macOS's 104-byte `sockaddr_un.sun_path` limit.
    const MAX_UNIX_SOCKET_PATH_BYTES: usize = 96;
    const SOCKET_IO_TIMEOUT: Duration = Duration::from_millis(250);
    const STALE_CLEANUP_LOCK_AGE: Duration = Duration::from_secs(5);
    const OPEN_CMD: &[u8] = b"open\n";
    const WAKE_CMD: &[u8] = b"quit\n";

    fn resolve_socket_path(config_dir: &Path) -> PathBuf {
        let direct = config_dir.join("pulsescribe.sock");
        if direct.as_os_str().len() <= MAX_UNIX_SOCKET_PATH_BYTES {
            return direct;
        }
        let hash = hash_config_dir(config_dir);
        std::env::temp_dir().join(format!("pulsescribe-{hash:016x}.sock"))
    }

    fn cleanup_lock_path(socket_path: &Path) -> PathBuf {
        let mut s = OsString::from(socket_path.as_os_str());
        s.push(".lock");
        PathBuf::from(s)
    }

    fn is_existing_socket(path: &Path) -> bool {
        fs::symlink_metadata(path)
            .map(|meta| meta.file_type().is_socket())
            .unwrap_or(false)
    }

    fn socket_inode(path: &Path) -> Option<u64> {
        let meta = fs::symlink_metadata(path).ok()?;
        meta.file_type().is_socket().then_some(meta.ino())
    }

    fn signal_running_instance(path: &Path) -> bool {
        let Ok(mut stream) = UnixStream::connect(path) else {
            return false;
        };
        let _ = stream.set_write_timeout(Some(SOCKET_IO_TIMEOUT));
        let _ = stream.write_all(OPEN_CMD);
        let _ = stream.flush();
        true
    }

    /// Short-lived atomic `O_CREAT | O_EXCL` guard serializing stale socket recovery so two
    /// concurrently starting processes can never unlink each other's freshly bound socket.
    struct StaleCleanupLock(PathBuf);

    impl StaleCleanupLock {
        fn try_acquire(socket_path: &Path) -> Option<Self> {
            let lock_path = cleanup_lock_path(socket_path);
            let try_create = || {
                OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .mode(0o600)
                    .open(&lock_path)
            };
            match try_create() {
                Ok(_) => Some(Self(lock_path)),
                Err(err) if err.kind() == io::ErrorKind::AlreadyExists => {
                    // Reclaim only if the lockfile was abandoned by a crashed process.
                    let is_stale = fs::symlink_metadata(&lock_path)
                        .ok()
                        .and_then(|m| m.modified().ok())
                        .and_then(|t| SystemTime::now().duration_since(t).ok())
                        .is_some_and(|age| age > STALE_CLEANUP_LOCK_AGE);
                    if is_stale {
                        let _ = fs::remove_file(&lock_path);
                        try_create().ok().map(|_| Self(lock_path))
                    } else {
                        None
                    }
                }
                Err(_) => None,
            }
        }
    }

    impl Drop for StaleCleanupLock {
        fn drop(&mut self) {
            let _ = fs::remove_file(&self.0);
        }
    }

    pub struct SingleInstanceGuard {
        socket_path: PathBuf,
        bound_inode: u64,
        listener: Option<UnixListener>,
        shutdown: Arc<AtomicBool>,
        worker: Option<JoinHandle<()>>,
    }

    impl SingleInstanceGuard {
        pub fn acquire(config_dir: &Path) -> Result<SingleInstanceOutcome, PlatformError> {
            fs::create_dir_all(config_dir)?;
            let _ = fs::set_permissions(config_dir, fs::Permissions::from_mode(0o700));

            let socket_path = resolve_socket_path(config_dir);
            if let Some(parent) = socket_path.parent() {
                fs::create_dir_all(parent)?;
            }

            // Always attempt atomic kernel `bind()` first — never unlink before probing.
            for attempt in 0..4 {
                match UnixListener::bind(&socket_path) {
                    Ok(listener) => {
                        let _ =
                            fs::set_permissions(&socket_path, fs::Permissions::from_mode(0o600));
                        let bound_inode = socket_inode(&socket_path).unwrap_or(0);
                        return Ok(SingleInstanceOutcome::Acquired(Self {
                            socket_path,
                            bound_inode,
                            listener: Some(listener),
                            shutdown: Arc::new(AtomicBool::new(false)),
                            worker: None,
                        }));
                    }
                    Err(err) if err.kind() == io::ErrorKind::AddrInUse => {
                        if signal_running_instance(&socket_path) {
                            return Ok(SingleInstanceOutcome::AlreadyRunning);
                        }
                        // Serialize stale socket removal with an atomic O_CREAT|O_EXCL lockfile,
                        // and re-verify `connect` while holding it so a live peer is never unlinked.
                        if let Some(_cleanup_guard) = StaleCleanupLock::try_acquire(&socket_path) {
                            if signal_running_instance(&socket_path) {
                                return Ok(SingleInstanceOutcome::AlreadyRunning);
                            }
                            if is_existing_socket(&socket_path) {
                                let _ = fs::remove_file(&socket_path);
                            }
                        } else if attempt < 3 {
                            thread::sleep(Duration::from_millis(25));
                        } else {
                            return Err(PlatformError::Io(err));
                        }
                    }
                    Err(err) => return Err(PlatformError::Io(err)),
                }
            }

            if signal_running_instance(&socket_path) {
                return Ok(SingleInstanceOutcome::AlreadyRunning);
            }
            Err(PlatformError::Io(io::Error::new(
                io::ErrorKind::AddrInUse,
                "failed to bind single-instance socket after retries",
            )))
        }

        /// Spawn a zero-polling listener thread invoking `on_activate` when a second launch connects.
        pub fn start_activation_listener<F>(&mut self, on_activate: F)
        where
            F: Fn() + Send + 'static,
        {
            if self.worker.is_some() {
                return;
            }
            let Some(listener) = self.listener.take() else {
                return;
            };
            let shutdown = Arc::clone(&self.shutdown);
            let handle = thread::Builder::new()
                .name("pulsescribe-instance".to_owned())
                .spawn(move || {
                    for incoming in listener.incoming() {
                        if shutdown.load(Ordering::Acquire) {
                            break;
                        }
                        let Ok(mut stream) = incoming else {
                            continue;
                        };
                        let _ = stream.set_read_timeout(Some(SOCKET_IO_TIMEOUT));
                        let mut buf = [0u8; 32];
                        let n = stream.read(&mut buf).unwrap_or(0);
                        if shutdown.load(Ordering::Acquire) {
                            break;
                        }
                        if n == 0 || buf[..n].starts_with(b"open") {
                            let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(
                                &on_activate,
                            ));
                        }
                    }
                });
            if let Ok(worker) = handle {
                self.worker = Some(worker);
            }
        }
    }

    impl Drop for SingleInstanceGuard {
        fn drop(&mut self) {
            self.shutdown.store(true, Ordering::Release);
            let owns_path =
                self.bound_inode != 0 && socket_inode(&self.socket_path) == Some(self.bound_inode);

            // Only self-connect, join, and unlink when our bound socket inode is still in place;
            // if externally deleted/replaced, avoid waking another instance or hanging in `accept()`.
            if owns_path {
                let mut woke = false;
                if self.worker.is_some() {
                    if let Ok(mut stream) = UnixStream::connect(&self.socket_path) {
                        let _ = stream.set_write_timeout(Some(SOCKET_IO_TIMEOUT));
                        let _ = stream.write_all(WAKE_CMD);
                        woke = true;
                    }
                }
                if woke {
                    if let Some(worker) = self.worker.take() {
                        let _ = worker.join();
                    }
                }
                self.listener.take();
                let _ = fs::remove_file(&self.socket_path);
            } else {
                self.listener.take();
            }
        }
    }
}

#[cfg(target_os = "windows")]
mod imp {
    #![allow(unsafe_code)]

    use std::ffi::OsStr;
    use std::os::windows::ffi::OsStrExt;
    use std::path::Path;
    use std::thread::{self, JoinHandle};

    use windows::core::PCWSTR;
    use windows::Win32::Foundation::{
        CloseHandle, GetLastError, ERROR_ALREADY_EXISTS, HANDLE, WAIT_EVENT, WAIT_OBJECT_0,
    };
    use windows::Win32::System::Threading::{
        CreateEventW, CreateMutexW, SetEvent, WaitForMultipleObjects, INFINITE,
    };

    use super::{hash_config_dir, SingleInstanceOutcome};
    use crate::PlatformError;

    struct OwnedHandle(HANDLE);

    // SAFETY: Win32 named mutex (`bInitialOwner = false`) and event handles are process-wide
    // kernel handles that may be signaled, waited on, and closed from any thread.
    unsafe impl Send for OwnedHandle {}

    impl Drop for OwnedHandle {
        fn drop(&mut self) {
            // SAFETY: `self.0` is a valid owned handle returned by `CreateMutexW`/`CreateEventW`.
            unsafe {
                let _ = CloseHandle(self.0);
            }
        }
    }

    #[derive(Clone, Copy)]
    struct SendHandle(HANDLE);

    impl SendHandle {
        fn raw(self) -> HANDLE {
            self.0
        }
    }

    // SAFETY: Non-owning copy passed exclusively to the activation listener thread, which is
    // joined in `SingleInstanceGuard::drop` before the owning `OwnedHandle`s are closed.
    unsafe impl Send for SendHandle {}

    fn encode_wide_nul(value: &str) -> Vec<u16> {
        OsStr::new(value)
            .encode_wide()
            .chain(std::iter::once(0))
            .collect()
    }

    pub struct SingleInstanceGuard {
        worker: Option<JoinHandle<()>>,
        shutdown_event: OwnedHandle,
        activate_event: OwnedHandle,
        _mutex: OwnedHandle,
    }

    impl SingleInstanceGuard {
        pub fn acquire(config_dir: &Path) -> Result<SingleInstanceOutcome, PlatformError> {
            let _ = std::fs::create_dir_all(config_dir);
            let hash = hash_config_dir(config_dir);
            let activate_name = encode_wide_nul(&format!(
                "Local\\PulseScribeSingleInstanceActivate_{hash:016x}"
            ));
            let mutex_name =
                encode_wide_nul(&format!("Local\\PulseScribeSingleInstance_{hash:016x}"));

            // Create/open the named auto-reset activation event BEFORE acquiring the mutex so
            // whichever process wins the mutex already holds an open handle to the event.
            // Any second launch that observes `ERROR_ALREADY_EXISTS` can immediately signal the
            // event and exit without racing against startup initialization.
            // SAFETY: `activate_name` is NUL-terminated for the duration of `CreateEventW`.
            let activate_event = OwnedHandle(
                unsafe { CreateEventW(None, false, false, PCWSTR(activate_name.as_ptr())) }
                    .map_err(|err| PlatformError::Io(std::io::Error::other(err.to_string())))?,
            );

            // SAFETY: `mutex_name` is NUL-terminated for the duration of `CreateMutexW`.
            let mutex = OwnedHandle(
                unsafe { CreateMutexW(None, false, PCWSTR(mutex_name.as_ptr())) }
                    .map_err(|err| PlatformError::Io(std::io::Error::other(err.to_string())))?,
            );

            // SAFETY: `GetLastError` immediately inspects the `CreateMutexW` status on this thread.
            let already_exists = unsafe { GetLastError() } == ERROR_ALREADY_EXISTS;
            if already_exists {
                // Signal the existing instance; because `activate_event` is auto-reset and kept
                // open by the primary instance, this signal is latched even during early startup.
                // SAFETY: `activate_event.0` is a valid event handle.
                unsafe {
                    let _ = SetEvent(activate_event.0);
                }
                return Ok(SingleInstanceOutcome::AlreadyRunning);
            }

            // Unnamed manual-reset event used to wake and join the activation listener cleanly.
            // SAFETY: Passing `None` creates an unnamed manual-reset event with no pointer args.
            let shutdown_event = OwnedHandle(
                unsafe { CreateEventW(None, true, false, None) }
                    .map_err(|err| PlatformError::Io(std::io::Error::other(err.to_string())))?,
            );

            Ok(SingleInstanceOutcome::Acquired(Self {
                worker: None,
                shutdown_event,
                activate_event,
                _mutex: mutex,
            }))
        }

        /// Spawn a zero-polling listener thread invoking `on_activate` whenever a second launch
        /// signals the per-`config_dir` activation event (including signals latched during startup).
        pub fn start_activation_listener<F>(&mut self, on_activate: F)
        where
            F: Fn() + Send + 'static,
        {
            if self.worker.is_some() {
                return;
            }
            let shutdown_h = SendHandle(self.shutdown_event.0);
            let activate_h = SendHandle(self.activate_event.0);
            let handle = thread::Builder::new()
                .name("pulsescribe-instance".to_owned())
                .spawn(move || {
                    let handles = [shutdown_h.raw(), activate_h.raw()];
                    let activate_wait = WAIT_EVENT(WAIT_OBJECT_0.0 + 1);
                    loop {
                        // SAFETY: Both handles remain valid until `SingleInstanceGuard::drop`
                        // signals `shutdown_event` and joins this thread.
                        let wait = unsafe { WaitForMultipleObjects(&handles, false, INFINITE) };
                        if wait == WAIT_OBJECT_0 {
                            break;
                        }
                        if wait == activate_wait {
                            let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(
                                &on_activate,
                            ));
                            continue;
                        }
                        break;
                    }
                });
            if let Ok(worker) = handle {
                self.worker = Some(worker);
            }
        }
    }

    impl Drop for SingleInstanceGuard {
        fn drop(&mut self) {
            // SAFETY: `shutdown_event.0` is a valid event handle owned by `self`.
            unsafe {
                let _ = SetEvent(self.shutdown_event.0);
            }
            if let Some(worker) = self.worker.take() {
                let _ = worker.join();
            }
            // Fields drop after `drop()` in declaration order: `shutdown_event`, `activate_event`,
            // then `_mutex` last so no replacement can acquire the mutex while teardown runs.
        }
    }
}

#[cfg(not(any(unix, target_os = "windows")))]
mod imp {
    use std::path::Path;

    use super::SingleInstanceOutcome;
    use crate::PlatformError;

    pub struct SingleInstanceGuard;

    impl SingleInstanceGuard {
        pub fn acquire(_config_dir: &Path) -> Result<SingleInstanceOutcome, PlatformError> {
            Ok(SingleInstanceOutcome::Acquired(Self))
        }

        pub fn start_activation_listener<F>(&mut self, _on_activate: F)
        where
            F: Fn() + Send + 'static,
        {
        }
    }
}

pub use imp::SingleInstanceGuard;

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;
    #[cfg(any(unix, target_os = "windows"))]
    use std::time::Duration;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_config_dir(tag: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("ps-si-{tag}-{}-{nanos}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn single_instance_blocks_duplicate_signals_existing_and_releases_on_drop() {
        let dir_a = temp_config_dir("a");
        let dir_b = temp_config_dir("b");

        let mut guard_a = match SingleInstanceGuard::acquire(&dir_a).unwrap() {
            SingleInstanceOutcome::Acquired(g) => g,
            SingleInstanceOutcome::AlreadyRunning => panic!("first acquire should succeed"),
        };

        let activations = Arc::new(AtomicUsize::new(0));
        let activations_clone = Arc::clone(&activations);
        guard_a.start_activation_listener(move || {
            activations_clone.fetch_add(1, Ordering::SeqCst);
        });

        // Distinct config dir must acquire independently.
        let guard_b = match SingleInstanceGuard::acquire(&dir_b).unwrap() {
            SingleInstanceOutcome::Acquired(g) => g,
            SingleInstanceOutcome::AlreadyRunning => panic!("distinct config dir should acquire"),
        };

        // Second launch against `dir_a` must detect the running instance.
        assert!(matches!(
            SingleInstanceGuard::acquire(&dir_a).unwrap(),
            SingleInstanceOutcome::AlreadyRunning
        ));

        #[cfg(any(unix, target_os = "windows"))]
        {
            for _ in 0..40 {
                if activations.load(Ordering::SeqCst) > 0 {
                    break;
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            assert_eq!(activations.load(Ordering::SeqCst), 1);
        }

        drop(guard_a);
        drop(guard_b);

        // After clean drop, `dir_a` can be acquired again.
        let reacquired = SingleInstanceGuard::acquire(&dir_a).unwrap();
        assert!(matches!(reacquired, SingleInstanceOutcome::Acquired(_)));
        drop(reacquired);

        let _ = fs::remove_dir_all(&dir_a);
        let _ = fs::remove_dir_all(&dir_b);
    }

    #[cfg(any(unix, target_os = "windows"))]
    #[test]
    fn early_activation_before_listener_start_is_preserved() {
        let dir = temp_config_dir("early");
        let mut guard = match SingleInstanceGuard::acquire(&dir).unwrap() {
            SingleInstanceOutcome::Acquired(g) => g,
            SingleInstanceOutcome::AlreadyRunning => panic!("first acquire should succeed"),
        };

        // Signal from a second launch BEFORE the listener thread starts.
        assert!(matches!(
            SingleInstanceGuard::acquire(&dir).unwrap(),
            SingleInstanceOutcome::AlreadyRunning
        ));

        let activations = Arc::new(AtomicUsize::new(0));
        let activations_clone = Arc::clone(&activations);
        guard.start_activation_listener(move || {
            activations_clone.fetch_add(1, Ordering::SeqCst);
        });

        for _ in 0..40 {
            if activations.load(Ordering::SeqCst) > 0 {
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(activations.load(Ordering::SeqCst), 1);

        drop(guard);
        let _ = fs::remove_dir_all(&dir);
    }
}
