"""Prepare verified installers and launch helpers that wait for our process to exit."""

from __future__ import annotations

import os
import ntpath
import plistlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from utils.updates import MAX_PACKAGE_BYTES, UpdateOffer


MAC_SWAP_SCRIPT = """pid="$1"; new="$2"; target="$3"; work="$4"; opener="$5"
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
"""

WINDOWS_INSTALL_SCRIPT = r"""param([int]$AppPid, [string]$Installer, [string]$Target,
    [Parameter(Mandatory=$true)][ValidateSet('ALLUSERS', 'CURRENTUSER')][string]$InstallMode)
$ErrorActionPreference = "Stop"
if (Get-Process -Id $AppPid -ErrorAction SilentlyContinue) {
    Wait-Process -Id $AppPid -Timeout 60
}
$directory = Split-Path -Parent $Target
$arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOCLOSEAPPLICATIONS', '/NORESTARTAPPLICATIONS', '/SP-', ('/DIR="' + $directory + '"'), ('/LOG="' + (Join-Path (Split-Path -Parent $Installer) 'setup.log') + '"'))
$arguments += "/$InstallMode"
try {
    $process = Start-Process -FilePath $Installer -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Installer failed: $($process.ExitCode)" }
} finally {
    Start-Process -FilePath $Target
}
Remove-Item -LiteralPath (Split-Path -Parent $Installer) -Recurse -Force
"""


def installed_target() -> Path | None:
    """Source runs never replace an installed application."""
    if not getattr(sys, "frozen", False):
        return None
    executable = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        return next(
            (
                parent
                for parent in executable.parents
                if parent.name == "PulseScribe.app"
            ),
            None,
        )
    if sys.platform == "win32" and executable.name.lower() == "pulsescribe.exe":
        return executable
    return None


def can_replace_mac(bundle: Path) -> bool:
    return (
        not (str(bundle).startswith("/Volumes/") or "/AppTranslocation/" in str(bundle))
        and os.access(bundle, os.W_OK)
        and os.access(bundle.parent, os.W_OK)
    )


def is_local_windows(target: Path) -> bool:
    return (target.parent / "_internal" / "faster_whisper").is_dir()


