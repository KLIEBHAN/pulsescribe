//! Verified update preparation, detached macOS/Windows installation helpers,
//! and the single-flight [`UpdateController`] background state machine.

use std::fs;
#[cfg(any(target_os = "macos", test))]
use std::fs::OpenOptions;
#[cfg(any(target_os = "windows", test))]
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
#[cfg(target_os = "macos")]
use std::time::{Duration, Instant};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::updates::{
    download_verified_asset, fetch_latest_offer, UpdateErrorContext, UpdateOffer, UpdatePhase,
    UpdateStateSnapshot, UpdaterHttpClient, IS_FULL_VARIANT,
};

#[cfg(target_os = "macos")]
const BUNDLE_IDENTIFIER: &str = "com.kliebhan.pulsescribe";

/// Detached POSIX shell helper waiting for the running PulseScribe PID to exit,
/// atomically swapping the verified `.app` bundle with automatic rollback on failure,
/// and relaunching the updated app.
#[cfg(any(target_os = "macos", test))]
pub const MAC_SWAP_SCRIPT: &str = r#"trap '' HUP
pid="$1"; new="$2"; target="$3"; work="$4"; opener="$5"
i=0
while kill -0 "$pid" 2>/dev/null; do
    i=$((i + 1))
    [ "$i" -gt 600 ] && exit 1
    /bin/sleep 0.1
done
backup="$work/previous.app"
failed() {
    printf '%s\n' "$1" > "$work/error.txt"
    "$opener" "$target"
    "$opener" "$work/error.txt"
    exit 1
}
/bin/mv "$target" "$backup" || failed "PulseScribe could not move the installed app. The update remains in $work."
if ! /bin/mv "$new" "$target"; then
    /bin/mv "$backup" "$target" || failed "PulseScribe could not restore the app. The previous app remains in $backup."
    failed "PulseScribe could not install the update. The previous app was restored."
fi
/bin/rm -rf "$backup"
"$opener" "$target"
/bin/rm -rf "$work"
"#;

/// Detached PowerShell helper waiting for the running PulseScribe PID to exit,
/// invoking the verified Tauri v2 NSIS installer with `/S /UPDATE /D=<dir>`,
/// and relaunching `pulsescribe-desktop.exe` even if the installer fails.
#[cfg(any(target_os = "windows", test))]
pub const WINDOWS_INSTALL_SCRIPT: &str = r#"param([int]$AppPid, [string]$Installer, [string]$Target)
$ErrorActionPreference = "Stop"
if (Get-Process -Id $AppPid -ErrorAction SilentlyContinue) {
    Wait-Process -Id $AppPid -Timeout 60
}
$directory = Split-Path -Parent $Target
$workDir = Split-Path -Parent $Installer
$succeeded = $false
try {
    $process = Start-Process -FilePath $Installer -ArgumentList "/S /UPDATE /D=$directory" -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Installer failed with exit code $($process.ExitCode)"
    }
    $succeeded = $true
} catch {
    $_ | Out-String | Set-Content -Path (Join-Path $workDir "error.txt") -Encoding utf8
} finally {
    Start-Process -FilePath $Target
}
if ($succeeded) {
    Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
}
"#;

/// Verified update artifact staged on disk and ready to launch.
#[derive(Debug, Clone)]
pub struct PreparedUpdate {
    pub package: PathBuf,
    pub target: PathBuf,
    pub work: PathBuf,
    pub automatic: bool,
}

impl PreparedUpdate {
    pub fn hint_key(&self) -> &'static str {
        if self.automatic {
            "automatic"
        } else {
            "manual_dmg"
        }
    }
}

fn unique_suffix() -> String {
    static SEQ: AtomicU64 = AtomicU64::new(0);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let seq = SEQ.fetch_add(1, Ordering::Relaxed);
    format!("{}-{nanos}-{seq}", std::process::id())
}

fn create_unique_dir(parent: &Path, prefix: &str) -> Result<PathBuf, String> {
    let dir = parent.join(format!("{prefix}{}", unique_suffix()));
    fs::create_dir(&dir).map_err(|err| {
        format!(
            "Could not create staging directory {}: {err}",
            dir.display()
        )
    })?;
    Ok(dir)
}

/// Detect the installed application target that an in-app update is allowed to replace.
///
/// Returns `None` for source/dev runs (`cargo run`, `cargo test`) so development builds never
/// overwrite an installed application or trigger unsolicited background downloads.
pub fn detect_installed_target() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?.canonicalize().ok()?;
    detect_installed_target_from_exe(&exe)
}

