//! Shared visual constants, colors, shimmer track geometry, and horizontal layout
//! for the macOS and Windows recording overlays.
//!
//! Mirrors `ui/overlay_style.py`.

use serde::{Deserialize, Serialize};

use crate::animation::{BAR_COUNT, BAR_GAP, BAR_WIDTH};

pub const OVERLAY_HEIGHT: f64 = 44.0;
pub const OVERLAY_CORNER_RADIUS: f64 = 3.0;
pub const OVERLAY_PADDING_H: f64 = 16.0;
pub const OVERLAY_GAP: f64 = 12.0;
pub const OVERLAY_WIDTH: f64 = 400.0;
pub const OVERLAY_MAX_WIDTH_RATIO: f64 = 0.75;
pub const STATUS_SLOT_WIDTH: f64 = 84.0;
pub const TEXT_FADE_WIDTH: f64 = 30.0;

pub const LIVE_TEXT_WIDTH: f64 = 560.0;
pub const LIVE_TEXT_HEIGHT: f64 = 62.0;
pub const LIVE_TEXT_LINES: usize = 2;
pub const LIVE_TEXT_LINE_PITCH: f64 = 19.0;

pub const BACKGROUND_COLOR: &str = "#1b1b1b";
pub const BORDER_COLOR: &str = "#333333";
pub const TEXT_COLOR: &str = "#ececec";
pub const MUTED_TEXT_COLOR: &str = "#949494";
pub const INTERIM_TEXT_COLOR: &str = "#9a9a9a";

pub const TEXT_FONT_SIZE: f64 = 13.0;
pub const BADGE_FONT_SIZE: f64 = 12.5;
pub const META_FONT_SIZE: f64 = 12.5;
pub const META_TEXT_COLOR: &str = MUTED_TEXT_COLOR;
pub const LINK_TEXT_COLOR: &str = "#ffffff";
pub const LINK_HIT_SLOP: f64 = 4.0;
pub const BADGE_CORNER_RADIUS: f64 = 3.0;
pub const BADGE_PADDING_H: f64 = 8.0;
pub const BADGE_PADDING_V: f64 = 3.0;
pub const DISMISS_GLYPH: &str = "×";
pub const DISMISS_FONT_SIZE: f64 = 15.0;

pub const BAR_AREA_WIDTH: f64 = (BAR_COUNT as f64) * BAR_WIDTH + ((BAR_COUNT - 1) as f64) * BAR_GAP;
pub const LINE_HEIGHT: f64 = 3.0;

pub const PHASE_STEP_GAP: f64 = 3.0;
pub const PHASE_STEP_WIDTH: f64 = (BAR_AREA_WIDTH - PHASE_STEP_GAP) / 2.0;

pub const SHIMMER_BAND_WIDTH: f64 = 22.0;
pub const SHIMMER_PERIOD_SECONDS: f64 = 1.4;
pub const SHIMMER_HIGHLIGHT_COLOR: &str = "#f4f4f4";
pub const SHIMMER_ACTIVE_STEP_COLOR: &str = "#8a8a8a";
pub const SHIMMER_MAX_FPS: u32 = 30;

/// Status-slot bar visualization mode.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OverlayBarMode {
    #[default]
    None,
    /// Audio-driven traveling wave during `RECORDING`.
    Level,
    /// Low static/level-metered row during `LISTENING`.
    Low,
    /// Single flat processing line during `LOADING` or single-step `TRANSCRIBING`.
    Line,
    /// Two-step processing line with step 1 active (`TRANSCRIBING` before `REFINING`).
    Phase1,
    /// Two-step processing line with step 2 active (`REFINING`).
    Phase2,
}

impl OverlayBarMode {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::None => "none",
            Self::Level => "level",
            Self::Low => "low",
            Self::Line => "line",
            Self::Phase1 => "phase1",
            Self::Phase2 => "phase2",
        }
    }

    pub const fn bar_color(self) -> Option<&'static str> {
        match self {
            Self::Level => Some("#ececec"),
            Self::Low | Self::Line => Some("#6f6f6f"),
            Self::None | Self::Phase1 | Self::Phase2 => None,
        }
    }

    pub const fn phase_step_colors(self) -> Option<(&'static str, &'static str)> {
        match self {
            Self::Phase1 => Some(("#ececec", "#333333")),
            Self::Phase2 => Some(("#6f6f6f", "#ececec")),
            _ => None,
        }
    }
}

