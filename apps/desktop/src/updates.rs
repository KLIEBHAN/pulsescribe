//! GitHub Releases discovery, SHA-256 verification, and streamed update downloads.
//!
//! Zero extra crate dependencies: includes a self-contained FIPS 180-4 SHA-256
//! implementation verified against standard NIST test vectors.

use std::fs::OpenOptions;
use std::io::Write;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use reqwest::Client;
use serde::{Deserialize, Serialize};

pub const GITHUB_REPOSITORY: &str = "KLIEBHAN/pulsescribe";
pub const GITHUB_LATEST_RELEASE_URL: &str =
    "https://api.github.com/repos/KLIEBHAN/pulsescribe/releases/latest";
pub const GITHUB_RELEASES_DOWNLOAD_BASE: &str =
    "https://github.com/KLIEBHAN/pulsescribe/releases/download";

/// True when this binary was compiled with local offline ASR backends (Full variant).
pub const IS_FULL_VARIANT: bool = cfg!(any(feature = "local-whisper", feature = "local-sherpa"));

/// Upper bound on any downloaded installer package (2 GiB).
pub const MAX_PACKAGE_BYTES: u64 = 2 * 1024 * 1024 * 1024;
/// Upper bound on release metadata and `SHA256SUMS.txt` payloads (1 MiB).
pub const MAX_METADATA_BYTES: usize = 1024 * 1024;

const METADATA_REQUEST_TIMEOUT: Duration = Duration::from_secs(15);
const CONNECT_TIMEOUT: Duration = Duration::from_secs(15);
const READ_INACTIVITY_TIMEOUT: Duration = Duration::from_secs(30);
const MAX_RELEASE_NOTES_CHARS: usize = 4_000;
const INSTALL_NOTES_MARKER: &str = "<!-- install-notes -->";

/// Lifecycle phase of the in-app updater state machine.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UpdatePhase {
    #[default]
    Idle,
    Checking,
    Available,
    Downloading,
    Preparing,
    Ready,
    Error,
}

/// Which stage failed when [`UpdatePhase::Error`] is reported.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UpdateErrorContext {
    Check,
    Download,
    Install,
}

/// Verified metadata for a newer stable GitHub Release asset matching this platform/variant.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UpdateOffer {
    pub version: String,
    pub release_url: String,
    pub release_notes: String,
    pub asset_name: Option<String>,
    pub asset_url: Option<String>,
    pub size: Option<u64>,
    pub sha256: Option<String>,
}

/// Snapshot emitted to the tray menu and Settings/Diagnostics WebView.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UpdateStateSnapshot {
    pub current_version: String,
    pub status: UpdatePhase,
    pub latest_version: Option<String>,
    pub release_url: Option<String>,
    pub release_notes: Option<String>,
    pub asset_name: Option<String>,
    pub percent: u8,
    pub automatic: bool,
    pub can_install_in_app: bool,
    pub manual: bool,
    pub hint: Option<String>,
    pub error: Option<String>,
    pub error_context: Option<UpdateErrorContext>,
}

impl UpdateStateSnapshot {
    pub fn idle(current_version: impl Into<String>, can_install_in_app: bool) -> Self {
        Self {
            current_version: current_version.into(),
            status: UpdatePhase::Idle,
            latest_version: None,
            release_url: None,
            release_notes: None,
            asset_name: None,
            percent: 0,
            automatic: false,
            can_install_in_app,
            manual: false,
            hint: None,
            error: None,
            error_context: None,
        }
    }
}

/// Backwards-compatible check result kept for existing `check_for_updates` callers.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UpdateCheckResult {
    pub current_version: String,
    pub latest_version: Option<String>,
    pub update_available: bool,
    pub release_url: Option<String>,
    pub release_notes: Option<String>,
    pub error: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct GitHubRelease {
    pub tag_name: String,
    #[serde(default)]
    pub draft: bool,
    #[serde(default)]
    pub prerelease: bool,
    pub html_url: Option<String>,
    pub body: Option<String>,
    #[serde(default)]
    pub assets: Vec<GitHubReleaseAsset>,
}