pub fn detect_installed_target_from_exe(executable: &Path) -> Option<PathBuf> {
    #[cfg(target_os = "macos")]
    {
        let macos_dir = executable.parent()?;
        if macos_dir.file_name()?.to_str()? != "MacOS" {
            return None;
        }
        let contents_dir = macos_dir.parent()?;
        if contents_dir.file_name()?.to_str()? != "Contents" {
            return None;
        }
        let bundle = contents_dir.parent()?;
        if bundle.file_name()?.to_str()? == "PulseScribe.app"
            && contents_dir.join("Info.plist").is_file()
        {
            return Some(bundle.to_path_buf());
        }
        None
    }

    #[cfg(target_os = "windows")]
    {
        let file_name = executable.file_name()?.to_str()?;
        if !file_name.eq_ignore_ascii_case("pulsescribe-desktop.exe") {
            return None;
        }
        let dir = executable.parent()?;
        if dir.join("uninstall.exe").is_file() {
            return Some(executable.to_path_buf());
        }
        None
    }

    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = executable;
        None
    }
}

/// Return `true` when `bundle` is an installed macOS `.app` bundle that can be replaced in place.
#[cfg(any(target_os = "macos", test))]
pub fn can_replace_mac(bundle: &Path) -> bool {
    let path_str = bundle.to_string_lossy();
    if path_str.starts_with("/Volumes/") || path_str.contains("/AppTranslocation/") {
        return false;
    }
    let Some(parent) = bundle.parent() else {
        return false;
    };
    let Ok(meta) = fs::metadata(bundle) else {
        return false;
    };
    if meta.permissions().readonly() {
        return false;
    }
    // Probe real filesystem/ACL writability on the parent directory without mutating the signed bundle.
    let probe = parent.join(format!(".pulsescribe-write-check-{}", unique_suffix()));
    match OpenOptions::new().write(true).create_new(true).open(&probe) {
        Ok(_) => {
            let _ = fs::remove_file(&probe);
            true
        }
        Err(_) => false,
    }
}

/// Run a child process with timeout and cooperative cancellation (terminating only the known child PID).
/// Drains stdout/stderr concurrently on background threads so large output can never deadlock a pipe.
#[cfg(target_os = "macos")]
fn run_child_checked(
    program: &str,
    args: &[&str],
    timeout: Duration,
    cancelled: Option<&AtomicBool>,
) -> Result<String, String> {
    use std::io::Read as _;

    let mut child = Command::new(program)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|err| format!("Failed to start {program}: {err}"))?;

    let stdout_handle = child.stdout.take().map(|mut pipe| {
        std::thread::spawn(move || {
            let mut buf = Vec::new();
            let _ = pipe.read_to_end(&mut buf);
            buf
        })
    });
    let stderr_handle = child.stderr.take().map(|mut pipe| {
        std::thread::spawn(move || {
            let mut buf = Vec::new();
            let _ = pipe.read_to_end(&mut buf);
            buf
        })
    });

    let deadline = Instant::now() + timeout;
    let status = loop {
        if cancelled.is_some_and(|c| c.load(Ordering::Acquire)) {
            let _ = child.kill();
            let _ = child.wait();
            return Err("Update preparation cancelled.".to_owned());
        }
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(format!("{program} timed out after {}s.", timeout.as_secs()));
                }
                std::thread::sleep(Duration::from_millis(100));
            }
            Err(err) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!("Error waiting for {program}: {err}"));
            }
        }
    };

    let stdout_bytes = stdout_handle
        .and_then(|h| h.join().ok())
        .unwrap_or_default();
    let stderr_bytes = stderr_handle
        .and_then(|h| h.join().ok())
        .unwrap_or_default();
    if status.success() {
        return Ok(String::from_utf8_lossy(&stdout_bytes).trim().to_owned());
    }
    let stderr = String::from_utf8_lossy(&stderr_bytes).trim().to_owned();
    Err(format!("{program} exited with {status}: {stderr}"))
}

#[cfg(target_os = "macos")]
struct DmgMountGuard {
    mount_point: PathBuf,
    detached: bool,
}

#[cfg(target_os = "macos")]
impl DmgMountGuard {
    fn detach_now(&mut self) -> Result<(), String> {
        if self.detached {
            return Ok(());
        }
        self.detached = true;
        let mount_str = self.mount_point.to_string_lossy().into_owned();
        let mut last_err = String::new();
        for attempt in 0..3 {
            match run_child_checked(
                "/usr/bin/hdiutil",
                &["detach", &mount_str],
                Duration::from_secs(30),
                None,
            ) {
                Ok(_) => return Ok(()),
                Err(err) => {
                    last_err = err;
                    if attempt < 2 {
                        std::thread::sleep(Duration::from_secs(1));
                    }
                }
            }
        }
        Err(last_err)
    }
}

