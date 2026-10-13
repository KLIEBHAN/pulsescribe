"""Prepare verified installers and launch helpers that wait for our process to exit."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from utils.updates import MAX_PACKAGE_BYTES, UpdateOffer


MAC_SWAP_SCRIPT = """pid="$1"; new="$2"; target="$3"; work="$4"; opener="$5"
i=0
while kill -0 "$pid" 2>/dev/null; do
    i=$((i + 1))
    [ "$i" -gt 600 ] && exit 1
    sleep 0.1
done
backup="$work/previous.app"
if mv "$target" "$backup"; then
    if mv "$new" "$target"; then
        rm -rf "$backup"
    else
        mv "$backup" "$target" || exit 1
    fi
fi
"$opener" "$target"
rm -rf "$work"
"""

WINDOWS_INSTALL_SCRIPT = r"""param([int]$AppPid, [string]$Installer, [string]$Target)
$ErrorActionPreference = "Stop"
if (Get-Process -Id $AppPid -ErrorAction SilentlyContinue) {
    Wait-Process -Id $AppPid -Timeout 60
}
$directory = Split-Path -Parent $Target
$arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOCLOSEAPPLICATIONS', '/NORESTARTAPPLICATIONS', '/SP-', ('/DIR="' + $directory + '"'), ('/LOG="' + (Join-Path (Split-Path -Parent $Installer) 'setup.log') + '"'))
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
            subprocess.Popen(
                ["/usr/bin/open", str(self.package)], start_new_session=True
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
            powershell = (
                Path(os.environ["SystemRoot"])
                / "System32/WindowsPowerShell/v1.0/powershell.exe"
            )
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
                ],
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def prepare_update(
    offer: UpdateOffer, package: Path, target: Path, platform: str
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
            subprocess.run(
                [
                    "/usr/bin/hdiutil",
                    "attach",
                    "-readonly",
                    "-nobrowse",
                    "-mountpoint",
                    str(mount),
                    str(package),
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )
            try:
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
                subprocess.run(
                    ["/usr/bin/ditto", str(bundle), str(staged / "PulseScribe.app")],
                    check=True,
                    capture_output=True,
                    timeout=300,
                )
            finally:
                subprocess.run(
                    ["/usr/bin/hdiutil", "detach", str(mount)],
                    check=True,
                    capture_output=True,
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
