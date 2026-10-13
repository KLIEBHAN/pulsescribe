# Updates and releases

Packaged PulseScribe installations check the public GitHub repository for a newer
stable release 15 seconds after startup and then hourly. Source runs have no
updater. No token or additional dependency is required.

Use **Check for Updates…** in the macOS menu bar or Windows tray for an explicit
check. Automatic failures stay quiet; manual checks report errors or the current
version. A new update is downloaded in the background where PulseScribe can
install it itself. The menu shows download progress, then **Restart to Update to
X.Y.Z…**. The confirmation dialog shows the installed version, release notes, and
installation steps. Choose **Later** to keep working. Updates never install just
because the app was closed; the update action requires confirmation and an idle
dictation state.

Downloads use HTTPS, bounded chunks, and the exact release asset for the platform.
Size and SHA-256 are checked before preparing an installer. The checksum comes
from GitHub's asset digest, with `SHA256SUMS.txt` as a fallback for older releases.
Only changed integer percentages reach the UI. A single worker prevents duplicate
checks and downloads; network operations time out after 30 seconds of inactivity.

## Installation

- **macOS:** the verified DMG is mounted read-only. PulseScribe checks the bundle
  identifier and version, copies the new app next to the installed bundle, and
  detaches the image. After the confirmed restart, a detached helper waits up to
  60 seconds for this process to exit, swaps the bundles, and starts the new app.
  If the replacement move fails, it restores the old bundle and opens a diagnosis
  file; the staged update remains for inspection. An app running from
  a DMG, App Translocation, or a location without write access uses the manual
  DMG installation instead and downloads only when requested. Opening the manual
  installer moves it into Downloads before temporary working files are removed.
- **Windows:** a detached PowerShell helper waits up to 60 seconds for this
  process to exit, runs the Inno Setup installer silently into the current
  installation directory, and restarts PulseScribe after a successful installer
  exit. The helper selects the machine-wide mode from the installed application's
  uninstall registration in either the 32-bit or 64-bit registry view, or the
  current-user mode otherwise. Inno Setup requests elevation for
  an existing administrative installation ([Inno Setup install modes](https://jrsoftware.org/ishelp/topic_setup_usepreviousprivileges.htm)). A failed install restarts the app
  and keeps the installer and `setup.log` for diagnosis. It does not close other
  applications. Local Whisper installations require the `-Local.zip` asset and never
  receive the API-only installer. If the newer release lacks that optional asset,
  a manual check reports it; automatic checks retry hourly.

Installation and recording starts share a lock in each daemon. After reserving
the update, new recording starts are rejected until exit. Concurrent install
actions cannot launch a second helper. Shutdown cancels and joins the update
worker before removing temporary files; native preparation children are stopped
by their known PID, and a mount is detached even when attachment times out.

User configuration in `~/.pulsescribe` is preserved. Packages remain ad-hoc signed
on macOS and unsigned on Windows. The initial installation uses the existing
Gatekeeper/SmartScreen steps. macOS may require accessibility permission again
after a rebuilt app is installed.

Version 1.3.0 and earlier have no updater. Install version 1.4.0 once from the
release page; subsequent releases can be installed from its menu.

## Publishing

1. Merge the reviewed change and wait for the default branch's test workflow.
2. Update `pyproject.toml` and the changelog, commit the version bump, and push.
   Wait for that commit's tests before tagging it.
3. Write user-facing release notes in an annotated tag. Tag and project version
   must match:

   ```bash
   git tag -a v1.4.0 -F release-notes.txt
   git push origin v1.4.0
   ```

The **Build Installers** workflow validates the tag, version, and exact commit,
builds and validates the macOS DMG and API-only Windows installer, and creates
`SHA256SUMS.txt`. It creates a draft release, uploads the assets, and publishes it
as latest only after successful uploads and only if its version is newer than
the current latest release. Publication jobs are serialized so concurrently
completed builds cannot move latest backwards. The tag text becomes the update notes.
The release is not published if a build or upload fails. Reruns can continue a
draft, replacing partial draft uploads to keep installers and checksums consistent.
Published assets are overwritten only when explicitly requested through the
manual workflow. Lightweight tags are rejected. Published tags must not be moved.

The larger Windows Local Whisper package remains an opt-in build; see
[Building Windows](BUILDING_WINDOWS.md#ci-build). Existing `release: published`
and manual build triggers remain supported. Automatic publication through the
workflow's `GITHUB_TOKEN` does not trigger a second installer build.

## Focused validation

```bash
python -m pytest tests/test_updates.py tests/test_update_flow.py \
  tests/test_update_install.py tests/test_update_ui.py tests/test_menubar.py -q
actionlint .github/workflows/build-installers.yml .github/workflows/test.yml
```

Tests cover stable-version and package selection, checksums, bounded download
progress, cancellation, deduplication, shutdown, busy-dictation guards, and real
Mac bundle swaps with rollback. macOS also builds and mounts a small test DMG;
Windows CI executes the PowerShell helper with only process launches substituted.