#[cfg(target_os = "macos")]
impl Drop for DmgMountGuard {
    fn drop(&mut self) {
        let _ = self.detach_now();
    }
}

/// Validate and stage a verified update package for macOS or Windows.
pub fn prepare_update(
    offer: &UpdateOffer,
    package: &Path,
    target: &Path,
    cancelled: &AtomicBool,
) -> Result<PreparedUpdate, String> {
    let work = package
        .parent()
        .ok_or_else(|| "Update package has no parent directory.".to_owned())?
        .to_path_buf();

    #[cfg(target_os = "macos")]
    {
        if !can_replace_mac(target) {
            return Ok(PreparedUpdate {
                package: package.to_path_buf(),
                target: target.to_path_buf(),
                work,
                automatic: false,
            });
        }
        let target_parent = target
            .parent()
            .ok_or_else(|| "Installed app has no parent directory.".to_owned())?;
        let mount = work.join("mount");
        fs::create_dir(&mount).map_err(|err| format!("Could not create DMG mount point: {err}"))?;
        let staged = create_unique_dir(target_parent, ".PulseScribe-update-")?;

        let prep = (|| -> Result<(), String> {
            let mount_str = mount.to_string_lossy().into_owned();
            let pkg_str = package.to_string_lossy().into_owned();
            // Arm the detach guard BEFORE invoking `hdiutil attach` so cancellation or timeout
            // after macOS mounts the volume still detaches cleanly on drop.
            let mut guard = DmgMountGuard {
                mount_point: mount.clone(),
                detached: false,
            };
            run_child_checked(
                "/usr/bin/hdiutil",
                &[
                    "attach",
                    "-readonly",
                    "-nobrowse",
                    "-mountpoint",
                    &mount_str,
                    &pkg_str,
                ],
                Duration::from_secs(120),
                Some(cancelled),
            )?;

            let mounted_app = mount.join("PulseScribe.app");
            let plist = mounted_app.join("Contents/Info.plist");
            let bin = mounted_app.join("Contents/MacOS/pulsescribe-desktop");
            if !mounted_app.is_dir() || !bin.is_file() || !plist.is_file() {
                return Err("The update image does not contain PulseScribe.app.".to_owned());
            }

            let plist_str = plist.to_string_lossy().into_owned();
            let bundle_id = run_child_checked(
                "/usr/bin/plutil",
                &[
                    "-extract",
                    "CFBundleIdentifier",
                    "raw",
                    "-o",
                    "-",
                    &plist_str,
                ],
                Duration::from_secs(15),
                Some(cancelled),
            )?;
            let bundle_ver = run_child_checked(
                "/usr/bin/plutil",
                &[
                    "-extract",
                    "CFBundleShortVersionString",
                    "raw",
                    "-o",
                    "-",
                    &plist_str,
                ],
                Duration::from_secs(15),
                Some(cancelled),
            )?;
            if bundle_id != BUNDLE_IDENTIFIER || bundle_ver != offer.version {
                return Err(format!(
                    "The update contains an unexpected app or version ({bundle_id} {bundle_ver})."
                ));
            }

            let mounted_str = mounted_app.to_string_lossy().into_owned();
            run_child_checked(
                "/usr/bin/codesign",
                &["--verify", "--deep", "--strict", &mounted_str],
                Duration::from_secs(60),
                Some(cancelled),
            )?;

            let staged_app = staged.join("PulseScribe.app");
            let staged_app_str = staged_app.to_string_lossy().into_owned();
            run_child_checked(
                "/usr/bin/ditto",
                &[&mounted_str, &staged_app_str],
                Duration::from_secs(300),
                Some(cancelled),
            )?;

            guard.detach_now()?;
            if cancelled.load(Ordering::Acquire) {
                return Err("Update preparation cancelled.".to_owned());
            }
            Ok(())
        })();

        if let Err(err) = prep {
            let _ = fs::remove_dir_all(&staged);
            return Err(err);
        }

        Ok(PreparedUpdate {
            package: staged.join("PulseScribe.app"),
            target: target.to_path_buf(),
            work: staged,
            automatic: true,
        })
    }

    #[cfg(target_os = "windows")]
    {
        let _ = (offer, cancelled);
        verify_windows_pe_installer(package)?;
        Ok(PreparedUpdate {
            package: package.to_path_buf(),
            target: target.to_path_buf(),
            work,
            automatic: true,
        })
    }

    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = (offer, package, target, work, cancelled);
        Err("In-app updates are not supported on this platform.".to_owned())
    }
}

/// Basic PE header sanity check for verified Windows NSIS setup executables.
#[cfg(any(target_os = "windows", test))]
pub fn verify_windows_pe_installer(path: &Path) -> Result<(), String> {
    let meta =
        fs::metadata(path).map_err(|err| format!("Could not inspect Windows installer: {err}"))?;
    if meta.len() < 100_000 {
        return Err("Windows installer file is unexpectedly small.".to_owned());
    }
    let mut file =
        fs::File::open(path).map_err(|err| format!("Could not open Windows installer: {err}"))?;
    let mut magic = [0u8; 2];
    file.read_exact(&mut magic)
        .map_err(|err| format!("Could not read Windows installer header: {err}"))?;
    if magic != [0x4D, 0x5A] {
        return Err("Downloaded Windows installer is not a valid PE executable.".to_owned());
    }
    Ok(())
}

/// Spawn the detached platform helper to install `prepared` after this process exits.
pub fn launch_prepared_update(prepared: &PreparedUpdate) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        if !prepared.automatic {
            let home = std::env::var_os("HOME")
                .map(PathBuf::from)
                .ok_or_else(|| "Could not determine user home directory.".to_owned())?;
            let downloads = home.join("Downloads");
            fs::create_dir_all(&downloads)
                .map_err(|err| format!("Could not access Downloads folder: {err}"))?;
            let base_name = prepared
                .package
                .file_name()
                .ok_or_else(|| "Invalid update package filename.".to_owned())?;
            let mut dest = downloads.join(base_name);
            if dest.exists() {
                let stem = prepared
                    .package
                    .file_stem()
                    .and_then(|s| s.to_str())
                    .unwrap_or("PulseScribe-Update");
                dest = downloads.join(format!("{stem}-{}.dmg", unique_suffix()));
            }
            // Rename when same volume, otherwise copy + unlink so moving across filesystems works.
            if fs::rename(&prepared.package, &dest).is_err() {
                fs::copy(&prepared.package, &dest)
                    .map_err(|err| format!("Could not copy installer to Downloads: {err}"))?;
                let _ = fs::remove_file(&prepared.package);
            }
            Command::new("/usr/bin/open")
                .arg(&dest)
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .map_err(|err| {
                    format!(
                        "Could not open verified installer ({err}). Open it manually at {}.",
                        dest.display()
                    )
                })?;
            return Ok(());
        }

        Command::new("/bin/sh")
            .arg("-c")
            .arg(MAC_SWAP_SCRIPT)
            .arg("pulsescribe-update")
            .arg(std::process::id().to_string())
            .arg(&prepared.package)
            .arg(&prepared.target)
            .arg(&prepared.work)
            .arg("/usr/bin/open")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|err| format!("Could not launch macOS update helper: {err}"))?;
        Ok(())
    }

    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;

        let target_dir = prepared
            .target
            .parent()
            .ok_or_else(|| "Installed executable has no parent directory.".to_owned())?;
        let target_dir_str = target_dir.to_string_lossy();
        if target_dir_str.contains('"')
            || target_dir_str.contains('\n')
            || target_dir_str.contains('\r')
        {
            return Err("Installation path contains unsupported characters.".to_owned());
        }

        let script_path = prepared.work.join("install.ps1");
        fs::write(&script_path, WINDOWS_INSTALL_SCRIPT)
            .map_err(|err| format!("Could not write Windows update helper script: {err}"))?;

        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        const DETACHED_PROCESS: u32 = 0x0000_0008;
        Command::new("powershell.exe")
            .args([
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
            ])
            .arg(&script_path)
            .args(["-AppPid", &std::process::id().to_string(), "-Installer"])
            .arg(&prepared.package)
            .arg("-Target")
            .arg(&prepared.target)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(CREATE_NO_WINDOW | DETACHED_PROCESS)
            .spawn()
            .map_err(|err| format!("Could not launch Windows update helper: {err}"))?;
        Ok(())
    }

    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = prepared;
        Err("In-app updates are not supported on this platform.".to_owned())
    }
}

struct UpdateInner {
    snapshot: UpdateStateSnapshot,
    offer: Option<UpdateOffer>,
    prepared: Option<PreparedUpdate>,
    work_dir: Option<PathBuf>,
    in_flight: bool,
    installing: bool,
}

type StateListener = Arc<dyn Fn(UpdateStateSnapshot) + Send + Sync>;

/// Thread-safe, single-flight update coordinator shared across tray, background timer, and IPC.
pub struct UpdateController {
    target: Option<PathBuf>,
    client: Result<UpdaterHttpClient, String>,
    inner: Mutex<UpdateInner>,
    stopped: Arc<AtomicBool>,
}

impl UpdateController {
    /// Construct an update controller for the current binary (`detect_installed_target()`).
    pub fn new(current_version: impl Into<String>) -> Self {
        Self::with_target(current_version, detect_installed_target())
    }

    /// Construct an update controller with an explicit target (used by tests).
    pub fn with_target(current_version: impl Into<String>, target: Option<PathBuf>) -> Self {
        let can_install = target.is_some();
        let client = UpdaterHttpClient::new();
        Self {
            target,
            client,
            inner: Mutex::new(UpdateInner {
                snapshot: UpdateStateSnapshot::idle(current_version, can_install),
                offer: None,
                prepared: None,
                work_dir: None,
                in_flight: false,
                installing: false,
            }),
            stopped: Arc::new(AtomicBool::new(false)),
        }
    }

    pub fn has_installed_target(&self) -> bool {
        self.target.is_some()
    }

    pub fn snapshot(&self) -> UpdateStateSnapshot {
        self.inner
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .snapshot
            .clone()
    }

    fn emit_change(
        &self,
        on_change: &StateListener,
        mutate: impl FnOnce(&mut UpdateStateSnapshot),
    ) {
        if self.stopped.load(Ordering::Acquire) {
            return;
        }
        let snapshot = {
            let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
            mutate(&mut guard.snapshot);
            guard.snapshot.clone()
        };
        on_change(snapshot);
    }

    fn discard_work_locked(guard: &mut UpdateInner) {
        if let Some(work) = guard.work_dir.take() {
            let _ = fs::remove_dir_all(work);
        }
        guard.prepared = None;
    }

    /// Check GitHub Releases for a newer version; when automatic replacement is supported for the
    /// installed app, immediately streams, verifies, and stages the update in the background.
    pub async fn run_check(&self, manual: bool, on_change: StateListener) -> UpdateStateSnapshot {
        {
            let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
            if self.stopped.load(Ordering::Acquire)
                || guard.in_flight
                || matches!(
                    guard.snapshot.status,
                    UpdatePhase::Downloading | UpdatePhase::Preparing | UpdatePhase::Ready
                )
            {
                return guard.snapshot.clone();
            }
            guard.in_flight = true;
        }
        let _flight = InFlightGuard { controller: self };

        let current_version = self.snapshot().current_version;
        self.emit_change(&on_change, |s| {
            s.status = UpdatePhase::Checking;
            s.manual = manual;
            s.error = None;
            s.error_context = None;
        });

        let client = match self.client.as_ref() {
            Ok(c) => c,
            Err(err) => {
                self.emit_change(&on_change, |s| {
                    s.status = UpdatePhase::Error;
                    s.error = Some(err.clone());
                    s.error_context = Some(UpdateErrorContext::Check);
                });
                return self.snapshot();
            }
        };

        let check_res = fetch_latest_offer(
            client,
            &current_version,
            std::env::consts::OS,
            std::env::consts::ARCH,
            IS_FULL_VARIANT,
        )
        .await;

        let offer = match check_res {
            Ok(Some(offer)) => offer,
            Ok(None) => {
                self.emit_change(&on_change, |s| {
                    s.status = UpdatePhase::Idle;
                    s.latest_version = Some(current_version.clone());
                    s.release_url = None;
                    s.release_notes = None;
                    s.asset_name = None;
                    s.percent = 0;
                    s.hint = None;
                    s.error = None;
                    s.error_context = None;
                });
                return self.snapshot();
            }
            Err(err) => {
                self.emit_change(&on_change, |s| {
                    s.status = UpdatePhase::Error;
                    s.error = Some(err);
                    s.error_context = Some(UpdateErrorContext::Check);
                });
                return self.snapshot();
            }
        };

        let auto_download = self.target.as_deref().is_some_and(|target| {
            #[cfg(target_os = "macos")]
            {
                can_replace_mac(target) && offer.asset_url.is_some()
            }
            #[cfg(target_os = "windows")]
            {
                let _ = target;
                offer.asset_url.is_some()
            }
            #[cfg(not(any(target_os = "macos", target_os = "windows")))]
            {
                let _ = target;
                false
            }
        });

        {
            let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
            guard.offer = Some(offer.clone());
        }
        self.emit_change(&on_change, |s| {
            s.status = UpdatePhase::Available;
            s.latest_version = Some(offer.version.clone());
            s.release_url = Some(offer.release_url.clone());
            s.release_notes = if offer.release_notes.is_empty() {
                None
            } else {
                Some(offer.release_notes.clone())
            };
            s.asset_name = offer.asset_name.clone();
            s.percent = 0;
            s.automatic = auto_download;
            s.hint = None;
            s.error = None;
            s.error_context = None;
        });

        if auto_download {
            self.download_and_prepare_locked_flight(offer, on_change)
                .await;
        }
        self.snapshot()
    }