#[derive(Debug, Deserialize)]
pub struct GitHubReleaseAsset {
    pub name: String,
    pub size: u64,
    pub digest: Option<String>,
}

/// Build an HTTPS-only `reqwest::Client` that refuses insecure HTTP redirects and
/// enforces bounded connection + read-inactivity timeouts.
pub fn build_updater_http_client() -> Result<Client, String> {
    let policy = reqwest::redirect::Policy::custom(|attempt| {
        if attempt.url().scheme() != "https" {
            return attempt.error("Update request redirected to an insecure address.");
        }
        if attempt.previous().len() >= 10 {
            return attempt.error("Too many redirects while fetching update.");
        }
        attempt.follow()
    });
    Client::builder()
        .user_agent(format!("PulseScribe/{}", env!("CARGO_PKG_VERSION")))
        .connect_timeout(CONNECT_TIMEOUT)
        .read_timeout(READ_INACTIVITY_TIMEOUT)
        .redirect(policy)
        .build()
        .map_err(|err| format!("Failed to initialize updater HTTP client: {err}"))
}

/// Parse a `vX.Y.Z` or `X.Y.Z` semantic version prefix (ignoring optional pre-release/build suffix).
pub fn parse_semver(raw: &str) -> Option<(u64, u64, u64)> {
    let trimmed = raw.trim();
    let without_prefix = trimmed
        .strip_prefix('v')
        .or_else(|| trimmed.strip_prefix('V'))
        .unwrap_or(trimmed);
    let core = without_prefix.split(['-', '+']).next()?;
    let mut parts = core.split('.');
    let major = parts.next()?.parse::<u64>().ok()?;
    let minor = parts.next().unwrap_or("0").parse::<u64>().ok()?;
    let patch = parts.next().unwrap_or("0").parse::<u64>().ok()?;
    if parts.next().is_some() {
        return None;
    }
    Some((major, minor, patch))
}

/// Parse a strictly stable `vX.Y.Z` or `X.Y.Z` version (three numeric segments, no suffix).
pub fn parse_stable_version(raw: &str) -> Option<(String, (u64, u64, u64))> {
    let trimmed = raw.trim();
    let version = trimmed
        .strip_prefix('v')
        .or_else(|| trimmed.strip_prefix('V'))
        .unwrap_or(trimmed);
    let mut parts = version.split('.');
    let major_s = parts.next()?;
    let minor_s = parts.next()?;
    let patch_s = parts.next()?;
    if parts.next().is_some() {
        return None;
    }
    if [major_s, minor_s, patch_s]
        .iter()
        .any(|s| s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit()))
    {
        return None;
    }
    let tuple = (
        major_s.parse::<u64>().ok()?,
        minor_s.parse::<u64>().ok()?,
        patch_s.parse::<u64>().ok()?,
    );
    Some((version.to_owned(), tuple))
}

/// Return `true` when `latest` parses to a strictly higher SemVer tuple than `current`,
/// or when both share `(major, minor, patch)` and `current` is a pre-release while `latest` is stable.
pub fn is_newer_version(current: &str, latest: &str) -> bool {
    let (Some(cur), Some(lat)) = (parse_semver(current), parse_semver(latest)) else {
        return false;
    };
    if lat != cur {
        return lat > cur;
    }
    let has_prerelease = |v: &str| v.split('+').next().is_some_and(|base| base.contains('-'));
    has_prerelease(current) && !has_prerelease(latest)
}

/// Return the expected GitHub Release installer asset filename for `(os, arch, is_full)`.
pub fn expected_asset_name(version: &str, os: &str, arch: &str, is_full: bool) -> Option<String> {
    let variant = if is_full { "Full" } else { "Slim" };
    match (os, arch) {
        ("macos", "aarch64") => Some(format!("PulseScribe-{version}-macOS-arm64-{variant}.dmg")),
        ("windows", "x86_64") => Some(format!(
            "PulseScribe-{version}-Windows-x64-{variant}-Setup.exe"
        )),
        _ => None,
    }
}

