import hashlib
import io
from dataclasses import replace

import pytest

from utils import updates


def release(version="1.4.0", *, platform="darwin", local=False, content=b"installer"):
    name = (
        f"PulseScribe-{version}.dmg"
        if platform == "darwin"
        else f"PulseScribe-Setup-{version}{'-Local.zip' if local else '.exe'}"
    )
    return {
        "tag_name": f"v{version}",
        "draft": False,
        "prerelease": False,
        "body": "New dictation features\n<!-- install-notes -->\nInstallation help",
        "assets": [
            {
                "name": name,
                "size": len(content),
                "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
                "browser_download_url": "https://untrusted.example/installer",
            }
        ],
    }


@pytest.mark.parametrize(
    "platform,local", [("darwin", False), ("win32", False), ("win32", True)]
)
def test_exact_platform_and_variant_package(platform, local):
    offer = updates.parse_release(
        release(platform=platform, local=local), "1.3.0", platform, local=local
    )
    assert offer.version == "1.4.0"
    assert offer.notes == "New dictation features"
    assert offer.url.startswith(
        "https://github.com/KLIEBHAN/pulsescribe/releases/download/v1.4.0/"
    )
    assert "untrusted" not in offer.url


@pytest.mark.parametrize("version", ["1.2.9", "1.3.0"])
def test_never_downgrades(version):
    assert updates.parse_release(release(version), "1.3.0", "darwin") is None


def test_version_comparison_is_numeric():
    assert updates.parse_release(release("1.10.0"), "1.9.0", "darwin") is not None


@pytest.mark.parametrize("flag", ["draft", "prerelease"])
def test_unpublished_releases_are_ignored(flag):
    data = release()
    data[flag] = True
    assert updates.parse_release(data, "1.3.0", "darwin") is None


def test_local_installation_never_gets_cloud_installer():
    with pytest.raises(ValueError, match="Local.zip"):
        updates.parse_release(release(platform="win32"), "1.3.0", "win32", local=True)


@pytest.mark.parametrize("size", [True, 0, -1, "1", updates.MAX_PACKAGE_BYTES + 1])
def test_rejects_invalid_size(size):
    data = release()
    data["assets"][0]["size"] = size
    with pytest.raises(ValueError, match="size"):
        updates.parse_release(data, "1.3.0", "darwin")


@pytest.mark.parametrize("tag", ["v1.4.0-beta", "v1.4", "v1/../../4.0", "v1.4.0\n"])
def test_rejects_nonstable_tags(tag):
    data = release()
    data["tag_name"] = tag
    with pytest.raises(ValueError):
        updates.parse_release(data, "1.3.0", "darwin")


def test_checksum_fallback_for_old_github_assets(monkeypatch):
    import json

    data = release()
    digest = data["assets"][0].pop("digest")[7:]
    replies = iter(
        [json.dumps(data).encode(), f"{digest}  PulseScribe-1.4.0.dmg\n".encode()]
    )
    monkeypatch.setattr(updates, "_read_small", lambda _url: next(replies))
    assert updates.check_release("1.3.0", "darwin").sha256 == digest


def test_download_streams_and_coalesces_progress(monkeypatch, tmp_path):
    content = b"x" * (1024 * 1024)
    offer = updates.parse_release(release(content=content), "1.3.0", "darwin")
    monkeypatch.setattr(updates, "_open", lambda _url: io.BytesIO(content))
    percentages = []
    output = tmp_path / offer.name
    updates.download_update(offer, output, percentages.append, lambda: False)
    assert output.read_bytes() == content
    assert percentages == [25, 50, 75, 99]


@pytest.mark.parametrize(
    "content", [b"wrong!!!!", b"short", b"installer plus extra bytes"]
)
def test_download_rejects_damage_and_cleans_partial_file(
    monkeypatch, tmp_path, content
):
    offer = updates.parse_release(release(), "1.3.0", "darwin")
    monkeypatch.setattr(updates, "_open", lambda _url: io.BytesIO(content))
    output = tmp_path / offer.name
    with pytest.raises(ValueError):
        updates.download_update(offer, output, lambda _percent: None, lambda: False)
    assert not output.exists()


def test_download_cancel_and_existing_file_are_safe(monkeypatch, tmp_path):
    offer = updates.parse_release(release(), "1.3.0", "darwin")
    monkeypatch.setattr(updates, "_open", lambda _url: io.BytesIO(b"installer"))
    output = tmp_path / offer.name
    with pytest.raises(InterruptedError):
        updates.download_update(offer, output, lambda _percent: None, lambda: True)
    assert not output.exists()
    output.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        updates.download_update(offer, output, lambda _percent: None, lambda: False)
    assert output.read_bytes() == b"keep"
    with pytest.raises(ValueError, match="checksum"):
        updates.download_update(
            replace(offer, sha256=""),
            tmp_path / "bad",
            lambda _percent: None,
            lambda: False,
        )


def test_redirect_cannot_downgrade_tls():
    with pytest.raises(ValueError, match="insecure"):
        updates._HTTPSRedirects().redirect_request(
            None, None, 302, "", {}, "http://example.com/package"
        )