    /// Manually trigger download & preparation for an already-discovered offer (e.g. non-writable macOS
    /// location or retry after a download failure).
    pub async fn run_download(&self, on_change: StateListener) -> UpdateStateSnapshot {
        let offer = {
            let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
            if self.stopped.load(Ordering::Acquire) || guard.in_flight {
                return guard.snapshot.clone();
            }
            let Some(offer) = guard.offer.clone() else {
                return guard.snapshot.clone();
            };
            if self.target.is_none() || offer.asset_url.is_none() {
                return guard.snapshot.clone();
            }
            guard.in_flight = true;
            guard.snapshot.manual = true;
            offer
        };
        let _flight = InFlightGuard { controller: self };

        self.download_and_prepare_locked_flight(offer, on_change)
            .await;
        self.snapshot()
    }

    fn finish_in_flight(&self) {
        let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        guard.in_flight = false;
        if self.stopped.load(Ordering::Acquire) && !guard.installing {
            Self::discard_work_locked(&mut guard);
        }
    }

    async fn download_and_prepare_locked_flight(
        &self,
        offer: UpdateOffer,
        on_change: StateListener,
    ) {
        let Some(target) = self.target.clone() else {
            return;
        };
        let Some(asset_name) = offer.asset_name.clone() else {
            return;
        };

        let work_dir = match create_unique_dir(&std::env::temp_dir(), "pulsescribe-update-") {
            Ok(dir) => dir,
            Err(err) => {
                self.emit_change(&on_change, |s| {
                    s.status = UpdatePhase::Error;
                    s.error = Some(err);
                    s.error_context = Some(UpdateErrorContext::Download);
                });
                return;
            }
        };

        {
            let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
            Self::discard_work_locked(&mut guard);
            guard.work_dir = Some(work_dir.clone());
        }

        self.emit_change(&on_change, |s| {
            s.status = UpdatePhase::Downloading;
            s.percent = 0;
            s.error = None;
            s.error_context = None;
        });

        let package_path = work_dir.join(&asset_name);
        let progress_cb = |pct: u8| {
            self.emit_change(&on_change, |s| {
                s.percent = pct;
            });
        };

        let client = match self.client.as_ref() {
            Ok(c) => c,
            Err(err) => {
                let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
                Self::discard_work_locked(&mut guard);
                drop(guard);
                self.emit_change(&on_change, |s| {
                    s.status = UpdatePhase::Error;
                    s.percent = 0;
                    s.error = Some(err.clone());
                    s.error_context = Some(UpdateErrorContext::Download);
                });
                return;
            }
        };

        if let Err(err) =
            download_verified_asset(client, &offer, &package_path, &self.stopped, progress_cb).await
        {
            let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
            Self::discard_work_locked(&mut guard);
            drop(guard);
            self.emit_change(&on_change, |s| {
                s.status = UpdatePhase::Error;
                s.percent = 0;
                s.error = Some(err);
                s.error_context = Some(UpdateErrorContext::Download);
            });
            return;
        }

        if self.stopped.load(Ordering::Acquire) {
            let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
            Self::discard_work_locked(&mut guard);
            return;
        }

        self.emit_change(&on_change, |s| {
            s.status = UpdatePhase::Preparing;
            s.percent = 99;
        });

        let offer_clone = offer.clone();
        let pkg_clone = package_path.clone();
        let target_clone = target.clone();
        let stopped_clone = Arc::clone(&self.stopped);
        let prep_result = tokio::task::spawn_blocking(move || {
            prepare_update(&offer_clone, &pkg_clone, &target_clone, &stopped_clone)
        })
        .await
        .map_err(|err| format!("Update preparation task failed: {err}"))
        .and_then(|res| res);

        match prep_result {
            Ok(prepared) => {
                // On automatic macOS preparation, the DMG download dir is no longer needed;
                // only the sibling staged `.PulseScribe-update-*` directory next to target stays.
                let new_work = prepared.work.clone();
                if new_work != work_dir {
                    let _ = fs::remove_dir_all(&work_dir);
                }
                if self.stopped.load(Ordering::Acquire) {
                    let _ = fs::remove_dir_all(&new_work);
                    let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
                    guard.work_dir = None;
                    guard.prepared = None;
                    return;
                }
                let automatic = prepared.automatic;
                let hint = prepared.hint_key().to_owned();
                {
                    let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
                    guard.work_dir = Some(new_work);
                    guard.prepared = Some(prepared);
                }
                self.emit_change(&on_change, |s| {
                    s.status = UpdatePhase::Ready;
                    s.percent = 100;
                    s.automatic = automatic;
                    s.hint = Some(hint);
                    s.error = None;
                    s.error_context = None;
                });
            }
            Err(err) => {
                let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
                Self::discard_work_locked(&mut guard);
                drop(guard);
                self.emit_change(&on_change, |s| {
                    s.status = UpdatePhase::Error;
                    s.percent = 0;
                    s.error = Some(err);
                    s.error_context = Some(UpdateErrorContext::Install);
                });
            }
        }
    }