@dataclass(frozen=True)
class PreparedUpdate:
    package: Path
    target: Path
    work: Path
    platform: str
    automatic: bool

    @property
    def hint(self) -> str:
        if not self.automatic:
            return "The disk image will open. Drag PulseScribe into Applications and replace the previous version."
        return "PulseScribe will quit, install the update, and restart."

    def launch(self) -> None:
        """Spawn before requesting an orderly quit; helpers abort after 60 seconds."""
        if not self.automatic:
            downloads = Path.home() / "Downloads"
            downloads.mkdir(exist_ok=True)
            destination = downloads / self.package.name
            if destination.exists():
                destination = (
                    downloads / f"{self.package.stem}-{uuid.uuid4().hex[:8]}.dmg"
                )
            shutil.move(str(self.package), destination)
            subprocess.Popen(
                ["/usr/bin/open", str(destination)], start_new_session=True
            )
        elif self.platform == "darwin":
            subprocess.Popen(
                [
                    "/bin/sh",
                    "-c",
                    MAC_SWAP_SCRIPT,
                    "pulsescribe-update",
                    str(os.getpid()),
                    str(self.package),
                    str(self.target),
                    str(self.work),
                    "/usr/bin/open",
                ],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            helper = self.work / "install.ps1"
            helper.write_text(WINDOWS_INSTALL_SCRIPT, encoding="utf-8")
            powershell = windows_powershell()
            subprocess.Popen(
                [
                    str(powershell),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(helper),
                    "-AppPid",
                    str(os.getpid()),
                    "-Installer",
                    str(self.package),
                    "-Target",
                    str(self.target),
                    "-InstallMode",
                    windows_install_mode(self.target),
                ],
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def windows_powershell() -> Path:
    """Resolve the OS executable directory without trusting environment paths."""
    import ctypes

    directory = ctypes.create_unicode_buffer(32768)
    size = ctypes.windll.kernel32.GetSystemDirectoryW(directory, len(directory))
    if not 0 < size < len(directory):
        raise OSError("Could not resolve the Windows system directory.")
    return Path(directory.value) / "WindowsPowerShell/v1.0/powershell.exe"


def windows_install_mode(target: Path) -> str:
    """Match Inno's uninstall registration independently of the process bitness."""
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{9A961D4A-17CB-4B01-A331-17C773E14149}_is1"
    directory = ntpath.normcase(ntpath.normpath(str(target.parent)))
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ | view
            ) as key:
                location, _kind = winreg.QueryValueEx(key, "InstallLocation")
        except FileNotFoundError:
            continue
        if not isinstance(location, str) or not location:
            raise ValueError("The machine installation has no valid directory.")
        if ntpath.normcase(ntpath.normpath(location)) == directory:
            return "ALLUSERS"
    return "CURRENTUSER"


def _run_native(
    command: list[str], *, timeout: int, stopped: threading.Event | None = None
) -> None:
    """Wait for a known child process; cancellation never targets other processes."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + timeout
    try:
        while True:
            if stopped is not None and stopped.is_set():
                raise InterruptedError("Update preparation cancelled.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                stdout, stderr = process.communicate(timeout=min(0.25, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            raise subprocess.CalledProcessError(
                process.returncode, command, stdout, stderr
            )
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)


def prepare_update(
    offer: UpdateOffer,
    package: Path,
    target: Path,
    platform: str,
    *,
    stopped: threading.Event | None = None,
) -> PreparedUpdate:
    """Validate the Mac bundle, or unpack only the expected Local Whisper installer."""
    work = package.parent
    if platform == "darwin":
        if not can_replace_mac(target):
            return PreparedUpdate(package, target, work, platform, False)
        mount = work / "mount"
        mount.mkdir()
        staged = Path(
            tempfile.mkdtemp(prefix=".PulseScribe-update-", dir=target.parent)
        )
        try:
            try:
                _run_native(
                    [
                        "/usr/bin/hdiutil",
                        "attach",
                        "-readonly",
                        "-nobrowse",
                        "-mountpoint",
                        str(mount),
                        str(package),
                    ],
                    timeout=120,
                    stopped=stopped,
                )
                bundle = mount / "PulseScribe.app"
                with (bundle / "Contents/Info.plist").open("rb") as file:
                    info = plistlib.load(file)
                if (
                    info.get("CFBundleIdentifier") != "com.kliebhan.pulsescribe"
                    or info.get("CFBundleShortVersionString") != offer.version
                ):
                    raise ValueError(
                        "The update contains an unexpected app or version."
                    )
                _run_native(
                    ["/usr/bin/ditto", str(bundle), str(staged / "PulseScribe.app")],
                    timeout=300,
                    stopped=stopped,
                )
            finally:
                # attach can mount before timing out, so every exit path detaches.
                _run_native(
                    ["/usr/bin/hdiutil", "detach", str(mount)],
                    timeout=60,
                )
        except Exception:
            shutil.rmtree(staged)
            raise
        return PreparedUpdate(
            staged / "PulseScribe.app", target, staged, platform, True
        )
    if platform != "win32":
        raise ValueError("Unsupported update platform.")
    if package.suffix == ".zip":
        name = f"PulseScribe-Setup-{offer.version}-Local.exe"
        with zipfile.ZipFile(package) as archive:
            entries = archive.infolist()
            if (
                len(entries) != 1
                or entries[0].filename != name
                or not 0 < entries[0].file_size <= MAX_PACKAGE_BYTES
            ):
                raise ValueError("Unexpected Local Whisper installer archive.")
            with archive.open(entries[0]) as source, (work / name).open("xb") as output:
                shutil.copyfileobj(source, output, length=256 * 1024)
        package.unlink()
        package = work / name
    return PreparedUpdate(package, target, work, platform, True)
