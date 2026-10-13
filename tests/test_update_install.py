import os
import plistlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from utils import update_install
from utils.updates import UpdateOffer


def offer(name="PulseScribe-1.4.0.dmg"):
    return UpdateOffer("1.4.0", "", name, 1, "a" * 64)


def test_source_run_has_no_install_target(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert update_install.installed_target() is None


def test_frozen_mac_target_and_readonly_locations(monkeypatch, tmp_path):
    bundle = tmp_path / "PulseScribe.app"
    (bundle / "Contents/MacOS").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "executable", str(bundle / "Contents/MacOS/pulsescribe"))
    assert update_install.installed_target() == bundle
    assert update_install.can_replace_mac(bundle)
    assert not update_install.can_replace_mac(Path("/Volumes/Test/PulseScribe.app"))
    assert not update_install.can_replace_mac(
        Path("/private/AppTranslocation/uuid/PulseScribe.app")
    )


def test_windows_local_variant_detected_from_bundle(tmp_path):
    target = tmp_path / "PulseScribe.exe"
    assert not update_install.is_local_windows(target)
    (tmp_path / "_internal/faster_whisper").mkdir(parents=True)
    assert update_install.is_local_windows(target)


def test_local_zip_only_extracts_expected_installer(tmp_path):
    package = tmp_path / "PulseScribe-Setup-1.4.0-Local.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("PulseScribe-Setup-1.4.0-Local.exe", b"installer")
    prepared = update_install.prepare_update(
        offer(package.name), package, tmp_path / "PulseScribe.exe", "win32"
    )
    assert prepared.package.read_bytes() == b"installer"
    assert prepared.automatic
    assert not package.exists()


@pytest.mark.parametrize(
    "name", ["../outside.exe", "nested/PulseScribe-Setup-1.4.0-Local.exe", "Other.exe"]
)
def test_local_zip_rejects_traversal_and_wrong_package(tmp_path, name):
    package = tmp_path / "installer.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(name, b"installer")
    with pytest.raises(ValueError, match="Unexpected"):
        update_install.prepare_update(
            offer(package.name), package, tmp_path / "PulseScribe.exe", "win32"
        )
    assert not (tmp_path.parent / "outside.exe").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX bundle swap")
@pytest.mark.parametrize("missing_new", [False, True])
def test_mac_swap_waits_for_known_process_and_rolls_back(tmp_path, missing_new):
    target = tmp_path / "App with spaces.app"
    target.mkdir()
    (target / "version").write_text("old")
    work = tmp_path / "work"
    work.mkdir()
    new = work / "PulseScribe.app"
    if not missing_new:
        new.mkdir()
        (new / "version").write_text("new")
    opener = tmp_path / "opener"
    opener.write_text('#!/bin/sh\ntest -d "$1"\n')
    opener.chmod(0o700)
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; print('ready', flush=True); sys.stdin.readline()",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    helper = None
    try:
        assert child.stdout.readline().strip() == "ready"
        helper = subprocess.Popen(
            [
                "/bin/sh",
                "-c",
                update_install.MAC_SWAP_SCRIPT,
                "test-update",
                str(child.pid),
                str(new),
                str(target),
                str(work),
                str(opener),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert (target / "version").read_text() == "old"
        child.communicate("exit\n", timeout=5)
        helper.communicate(timeout=5)
        assert helper.returncode == 0
        assert (target / "version").read_text() == ("old" if missing_new else "new")
        assert not work.exists()
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        if helper and helper.poll() is None:
            helper.terminate()
            helper.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "darwin", reason="native DMG preparation")
@pytest.mark.parametrize("valid_bundle", [False, True])
def test_real_dmg_preparation_validates_bundle_and_detaches(tmp_path, valid_bundle):
    source = tmp_path / "source"
    bundle = source / "PulseScribe.app"
    (bundle / "Contents").mkdir(parents=True)
    with (bundle / "Contents/Info.plist").open("wb") as file:
        plistlib.dump(
            {
                "CFBundleIdentifier": "com.kliebhan.pulsescribe"
                if valid_bundle
                else "other.app",
                "CFBundleShortVersionString": "1.4.0",
            },
            file,
        )
    (bundle / "payload").write_text("new app")
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    package = downloads / "PulseScribe-1.4.0.dmg"
    subprocess.run(
        [
            "hdiutil",
            "create",
            "-srcfolder",
            str(source),
            "-volname",
            "PulseScribe Update Test",
            "-format",
            "UDZO",
            str(package),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    target = tmp_path / "Applications/PulseScribe.app"
    target.mkdir(parents=True)
    if not valid_bundle:
        with pytest.raises(ValueError, match="unexpected app"):
            update_install.prepare_update(offer(), package, target, "darwin")
        assert not list(target.parent.glob(".PulseScribe-update-*"))
    else:
        prepared = update_install.prepare_update(offer(), package, target, "darwin")
        try:
            assert prepared.package.parent.parent == target.parent
            assert (prepared.package / "payload").read_text() == "new app"
            assert prepared.automatic
        finally:
            shutil.rmtree(prepared.work)
    assert not (downloads / "mount/PulseScribe.app").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell helper")
@pytest.mark.parametrize("installer_exit", [0, 1])
def test_windows_helper_parses_and_installs_before_restarting(tmp_path, installer_exit):
    helper = tmp_path / "install.ps1"
    helper.write_text(update_install.WINDOWS_INSTALL_SCRIPT, encoding="utf-8")
    work = tmp_path / "download"
    work.mkdir()
    installer = work / "setup.exe"
    installer.write_bytes(b"installer")
    target = tmp_path / "Program Files/PulseScribe/PulseScribe.exe"
    output = tmp_path / "calls.json"
    # Execute the real script; only installer launch and app launch are replaced.
    harness = tmp_path / "test.ps1"
    harness.write_text(
        r"""param($Helper, $Installer, $Target, $Output, [int]$InstallerExit)
$calls = [System.Collections.Generic.List[object]]::new()
function Start-Process {
    param($FilePath, $ArgumentList, [switch]$Wait, [switch]$PassThru)
    $calls.Add(@{file=$FilePath; arguments=$ArgumentList; wait=$Wait.IsPresent})
    return @{ExitCode=$InstallerExit}
}
try { & $Helper -AppPid 2147483647 -Installer $Installer -Target $Target } catch { }
$calls | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $Output
""",
        encoding="utf-8",
    )
    powershell = (
        Path(os.environ["SystemRoot"])
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            str(helper),
            str(installer),
            str(target),
            str(output),
            str(installer_exit),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    import json

    calls = json.loads(output.read_text(encoding="utf-8-sig"))
    assert calls[0]["file"] == str(installer)
    assert calls[0]["wait"]
    assert f'/DIR="{target.parent}"' in calls[0]["arguments"]
    assert calls[1]["file"] == str(target)
    assert "/NOCLOSEAPPLICATIONS" in calls[0]["arguments"]
    assert work.exists() == (installer_exit != 0)