    /// Launch the verified prepared update helper, returning `Ok(true)` if the caller should now
    /// cleanly exit the running app so the helper can complete an automatic in-place upgrade, or
    /// `Ok(false)` when a manual installer was opened in Finder/Downloads.
    pub fn begin_install(&self, is_daemon_busy: bool) -> Result<bool, String> {
        if is_daemon_busy {
            return Err(
                "Finish or cancel the active dictation before installing the update.".to_owned(),
            );
        }
        let prepared = {
            let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
            if guard.installing {
                return Err("Update installation is already starting.".to_owned());
            }
            if self.stopped.load(Ordering::Acquire) || guard.snapshot.status != UpdatePhase::Ready {
                return Err("No verified update is ready to install.".to_owned());
            }
            let Some(prepared) = guard.prepared.clone() else {
                return Err("No verified update is ready to install.".to_owned());
            };
            guard.installing = true;
            prepared
        };

        if let Err(err) = launch_prepared_update(&prepared) {
            let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
            guard.installing = false;
            guard.snapshot.status = UpdatePhase::Error;
            guard.snapshot.error = Some(err.clone());
            guard.snapshot.error_context = Some(UpdateErrorContext::Install);
            return Err(err);
        }

        if !prepared.automatic {
            // Manual DMG was moved into Downloads and opened; clean up empty staging dir
            // and reset state back to Available so UI/tray do not stay stuck in Ready.
            let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
            guard.installing = false;
            Self::discard_work_locked(&mut guard);
            guard.snapshot.status = UpdatePhase::Available;
            guard.snapshot.percent = 0;
            guard.snapshot.hint = None;
            return Ok(false);
        }

        Ok(true)
    }

    /// Cancel any in-flight update download/preparation and clean up unlaunched staging files.
    pub fn shutdown(&self) {
        self.stopped.store(true, Ordering::Release);
        let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        if !guard.installing && !guard.in_flight {
            Self::discard_work_locked(&mut guard);
        }
    }
}

struct InFlightGuard<'a> {
    controller: &'a UpdateController,
}