/// Validate a 64-character lowercase/uppercase hex SHA-256 string and normalize to lowercase.
pub fn normalize_sha256_hex(raw: &str) -> Option<String> {
    let trimmed = raw.trim();
    if trimmed.len() == 64 && trimmed.bytes().all(|b| b.is_ascii_hexdigit()) {
        Some(trimmed.to_ascii_lowercase())
    } else {
        None
    }
}

/// Parse a `SHA256SUMS.txt` manifest and return the lowercase SHA-256 digest matching `asset_name`.
pub fn parse_sha256sums(contents: &str, asset_name: &str) -> Option<String> {
    let stripped = contents.strip_prefix('\u{feff}').unwrap_or(contents);
    for line in stripped.lines() {
        let mut parts = line.split_whitespace();
        let Some(hash) = parts.next() else {
            continue;
        };
        let Some(name) = parts.next() else {
            continue;
        };
        if parts.next().is_some() {
            continue;
        }
        if name.trim_start_matches('*') == asset_name {
            if let Some(valid) = normalize_sha256_hex(hash) {
                return Some(valid);
            }
        }
    }
    None
}

/// Strip install instructions after `<!-- install-notes -->` and clamp very long notes.
pub fn clean_release_notes(body: Option<&str>) -> String {
    let Some(raw) = body else {
        return String::new();
    };
    let head = raw.split(INSTALL_NOTES_MARKER).next().unwrap_or(raw).trim();
    if head.chars().count() > MAX_RELEASE_NOTES_CHARS {
        let prefix: String = head.chars().take(MAX_RELEASE_NOTES_CHARS - 1).collect();
        format!("{}…", prefix.trim_end())
    } else {
        head.to_owned()
    }
}

/// Parse a GitHub Release payload into an [`UpdateOffer`] when it is strictly newer than `current`.
pub fn parse_release_offer(
    release: &GitHubRelease,
    current_version: &str,
    os: &str,
    arch: &str,
    is_full: bool,
) -> Result<Option<UpdateOffer>, String> {
    if release.draft || release.prerelease {
        return Ok(None);
    }
    let Some((version, _)) = parse_stable_version(&release.tag_name) else {
        return Err("Release has no stable version tag.".to_owned());
    };
    if !is_newer_version(current_version, &release.tag_name) {
        return Ok(None);
    }

    let release_url = release
        .html_url
        .as_deref()
        .filter(|url| url.starts_with("https://github.com/"))
        .map(ToOwned::to_owned)
        .unwrap_or_else(|| {
            format!("https://github.com/{GITHUB_REPOSITORY}/releases/tag/v{version}")
        });
    let release_notes = clean_release_notes(release.body.as_deref());

    let Some(expected_name) = expected_asset_name(&version, os, arch, is_full) else {
        return Ok(Some(UpdateOffer {
            version,
            release_url,
            release_notes,
            asset_name: None,
            asset_url: None,
            size: None,
            sha256: None,
        }));
    };

    let Some(asset) = release.assets.iter().find(|a| a.name == expected_name) else {
        return Err(format!(
            "Release v{version} does not yet contain {expected_name}. Try again later."
        ));
    };
    if asset.size == 0 || asset.size > MAX_PACKAGE_BYTES {
        return Err("Invalid update package size.".to_owned());
    }

    let sha256 = match asset.digest.as_deref() {
        Some(raw) => {
            let hex = raw
                .strip_prefix("sha256:")
                .and_then(normalize_sha256_hex)
                .ok_or_else(|| "Invalid update checksum digest.".to_owned())?;
            Some(hex)
        }
        None => None,
    };

    let asset_url = format!("{GITHUB_RELEASES_DOWNLOAD_BASE}/v{version}/{expected_name}");

    Ok(Some(UpdateOffer {
        version,
        release_url,
        release_notes,
        asset_name: Some(expected_name),
        asset_url: Some(asset_url),
        size: Some(asset.size),
        sha256,
    }))
}

async fn read_bounded_bytes(resp: reqwest::Response, max_bytes: usize) -> Result<Vec<u8>, String> {
    let mut resp = resp;
    let mut out = Vec::new();
    while let Some(chunk) = resp
        .chunk()
        .await
        .map_err(|err| format!("Network read error: {err}"))?
    {
        if out.len().saturating_add(chunk.len()) > max_bytes {
            return Err("Update metadata exceeds maximum allowed size.".to_owned());
        }
        out.extend_from_slice(&chunk);
    }
    Ok(out)
}

/// Fetch the latest stable GitHub Release and resolve its SHA-256 digest (asset digest or `SHA256SUMS.txt`).
pub async fn fetch_latest_offer(
    client: &Client,
    current_version: &str,
    os: &str,
    arch: &str,
    is_full: bool,
) -> Result<Option<UpdateOffer>, String> {
    let resp = client
        .get(GITHUB_LATEST_RELEASE_URL)
        .timeout(METADATA_REQUEST_TIMEOUT)
        .header(reqwest::header::ACCEPT, "application/vnd.github+json")
        .send()
        .await
        .map_err(|err| format!("Update check failed: {err}"))?;
    if !resp.status().is_success() {
        return Err(format!(
            "GitHub Releases returned HTTP {}",
            resp.status().as_u16()
        ));
    }
    let body = read_bounded_bytes(resp, MAX_METADATA_BYTES).await?;
    let release: GitHubRelease =
        serde_json::from_slice(&body).map_err(|err| format!("Invalid release payload: {err}"))?;

    let Some(mut offer) = parse_release_offer(&release, current_version, os, arch, is_full)? else {
        return Ok(None);
    };

    // Fall back to `SHA256SUMS.txt` when GitHub's API did not populate `asset.digest`.
    if offer.sha256.is_none() {
        if let Some(asset_name) = offer.asset_name.as_deref() {
            let sums_url = format!(
                "{GITHUB_RELEASES_DOWNLOAD_BASE}/v{}/SHA256SUMS.txt",
                offer.version
            );
            let sums_resp = client
                .get(&sums_url)
                .timeout(METADATA_REQUEST_TIMEOUT)
                .send()
                .await
                .map_err(|err| format!("Could not fetch SHA256SUMS.txt: {err}"))?;
            if !sums_resp.status().is_success() {
                return Err(format!(
                    "SHA256SUMS.txt returned HTTP {}",
                    sums_resp.status().as_u16()
                ));
            }
            let sums_bytes = read_bounded_bytes(sums_resp, MAX_METADATA_BYTES).await?;
            let sums_text = String::from_utf8(sums_bytes)
                .map_err(|_| "SHA256SUMS.txt is not valid UTF-8.".to_owned())?;
            let checksum = parse_sha256sums(&sums_text, asset_name)
                .ok_or_else(|| "Release contains no checksum for this installer.".to_owned())?;
            offer.sha256 = Some(checksum);
        }
    }

    Ok(Some(offer))
}

/// Stream an update asset to `destination`, enforcing exact byte size, HTTPS origin,
/// cancellation checks, and SHA-256 checksum verification before returning.
pub async fn download_verified_asset<F>(
    client: &Client,
    offer: &UpdateOffer,
    destination: &Path,
    cancelled: &AtomicBool,
    mut on_progress: F,
) -> Result<(), String>
where
    F: FnMut(u8),
{
    let url = offer
        .asset_url
        .as_deref()
        .filter(|u| u.starts_with("https://"))
        .ok_or_else(|| "Update offer has no valid HTTPS download URL.".to_owned())?;
    let expected_size = offer
        .size
        .filter(|&s| s > 0 && s <= MAX_PACKAGE_BYTES)
        .ok_or_else(|| "Update offer has no valid package size.".to_owned())?;
    let expected_sha256 = offer
        .sha256
        .as_deref()
        .and_then(normalize_sha256_hex)
        .ok_or_else(|| "Update offer has no valid SHA-256 checksum.".to_owned())?;

    if cancelled.load(Ordering::Acquire) {
        return Err("Update download cancelled.".to_owned());
    }

    let mut resp = client
        .get(url)
        .send()
        .await
        .map_err(|err| format!("Could not start update download: {err}"))?;
    if !resp.status().is_success() {
        return Err(format!(
            "Update download returned HTTP {}",
            resp.status().as_u16()
        ));
    }
    if resp.url().scheme() != "https" {
        return Err("Update download resolved to an insecure address.".to_owned());
    }

    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(destination)
        .map_err(|err| format!("Could not create download file: {err}"))?;

    let download_result = async {
        let mut hasher = Sha256::new();
        let mut received: u64 = 0;
        let mut last_percent: Option<u8> = None;

        while let Some(chunk) = resp
            .chunk()
            .await
            .map_err(|err| format!("Download interrupted: {err}"))?
        {
            if cancelled.load(Ordering::Acquire) {
                return Err("Update download cancelled.".to_owned());
            }
            received = received
                .checked_add(chunk.len() as u64)
                .ok_or_else(|| "Update package size overflow.".to_owned())?;
            if received > expected_size {
                return Err("Update package exceeds its declared size.".to_owned());
            }
            hasher.update(&chunk);
            file.write_all(&chunk)
                .map_err(|err| format!("Could not write update package: {err}"))?;

            let pct = ((received.saturating_mul(100)) / expected_size).min(99) as u8;
            if last_percent != Some(pct) {
                last_percent = Some(pct);
                on_progress(pct);
            }
        }
        file.flush()
            .map_err(|err| format!("Could not flush update package: {err}"))?;

        if received != expected_size {
            return Err("The downloaded update is incomplete.".to_owned());
        }
        let actual_sha256 = hasher.finalize_hex();
        if actual_sha256 != expected_sha256 {
            return Err("The downloaded update failed SHA-256 verification.".to_owned());
        }
        Ok(())
    }
    .await;

    drop(file);
    if download_result.is_err() {
        let _ = std::fs::remove_file(destination);
    }
    download_result
}

/// Query GitHub Releases for the latest PulseScribe tag without downloading or installing anything.
pub async fn check_latest_release(client: &Client, current_version: &str) -> UpdateCheckResult {
    let response = client
        .get(GITHUB_LATEST_RELEASE_URL)
        .timeout(METADATA_REQUEST_TIMEOUT)
        .header(reqwest::header::ACCEPT, "application/vnd.github+json")
        .send()
        .await;

    let resp = match response {
        Ok(r) => r,
        Err(err) => {
            return UpdateCheckResult {
                current_version: current_version.to_owned(),
                latest_version: None,
                update_available: false,
                release_url: None,
                release_notes: None,
                error: Some(format!("Update check failed: {err}")),
            };
        }
    };

    if !resp.status().is_success() {
        return UpdateCheckResult {
            current_version: current_version.to_owned(),
            latest_version: None,
            update_available: false,
            release_url: None,
            release_notes: None,
            error: Some(format!(
                "GitHub Releases returned HTTP {}",
                resp.status().as_u16()
            )),
        };
    }

    match resp.json::<GitHubRelease>().await {
        Ok(release) => {
            let latest = release.tag_name.trim().trim_start_matches(['v', 'V']);
            let update_available = is_newer_version(current_version, &release.tag_name);
            let notes =
                Some(clean_release_notes(release.body.as_deref())).filter(|s| !s.is_empty());
            UpdateCheckResult {
                current_version: current_version.to_owned(),
                latest_version: Some(latest.to_owned()),
                update_available,
                release_url: release.html_url,
                release_notes: notes,
                error: None,
            }
        }
        Err(err) => UpdateCheckResult {
            current_version: current_version.to_owned(),
            latest_version: None,
            update_available: false,
            release_url: None,
            release_notes: None,
            error: Some(format!("Invalid release payload: {err}")),
        },
    }
}

/// Pure-Rust FIPS 180-4 SHA-256 streaming hasher (zero external dependencies).
#[derive(Clone)]
pub struct Sha256 {
    state: [u32; 8],
    buf: [u8; 64],
    buf_len: usize,
    total_len: u64,
}

const SHA256_INIT: [u32; 8] = [
    0x6a09_e667,
    0xbb67_ae85,
    0x3c6e_f372,
    0xa54f_f53a,
    0x510e_527f,
    0x9b05_688c,
    0x1f83_d9ab,
    0x5be0_cd19,
];

const SHA256_K: [u32; 64] = [
    0x428a_2f98,
    0x7137_4491,
    0xb5c0_fbcf,
    0xe9b5_dba5,
    0x3956_c25b,
    0x59f1_11f1,
    0x923f_82a4,
    0xab1c_5ed5,
    0xd807_aa98,
    0x1283_5b01,
    0x2431_85be,
    0x550c_7dc3,
    0x72be_5d74,
    0x80de_b1fe,
    0x9bdc_06a7,
    0xc19b_f174,
    0xe49b_69c1,
    0xefbe_4786,
    0x0fc1_9dc6,
    0x240c_a1cc,
    0x2de9_2c6f,
    0x4a74_84aa,
    0x5cb0_a9dc,
    0x76f9_88da,
    0x983e_5152,
    0xa831_c66d,
    0xb003_27c8,
    0xbf59_7fc7,
    0xc6e0_0bf3,
    0xd5a7_9147,
    0x06ca_6351,
    0x1429_2967,
    0x27b7_0a85,
    0x2e1b_2138,
    0x4d2c_6dfc,
    0x5338_0d13,
    0x650a_7354,
    0x766a_0abb,
    0x81c2_c92e,
    0x9272_2c85,
    0xa2bf_e8a1,
    0xa81a_664b,
    0xc24b_8b70,
    0xc76c_51a3,
    0xd192_e819,
    0xd699_0624,
    0xf40e_3585,
    0x106a_a070,
    0x19a4_c116,
    0x1e37_6c08,
    0x2748_774c,
    0x34b0_bcb5,
    0x391c_0cb3,
    0x4ed8_aa4a,
    0x5b9c_ca4f,
    0x682e_6ff3,
    0x748f_82ee,
    0x78a5_636f,
    0x84c8_7814,
    0x8cc7_0208,
    0x90be_fffa,
    0xa450_6ceb,
    0xbef9_a3f7,
    0xc671_78f2,
];

impl Default for Sha256 {
    fn default() -> Self {
        Self::new()
    }
}

impl Sha256 {
    pub const fn new() -> Self {
        Self {
            state: SHA256_INIT,
            buf: [0u8; 64],
            buf_len: 0,
            total_len: 0,
        }
    }

    pub fn update(&mut self, mut input: &[u8]) {
        self.total_len = self.total_len.wrapping_add(input.len() as u64);
        if self.buf_len > 0 {
            let take = (64 - self.buf_len).min(input.len());
            self.buf[self.buf_len..self.buf_len + take].copy_from_slice(&input[..take]);
            self.buf_len += take;
            input = &input[take..];
            if self.buf_len == 64 {
                let block = self.buf;
                Self::compress(&mut self.state, &block);
                self.buf_len = 0;
            }
        }
        let (blocks, remainder) = input.as_chunks::<64>();
        for block in blocks {
            Self::compress(&mut self.state, block);
        }
        if !remainder.is_empty() {
            self.buf[..remainder.len()].copy_from_slice(remainder);
            self.buf_len = remainder.len();
        }
    }

    pub fn finalize(mut self) -> [u8; 32] {
        let bit_len = self.total_len.wrapping_mul(8);
        self.buf[self.buf_len] = 0x80;
        self.buf_len += 1;
        if self.buf_len > 56 {
            self.buf[self.buf_len..64].fill(0);
            let block = self.buf;
            Self::compress(&mut self.state, &block);
            self.buf_len = 0;
        }
        self.buf[self.buf_len..56].fill(0);
        self.buf[56..64].copy_from_slice(&bit_len.to_be_bytes());
        let block = self.buf;
        Self::compress(&mut self.state, &block);

        let mut out = [0u8; 32];
        for (i, word) in self.state.iter().enumerate() {
            out[i * 4..(i + 1) * 4].copy_from_slice(&word.to_be_bytes());
        }
        out
    }

    pub fn finalize_hex(self) -> String {
        const HEX: &[u8; 16] = b"0123456789abcdef";
        let digest = self.finalize();
        let mut s = String::with_capacity(64);
        for b in digest {
            s.push(HEX[(b >> 4) as usize] as char);
            s.push(HEX[(b & 0x0f) as usize] as char);
        }
        s
    }

    fn compress(state: &mut [u32; 8], block: &[u8; 64]) {
        let mut w = [0u32; 64];
        for i in 0..16 {
            w[i] = u32::from_be_bytes([
                block[i * 4],
                block[i * 4 + 1],
                block[i * 4 + 2],
                block[i * 4 + 3],
            ]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }

        let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut h] = *state;
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ ((!e) & g);
            let temp1 = h
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(SHA256_K[i])
                .wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let temp2 = s0.wrapping_add(maj);

            h = g;
            g = f;
            f = e;
            e = d.wrapping_add(temp1);
            d = c;
            c = b;
            b = a;
            a = temp1.wrapping_add(temp2);
        }

        state[0] = state[0].wrapping_add(a);
        state[1] = state[1].wrapping_add(b);
        state[2] = state[2].wrapping_add(c);
        state[3] = state[3].wrapping_add(d);
        state[4] = state[4].wrapping_add(e);
        state[5] = state[5].wrapping_add(f);
        state[6] = state[6].wrapping_add(g);
        state[7] = state[7].wrapping_add(h);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sha256_hex(data: &[u8]) -> String {
        let mut h = Sha256::new();
        h.update(data);
        h.finalize_hex()
    }

    #[test]
    fn sha256_matches_nist_test_vectors_and_streaming_boundaries() {
        assert_eq!(
            sha256_hex(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(
            sha256_hex(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"
        );
        // 1,000,000 'a' bytes streamed in odd-sized chunks across 64-byte block boundaries.
        let mut h = Sha256::new();
        let chunk = [b'a'; 137];
        let mut remaining = 1_000_000usize;
        while remaining > 0 {
            let n = remaining.min(chunk.len());
            h.update(&chunk[..n]);
            remaining -= n;
        }
        assert_eq!(
            h.finalize_hex(),
            "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"
        );
    }

    #[test]
    fn semver_parsing_and_comparison_handle_v_prefix_and_prereleases() {
        assert_eq!(parse_semver("0.1.0"), Some((0, 1, 0)));
        assert_eq!(parse_semver("v2.3.14"), Some((2, 3, 14)));
        assert_eq!(parse_semver("v1.2.0-rc.1+build.9"), Some((1, 2, 0)));
        assert_eq!(parse_semver("invalid"), None);
        assert_eq!(parse_semver("1.2.3.4"), None);

        assert_eq!(
            parse_stable_version("v2.1.0"),
            Some(("2.1.0".into(), (2, 1, 0)))
        );
        assert_eq!(parse_stable_version("v2.1.0-rc.1"), None);

        assert!(is_newer_version("0.1.0", "v0.2.0"));
        assert!(is_newer_version("0.1.0", "0.1.1"));
        assert!(is_newer_version("1.0.0-beta.1", "v1.0.0"));
        assert!(!is_newer_version("0.2.0", "v0.2.0"));
        assert!(!is_newer_version("0.2.0", "v0.1.9"));
        assert!(!is_newer_version("1.0.0", "v1.0.0-rc.1"));
        assert!(!is_newer_version("0.1.0+git-1", "v0.1.0"));
    }

    #[test]
    fn expected_asset_name_selects_exact_platform_and_variant() {
        assert_eq!(
            expected_asset_name("2.2.0", "macos", "aarch64", true).as_deref(),
            Some("PulseScribe-2.2.0-macOS-arm64-Full.dmg")
        );
        assert_eq!(
            expected_asset_name("2.2.0", "macos", "aarch64", false).as_deref(),
            Some("PulseScribe-2.2.0-macOS-arm64-Slim.dmg")
        );
        assert_eq!(
            expected_asset_name("2.2.0", "windows", "x86_64", true).as_deref(),
            Some("PulseScribe-2.2.0-Windows-x64-Full-Setup.exe")
        );
        assert_eq!(
            expected_asset_name("2.2.0", "windows", "x86_64", false).as_deref(),
            Some("PulseScribe-2.2.0-Windows-x64-Slim-Setup.exe")
        );
        assert_eq!(expected_asset_name("2.2.0", "linux", "x86_64", false), None);
    }

    #[test]
    fn parse_release_offer_validates_asset_digest_and_strips_install_notes() {
        let digest = "a".repeat(64);
        let release = GitHubRelease {
            tag_name: "v2.2.0".to_owned(),
            draft: false,
            prerelease: false,
            html_url: Some("https://github.com/KLIEBHAN/pulsescribe/releases/tag/v2.2.0".into()),
            body: Some(
                "Highlights here.\n\n<!-- install-notes -->\n\nManual install steps.".into(),
            ),
            assets: vec![GitHubReleaseAsset {
                name: "PulseScribe-2.2.0-macOS-arm64-Full.dmg".to_owned(),
                size: 19_000_000,
                digest: Some(format!("sha256:{digest}")),
            }],
        };

        let offer = parse_release_offer(&release, "2.1.0", "macos", "aarch64", true)
            .expect("offer parses")
            .expect("update available");
        assert_eq!(offer.version, "2.2.0");
        assert_eq!(offer.release_notes, "Highlights here.");
        assert_eq!(
            offer.asset_name.as_deref(),
            Some("PulseScribe-2.2.0-macOS-arm64-Full.dmg")
        );
        assert_eq!(
            offer.asset_url.as_deref(),
            Some("https://github.com/KLIEBHAN/pulsescribe/releases/download/v2.2.0/PulseScribe-2.2.0-macOS-arm64-Full.dmg")
        );
        assert_eq!(offer.sha256.as_deref(), Some(digest.as_str()));

        // Same version returns Ok(None).
        assert!(
            parse_release_offer(&release, "2.2.0", "macos", "aarch64", true)
                .unwrap()
                .is_none()
        );

        // Missing Slim asset returns an error instead of silently offering Full.
        assert!(parse_release_offer(&release, "2.1.0", "macos", "aarch64", false).is_err());
    }

    #[test]
    fn parse_sha256sums_handles_bom_and_binary_marker() {
        let h1 = "0123456789abcdef".repeat(4);
        let h2 = "fedcba9876543210".repeat(4);
        let text = format!(
            "\u{feff}{h1}  PulseScribe-2.2.0-macOS-arm64-Full.dmg\n{h2} *PulseScribe-2.2.0-Windows-x64-Slim-Setup.exe\n"
        );
        assert_eq!(
            parse_sha256sums(&text, "PulseScribe-2.2.0-macOS-arm64-Full.dmg").as_deref(),
            Some(h1.as_str())
        );
        assert_eq!(
            parse_sha256sums(&text, "PulseScribe-2.2.0-Windows-x64-Slim-Setup.exe").as_deref(),
            Some(h2.as_str())
        );
        assert_eq!(parse_sha256sums(&text, "other.dmg"), None);
    }
}