/// Outcome badge tone determining background and foreground hex colors.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OverlayTone {
    Success,
    Warn,
    Error,
}

impl OverlayTone {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Success => "success",
            Self::Warn => "warn",
            Self::Error => "error",
        }
    }

    /// `(background_hex, text_hex)` matching `ui/overlay_style.py:BADGE_TONES`.
    pub const fn badge_colors(self) -> (&'static str, &'static str) {
        match self {
            Self::Success => ("#173326", "#6fd39b"),
            Self::Warn => ("#3a2c12", "#f0b955"),
            Self::Error => ("#3a1a1a", "#f08a8a"),
        }
    }
}

/// Optional clickable action exposed in the overlay's right-aligned meta slot.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OverlayAction {
    Settings,
}

impl OverlayAction {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Settings => "settings",
        }
    }
}

/// Decoded 8-bit RGB color tuple.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct Rgb {
    pub r: u8,
    pub g: u8,
    pub b: u8,
}

/// Convert `#rrggbb` (or `rrggbb`) into RGB channels; returns `None` for invalid input.
pub fn hex_to_rgb(color: &str) -> Option<Rgb> {
    let hex = color.strip_prefix('#').unwrap_or(color);
    if hex.len() != 6 || !hex.is_ascii() {
        return None;
    }
    let r = u8::from_str_radix(&hex[0..2], 16).ok()?;
    let g = u8::from_str_radix(&hex[2..4], 16).ok()?;
    let b = u8::from_str_radix(&hex[4..6], 16).ok()?;
    Some(Rgb { r, g, b })
}

/// Shimmer track offset and width relative to the status slot origin.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct ShimmerTrack {
    pub x: f64,
    pub width: f64,
}

/// Return `(x, width)` of the shimmering portion of the status slot, or `None` when static.
pub const fn shimmer_track(mode: OverlayBarMode) -> Option<ShimmerTrack> {
    match mode {
        OverlayBarMode::Line => Some(ShimmerTrack {
            x: 0.0,
            width: BAR_AREA_WIDTH,
        }),
        OverlayBarMode::Phase1 => Some(ShimmerTrack {
            x: 0.0,
            width: PHASE_STEP_WIDTH,
        }),
        OverlayBarMode::Phase2 => Some(ShimmerTrack {
            x: PHASE_STEP_WIDTH + PHASE_STEP_GAP,
            width: PHASE_STEP_WIDTH,
        }),
        OverlayBarMode::None | OverlayBarMode::Level | OverlayBarMode::Low => None,
    }
}

/// Input measurements for `compute_overlay_layout`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct OverlayLayoutParams {
    pub text_width: f64,
    pub meta_width: f64,
    pub dismiss_width: f64,
    pub status_width: f64,
    pub grow: bool,
    pub max_width: f64,
    pub base_width: f64,
}

impl Default for OverlayLayoutParams {
    fn default() -> Self {
        Self {
            text_width: 0.0,
            meta_width: 0.0,
            dismiss_width: 0.0,
            status_width: 0.0,
            grow: false,
            max_width: OVERLAY_WIDTH,
            base_width: OVERLAY_WIDTH,
        }
    }
}

/// Horizontal zones of the overlay; absent optional parts are `None`.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct OverlayLayout {
    pub width: f64,
    pub status_x: f64,
    pub text_x: f64,
    pub text_width: f64,
    pub meta_x: Option<f64>,
    pub dismiss_x: Option<f64>,
}

fn finite_or_zero(value: f64) -> f64 {
    if value.is_finite() {
        value
    } else {
        0.0
    }
}

/// Lay out `[status] [text …] [meta] [×]` at `base_width`, growing up to `max_width`
/// only when `grow` is `true` (persistent messages).
pub fn compute_overlay_layout(params: &OverlayLayoutParams) -> OverlayLayout {
    let text_w = finite_or_zero(params.text_width);
    let meta_w = finite_or_zero(params.meta_width);
    let dismiss_w = finite_or_zero(params.dismiss_width);
    let status_w = finite_or_zero(params.status_width);
    let base_w = finite_or_zero(params.base_width);
    let max_w = finite_or_zero(params.max_width);

    let status_x = OVERLAY_PADDING_H;
    let text_x = status_x + STATUS_SLOT_WIDTH.max(status_w) + OVERLAY_GAP;

    let mut trailing = 0.0;
    if meta_w > 0.0 {
        trailing += OVERLAY_GAP + meta_w;
    }
    if dismiss_w > 0.0 {
        trailing += OVERLAY_GAP + dismiss_w;
    }

    let mut width = base_w;
    if params.grow {
        let natural = text_x + text_w.max(0.0) + trailing + OVERLAY_PADDING_H;
        width = width.max(natural.min(max_w));
    }

    let mut right = width - OVERLAY_PADDING_H;
    let mut dismiss_x = None;
    let mut meta_x = None;

    if dismiss_w > 0.0 {
        let x = right - dismiss_w;
        dismiss_x = Some(x);
        right = x - OVERLAY_GAP;
    }
    if meta_w > 0.0 {
        let x = right - meta_w;
        meta_x = Some(x);
        right = x - OVERLAY_GAP;
    }

    OverlayLayout {
        width,
        status_x,
        text_x,
        text_width: (right - text_x).max(0.0),
        meta_x,
        dismiss_x,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hex_to_rgb_and_shimmer_track_cover_valid_and_invalid_inputs() {
        assert_eq!(
            hex_to_rgb("#1b1b1b"),
            Some(Rgb {
                r: 27,
                g: 27,
                b: 27
            })
        );
        assert_eq!(
            hex_to_rgb("6fd39b"),
            Some(Rgb {
                r: 111,
                g: 211,
                b: 155
            })
        );
        assert_eq!(hex_to_rgb("#fff"), None);
        assert_eq!(hex_to_rgb("#zzzzzz"), None);

        assert_eq!(
            shimmer_track(OverlayBarMode::Line),
            Some(ShimmerTrack {
                x: 0.0,
                width: 57.0
            })
        );
        assert_eq!(
            shimmer_track(OverlayBarMode::Phase1),
            Some(ShimmerTrack {
                x: 0.0,
                width: 27.0
            })
        );
        assert_eq!(
            shimmer_track(OverlayBarMode::Phase2),
            Some(ShimmerTrack {
                x: 30.0,
                width: 27.0
            })
        );
        assert_eq!(shimmer_track(OverlayBarMode::Level), None);
    }

    #[test]
    fn layout_preserves_fixed_slot_right_aligns_meta_and_caps_growth() {
        let basic = compute_overlay_layout(&OverlayLayoutParams {
            text_width: 120.0,
            ..Default::default()
        });
        assert_eq!(basic.width, OVERLAY_WIDTH);
        assert_eq!(basic.status_x, 16.0);
        assert_eq!(basic.text_x, 112.0);
        assert_eq!(basic.text_width, 272.0);
        assert_eq!(basic.meta_x, None);
        assert_eq!(basic.dismiss_x, None);

        let grown = compute_overlay_layout(&OverlayLayoutParams {
            text_width: 800.0,
            meta_width: 50.0,
            dismiss_width: 14.0,
            status_width: 96.0,
            grow: true,
            max_width: 600.0,
            base_width: OVERLAY_WIDTH,
        });
        assert_eq!(grown.width, 600.0);
        assert_eq!(grown.text_x, 16.0 + 96.0 + 12.0);
        assert_eq!(grown.dismiss_x, Some(600.0 - 16.0 - 14.0));
        assert_eq!(grown.meta_x, Some(600.0 - 16.0 - 14.0 - 12.0 - 50.0));
    }
}