impl Drop for InFlightGuard<'_> {
    fn drop(&mut self) {
        self.controller.finish_in_flight();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("pulsescribe-upd-{tag}-{}", unique_suffix()));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn detect_installed_target_ignores_cargo_dev_binaries() {
        let dev_bin = PathBuf::from("/Users/dev/pulsescribe/target/debug/pulsescribe-desktop");
        assert_eq!(detect_installed_target_from_exe(&dev_bin), None);

        #[cfg(target_os = "macos")]
        {
            let root = temp_dir("mac-target");
            let app = root.join("PulseScribe.app");
            let macos = app.join("Contents/MacOS");
            fs::create_dir_all(&macos).unwrap();
            fs::write(app.join("Contents/Info.plist"), "<plist/>").unwrap();
            let bin = macos.join("pulsescribe-desktop");
            fs::write(&bin, b"bin").unwrap();
            assert_eq!(detect_installed_target_from_exe(&bin), Some(app));
            let _ = fs::remove_dir_all(&root);
        }
    }

    #[test]
    fn can_replace_mac_rejects_dmg_and_translocated_paths() {
        assert!(!can_replace_mac(Path::new(
            "/Volumes/PulseScribe/PulseScribe.app"
        )));
        assert!(!can_replace_mac(Path::new(
            "/private/var/folders/xx/AppTranslocation/ABC/d/PulseScribe.app"
        )));

        let root = temp_dir("writable");
        let app = root.join("PulseScribe.app");
        fs::create_dir_all(&app).unwrap();
        assert!(can_replace_mac(&app));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn verify_windows_pe_installer_rejects_non_pe_and_accepts_mz_header() {
        let root = temp_dir("pe-check");
        let bad = root.join("bad.exe");
        fs::write(&bad, vec![0x00u8; 120_000]).unwrap();
        assert!(verify_windows_pe_installer(&bad).is_err());

        let small = root.join("small.exe");
        fs::write(&small, b"MZshort").unwrap();
        assert!(verify_windows_pe_installer(&small).is_err());

        let good = root.join("good.exe");
        let mut bytes = vec![0x00u8; 120_000];
        bytes[0] = 0x4D;
        bytes[1] = 0x5A;
        fs::write(&good, bytes).unwrap();
        assert!(verify_windows_pe_installer(&good).is_ok());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn begin_install_blocks_when_daemon_is_busy_or_not_ready() {
        let ctrl = UpdateController::with_target("2.1.0", None);
        assert!(ctrl.begin_install(true).is_err());
        assert!(ctrl.begin_install(false).is_err());
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn mac_swap_script_swaps_bundle_atomically_and_rolls_back_on_missing_new() {
        let root = temp_dir("mac-swap");
        let target = root.join("PulseScribe.app");
        let work = root.join(".PulseScribe-update-test");
        let new_app = work.join("PulseScribe.app");
        fs::create_dir_all(&target).unwrap();
        fs::create_dir_all(&new_app).unwrap();
        fs::write(target.join("version.txt"), "2.1.0").unwrap();
        fs::write(new_app.join("version.txt"), "2.2.0").unwrap();

        let opened_log = root.join("opened.log");
        let fake_opener = root.join("fake_open.sh");
        fs::write(
            &fake_opener,
            format!("#!/bin/sh\necho \"$1\" >> \"{}\"\n", opened_log.display()),
        )
        .unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&fake_opener, fs::Permissions::from_mode(0o755)).unwrap();
        }

        // Spawn and reap a short-lived dummy process in a thread so `kill -0` observes true exit.
        let mut dummy = Command::new("/bin/sleep").arg("0.1").spawn().unwrap();
        let dummy_pid = dummy.id().to_string();
        let reaper = std::thread::spawn(move || {
            let _ = dummy.wait();
        });

        let status = Command::new("/bin/sh")
            .arg("-c")
            .arg(MAC_SWAP_SCRIPT)
            .arg("pulsescribe-update")
            .arg(&dummy_pid)
            .arg(&new_app)
            .arg(&target)
            .arg(&work)
            .arg(&fake_opener)
            .status()
            .unwrap();
        let _ = reaper.join();
        assert!(status.success(), "swap script should succeed");
        assert_eq!(
            fs::read_to_string(target.join("version.txt")).unwrap(),
            "2.2.0"
        );
        assert!(!work.exists(), "staging work dir should be cleaned up");
        let opened = fs::read_to_string(&opened_log).unwrap();
        assert!(opened.contains(&target.display().to_string()));

        // Rollback test: missing new_app restores previous target and writes error.txt.
        let work2 = root.join(".PulseScribe-update-fail");
        fs::create_dir_all(&work2).unwrap();
        let missing_new = work2.join("Missing.app");
        let fail_status = Command::new("/bin/sh")
            .arg("-c")
            .arg(MAC_SWAP_SCRIPT)
            .arg("pulsescribe-update")
            .arg("999999")
            .arg(&missing_new)
            .arg(&target)
            .arg(&work2)
            .arg(&fake_opener)
            .status()
            .unwrap();
        assert!(!fail_status.success(), "missing new bundle must fail");
        assert_eq!(
            fs::read_to_string(target.join("version.txt")).unwrap(),
            "2.2.0",
            "previous bundle must be restored on failure"
        );
        assert!(work2.join("error.txt").is_file());

        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn windows_install_script_uses_tauri_nsis_silent_update_flags() {
        assert!(WINDOWS_INSTALL_SCRIPT.contains("/S /UPDATE /D=$directory"));
        assert!(WINDOWS_INSTALL_SCRIPT.contains("Wait-Process -Id $AppPid -Timeout 60"));
        assert!(WINDOWS_INSTALL_SCRIPT.contains("Start-Process -FilePath $Target"));
    }
}
