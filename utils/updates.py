"""GitHub release discovery and streamed, verified update downloads."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPOSITORY = "KLIEBHAN/pulsescribe"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
MAX_PACKAGE_BYTES = 2 * 1024**3


@dataclass(frozen=True)
class UpdateOffer:
    version: str
    notes: str
    name: str
    size: int
    sha256: str

    @property
    def url(self) -> str:
        return f"{RELEASES_URL}/download/v{self.version}/{self.name}"


def version_tuple(version: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"Invalid stable version: {version}")
    return tuple(int(part) for part in version.split("."))


class _HTTPSRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme != "https":
            raise ValueError("Update download redirected to an insecure address.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str):
    request = Request(
        url,
        headers={
            "User-Agent": "PulseScribe-Updater",
            "Accept": "application/vnd.github+json",
        },
    )
    return build_opener(_HTTPSRedirects()).open(request, timeout=30)


def _read_small(url: str) -> bytes:
    with _open(url) as response:
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("Update metadata is too large.")
    return data


def parse_release(
    data: object, current: str, platform: str, *, local: bool = False
) -> UpdateOffer | None:
    """Only offer a newer stable release with the exact platform/variant asset."""
    if not isinstance(data, dict):
        raise ValueError("Invalid release response.")
    if data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise ValueError("Release has no stable version tag.")
    version = tag[1:]
    if version_tuple(version) <= version_tuple(current):
        return None
    if platform == "darwin":
        name = f"PulseScribe-{version}.dmg"
    elif platform == "win32":
        name = f"PulseScribe-Setup-{version}{'-Local.zip' if local else '.exe'}"
    else:
        raise ValueError("Updates are not supported on this platform.")
    assets = data.get("assets")
    if not isinstance(assets, list):
        raise ValueError("Release has no installer assets.")
    asset = next(
        (
            item
            for item in assets
            if isinstance(item, dict) and item.get("name") == name
        ),
        None,
    )
    if asset is None:
        raise ValueError(f"The release does not yet contain {name}. Try again later.")
    size = asset.get("size")
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or not 0 < size <= MAX_PACKAGE_BYTES
    ):
        raise ValueError("Invalid update package size.")
    digest = asset.get("digest")
    checksum = (
        digest[7:] if isinstance(digest, str) and digest.startswith("sha256:") else ""
    )
    if checksum and not re.fullmatch(r"[a-fA-F0-9]{64}", checksum):
        raise ValueError("Invalid update checksum.")
    notes = data.get("body")
    return UpdateOffer(
        version,
        notes.split("<!-- install-notes -->")[0].strip()
        if isinstance(notes, str)
        else "",
        name,
        size,
        checksum.lower(),
    )


def check_release(
    current: str, platform: str, *, local: bool = False
) -> UpdateOffer | None:
    data = json.loads(
        _read_small(f"https://api.github.com/repos/{REPOSITORY}/releases/latest")
    )
    offer = parse_release(data, current, platform, local=local)
    if offer is None or offer.sha256:
        return offer
    # Older releases predate GitHub's asset digests; the build publishes this file.
    checksums = _read_small(
        f"{RELEASES_URL}/download/v{offer.version}/SHA256SUMS.txt"
    ).decode("utf-8-sig")
    for line in checksums.splitlines():
        parts = line.split()
        if (
            len(parts) == 2
            and parts[1].lstrip("*") == offer.name
            and re.fullmatch(r"[a-fA-F0-9]{64}", parts[0])
        ):
            return UpdateOffer(
                offer.version, offer.notes, offer.name, offer.size, parts[0].lower()
            )
    raise ValueError("The release contains no checksum for this installer.")


def download_update(offer: UpdateOffer, destination: Path, progress, cancelled) -> None:
    """Keep memory bounded to one chunk and publish only changed whole percentages."""
    if not re.fullmatch(r"[a-f0-9]{64}", offer.sha256):
        raise ValueError("The update has no valid checksum.")
    digest = hashlib.sha256()
    received = 0
    percent = -1
    created = False
    try:
        with _open(offer.url) as response, destination.open("xb") as output:
            created = True
            while True:
                if cancelled():
                    raise InterruptedError("Update download cancelled.")
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > offer.size:
                    raise ValueError("Update package exceeds its declared size.")
                digest.update(chunk)
                output.write(chunk)
                next_percent = min(99, received * 100 // offer.size)
                if next_percent != percent:
                    percent = next_percent
                    progress(percent)
        if received != offer.size or digest.hexdigest() != offer.sha256:
            raise ValueError("The downloaded update is incomplete or damaged.")
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise
