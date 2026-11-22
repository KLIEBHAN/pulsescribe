//! Lightweight GitHub Releases SemVer update checker for PulseScribe desktop.

use std::time::Duration;

use reqwest::Client;
use serde::{Deserialize, Serialize};

const GITHUB_LATEST_RELEASE_URL: &str =
    "https://api.github.com/repos/KLIEBHAN/pulsescribe/releases/latest";
const UPDATE_CHECK_TIMEOUT: Duration = Duration::from_secs(10);

/// Result returned to the UI when checking for a newer PulseScribe release.
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
struct GitHubRelease {
    tag_name: String,
    html_url: Option<String>,
    body: Option<String>,
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

/// Query GitHub Releases for the latest PulseScribe tag without downloading or installing anything.
pub async fn check_latest_release(client: &Client, current_version: &str) -> UpdateCheckResult {
    let response = client
        .get(GITHUB_LATEST_RELEASE_URL)
        .timeout(UPDATE_CHECK_TIMEOUT)
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
            let notes = release.body.map(|body| {
                let trimmed = body.trim();
                if trimmed.chars().count() > 800 {
                    let prefix: String = trimmed.chars().take(799).collect();
                    format!("{}…", prefix.trim_end())
                } else {
                    trimmed.to_owned()
                }
            });
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn semver_parsing_and_comparison_handle_v_prefix_and_prereleases() {
        assert_eq!(parse_semver("0.1.0"), Some((0, 1, 0)));
        assert_eq!(parse_semver("v2.3.14"), Some((2, 3, 14)));
        assert_eq!(parse_semver("v1.2.0-rc.1+build.9"), Some((1, 2, 0)));
        assert_eq!(parse_semver("invalid"), None);
        assert_eq!(parse_semver("1.2.3.4"), None);

        assert!(is_newer_version("0.1.0", "v0.2.0"));
        assert!(is_newer_version("0.1.0", "0.1.1"));
        assert!(is_newer_version("1.0.0-beta.1", "v1.0.0"));
        assert!(!is_newer_version("0.2.0", "v0.2.0"));
        assert!(!is_newer_version("0.2.0", "v0.1.9"));
        assert!(!is_newer_version("1.0.0", "v1.0.0-rc.1"));
        assert!(!is_newer_version("0.1.0+git-1", "v0.1.0"));
    }
}
