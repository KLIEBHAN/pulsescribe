//! Shared overlay text formatting, bounded whitespace normalization, and reflow-free
//! multi-line interim wrapping.
//!
//! Mirrors `ui/overlay_text.py` and `utils/text_preview.py` using Unicode character
//! indices throughout so multi-byte text (German umlauts, CJK, emoji) never slices
//! mid-codepoint.

use serde::{Deserialize, Serialize};

pub const DEFAULT_RECORDING_INTERIM_MAX_CHARS: usize = 45;
/// Tail budget for overlays that fade clipped text on the left without `"..."`.
pub const FADED_RECORDING_INTERIM_MAX_CHARS: usize = 120;

const SENTENCE_END: [char; 7] = ['.', '!', '?', '…', '。', '！', '？'];
const ANCHOR_CHARS: usize = 24;
const LONGEST_WORD_CHARS: usize = 40;

/// Return the first `max_chars` characters of whitespace-normalized text without
/// scanning or allocating the rest of a long transcript (mirrors `utils/text_preview.py`).
pub fn normalized_text_prefix(text: &str, max_chars: usize) -> String {
    if max_chars == 0 {
        return String::new();
    }

    let mut out = String::new();
    let mut char_count = 0usize;
    let mut pending_space = false;

    for ch in text.chars() {
        if ch.is_whitespace() {
            if char_count > 0 {
                pending_space = true;
            }
            continue;
        }

        if pending_space {
            out.push(' ');
            char_count += 1;
            if char_count >= max_chars {
                break;
            }
            pending_space = false;
        }

        out.push(ch);
        char_count += 1;
        if char_count >= max_chars {
            break;
        }
    }

    out
}

/// Format a recording duration as `m:ss` (minutes are not capped; negative/NaN clamp to `0:00`).
pub fn format_recording_duration(seconds: f64) -> String {
    let total_secs = if seconds.is_finite() && seconds > 0.0 {
        seconds.trunc().min(u64::MAX as f64) as u64
    } else {
        0
    };
    let minutes = total_secs / 60;
    let secs = total_secs % 60;
    format!("{minutes}:{secs:02}")
}

/// Normalize whitespace and tail-truncate recording interim text.
pub fn format_recording_interim_text(text: &str, max_chars: usize, ellipsis: bool) -> String {
    if text.is_empty() {
        return String::new();
    }

    let (cleaned_tail, truncated) = normalized_tail(text, max_chars);
    if cleaned_tail.is_empty() || !truncated {
        return cleaned_tail.into_iter().collect();
    }

    if !ellipsis {
        let start = cleaned_tail.len().saturating_sub(max_chars);
        return cleaned_tail[start..].iter().collect();
    }

    ellipsis_tail(&cleaned_tail, max_chars)
}

/// Split formatted interim text into `(earlier, newest)` display parts so overlays
/// can emphasize words added since `previous_text`.
pub fn split_recording_interim_text(
    text: &str,
    previous_text: &str,
    max_chars: usize,
    ellipsis: bool,
) -> (String, String) {
    let formatted = format_recording_interim_text(text, max_chars, ellipsis);
    if formatted.is_empty() {
        return (String::new(), String::new());
    }

    let formatted_chars: Vec<char> = formatted.chars().collect();
    let raw_split = if !previous_text.is_empty() {
        if let Some(suffix) = text.strip_prefix(previous_text) {
            let added = collapse_whitespace(suffix);
            if added.is_empty() {
                return (formatted, String::new());
            }
            formatted_chars.len().saturating_sub(added.chars().count())
        } else {
            formatted_chars.len()
        }
    } else {
        0
    };

    let split_at = if raw_split > 0 {
        formatted_chars[..raw_split]
            .iter()
            .rposition(|&c| c == ' ')
            .map_or(0, |idx| idx + 1)
    } else {
        0
    };

    let earlier: String = formatted_chars[..split_at].iter().collect();
    let newest: String = formatted_chars[split_at..].iter().collect();
    (earlier, newest)
}

fn normalized_tail(text: &str, max_chars: usize) -> (Vec<char>, bool) {
    let mut reversed_chars = Vec::new();
    let mut pending_space = false;
    let mut truncated = false;

    for ch in text.chars().rev() {
        if ch.is_whitespace() {
            if !reversed_chars.is_empty() {
                pending_space = true;
            }
            continue;
        }

        if pending_space {
            reversed_chars.push(' ');
            pending_space = false;
        }

        reversed_chars.push(ch);
        if max_chars > 0 && reversed_chars.len() > max_chars {
            truncated = true;
            break;
        }
    }

    reversed_chars.reverse();
    (reversed_chars, truncated)
}

fn ellipsis_tail(cleaned_tail: &[char], max_chars: usize) -> String {
    if max_chars == 0 {
        return cleaned_tail.iter().collect();
    }
    if max_chars <= 3 {
        return "...".to_string();
    }
    let tail_chars = max_chars - 3;
    let start = cleaned_tail.len().saturating_sub(tail_chars);
    let mut out = String::from("...");
    out.extend(&cleaned_tail[start..]);
    out
}

fn collapse_whitespace(text: &str) -> String {
    let mut out = String::new();
    for (idx, word) in text.split_whitespace().enumerate() {
        if idx > 0 {
            out.push(' ');
        }
        out.push_str(word);
    }
    out
}

/// Wrapped live-text lines plus anchor state needed by the next incremental update.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct InterimLines {
    /// `(confirmed, fresh)` per visible line, oldest first.
    pub lines: Vec<(String, String)>,
    /// Whitespace-normalized text currently represented.
    pub source: String,
    /// Unicode character offset in `source` where the first visible line begins.
    pub start: usize,
}

/// Wrap growing live text into at most `max_lines` (>= 1) lines without reflowing
/// already-visible words when later text arrives.
pub fn wrap_interim_text<F>(
    text: &str,
    previous: Option<&InterimLines>,
    mut fits: F,
    max_lines: usize,
) -> InterimLines
where
    F: FnMut(&str) -> bool,
{
    let source = collapse_whitespace(text);
    if source.is_empty() {
        return InterimLines::default();
    }

    let max_lines = max_lines.max(1);
    let source_chars: Vec<char> = source.chars().collect();

    let mut start = 0usize;
    let mut confirmed = 0usize;
    if let Some(prev) = previous.filter(|p| !p.source.is_empty()) {
        let prev_chars: Vec<char> = prev.source.chars().collect();
        let prev_start = prev.start.min(prev_chars.len());
        let shown_chars = &prev_chars[prev_start..];
        let found = find_line_start(&source_chars, shown_chars, &prev_chars, prev_start);
        start = found.unwrap_or(0);
        let before = if found.is_none() {
            prev_chars.as_slice()
        } else {
            shown_chars
        };
        confirmed = start + common_word_prefix(before, &source_chars[start..]);
    }

    if source_chars
        .last()
        .is_some_and(|ch| SENTENCE_END.contains(ch))
    {
        confirmed = source_chars.len();
    }

    let start_byte = char_index_to_byte_offset(&source, start);
    let mut wrapped: Vec<(usize, String, usize)> = Vec::new(); // (char_offset, text, char_len)
    let mut offset = start;
    for word in source[start_byte..].split(' ') {
        let word_chars = word.chars().count();
        if let Some(last) = wrapped.last_mut() {
            let candidate = format!("{} {word}", last.1);
            if fits(&candidate) {
                last.1 = candidate;
                last.2 += 1 + word_chars;
                offset += word_chars + 1;
                continue;
            }
        }
        wrapped.extend(break_word(word, offset, &mut fits));
        offset += word_chars + 1;
    }

    let visible_start = wrapped.len().saturating_sub(max_lines);
    let visible = &wrapped[visible_start..];
    let lines = visible
        .iter()
        .map(|(at, line, _)| {
            let split_chars = confirmed.saturating_sub(*at);
            let split_byte = char_index_to_byte_offset(line, split_chars);
            (
                line[..split_byte].to_string(),
                line[split_byte..].to_string(),
            )
        })
        .collect();

    InterimLines {
        lines,
        source,
        start: visible[0].0,
    }
}

fn char_index_to_byte_offset(text: &str, char_index: usize) -> usize {
    text.char_indices()
        .nth(char_index)
        .map_or(text.len(), |(byte_idx, _)| byte_idx)
}

fn find_line_start(
    source_chars: &[char],
    shown_chars: &[char],
    prev_chars: &[char],
    prev_start: usize,
) -> Option<usize> {
    if prev_start == 0 {
        return Some(0);
    }
    let anchor_len = shown_chars.len().min(ANCHOR_CHARS);
    if anchor_len == 0 {
        return None;
    }
    let anchor = &shown_chars[..anchor_len];
    let found = find_occurrences(source_chars, anchor);
    let at = found
        .into_iter()
        .min_by_key(|&pos| pos.abs_diff(prev_start))?;
    let began_inside_word = prev_chars
        .get(prev_start.saturating_sub(1))
        .is_some_and(|&c| c != ' ');
    if at == 0 || source_chars[at - 1] == ' ' || began_inside_word {
        Some(at)
    } else {
        None
    }
}

fn find_occurrences(haystack: &[char], needle: &[char]) -> Vec<usize> {
    if needle.is_empty() || needle.len() > haystack.len() {
        return Vec::new();
    }
    haystack
        .windows(needle.len())
        .enumerate()
        .filter_map(|(idx, window)| (window == needle).then_some(idx))
        .collect()
}

fn break_word<F>(word: &str, offset: usize, fits: &mut F) -> Vec<(usize, String, usize)>
where
    F: FnMut(&str) -> bool,
{
    let word_chars = word.chars().count();
    if fits(word) {
        return vec![(offset, word.to_string(), word_chars)];
    }

    let mut pieces = Vec::new();
    let mut piece = String::new();
    let mut piece_chars = 0usize;

    for (index, ch) in word.chars().enumerate() {
        if !piece.is_empty() {
            let mut candidate = String::with_capacity(piece.len() + ch.len_utf8());
            candidate.push_str(&piece);
            candidate.push(ch);
            if !fits(&candidate) {
                pieces.push((offset + index - piece_chars, piece, piece_chars));
                piece = String::new();
                piece_chars = 0;
            }
        }
        piece.push(ch);
        piece_chars += 1;
    }
    pieces.push((offset + word_chars - piece_chars, piece, piece_chars));
    pieces
}

fn common_word_prefix(previous: &[char], current: &[char]) -> usize {
    let limit = previous.len().min(current.len());
    let same = (0..limit)
        .find(|&i| previous[i] != current[i])
        .unwrap_or(limit);
    let at_boundary = |slice: &[char]| same == slice.len() || slice[same] == ' ';
    if at_boundary(previous) && at_boundary(current) {
        return same;
    }
    let word_start = current[..same]
        .iter()
        .rposition(|&c| c == ' ')
        .map_or(0, |idx| idx + 1);
    if same - word_start <= LONGEST_WORD_CHARS {
        word_start
    } else {
        same
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn duration_and_prefix_handle_edge_cases_and_multibyte_unicode() {
        assert_eq!(format_recording_duration(-5.0), "0:00");
        assert_eq!(format_recording_duration(f64::NAN), "0:00");
        assert_eq!(format_recording_duration(65.9), "1:05");
        assert_eq!(format_recording_duration(3661.0), "61:01");

        assert_eq!(
            normalized_text_prefix("  Über   Straße\n\tgehen ", 8),
            "Über Str"
        );
        assert_eq!(normalized_text_prefix("hello", 0), "");
    }

    #[test]
    fn wrap_interim_text_keeps_line_positions_and_breaks_wide_unicode_words() {
        let fits = |line: &str| line.chars().count() <= 12;

        let first = wrap_interim_text("Hallo Welt", None, fits, 2);
        assert_eq!(first.lines, vec![(String::new(), "Hallo Welt".to_string())]);
        assert_eq!(first.start, 0);

        let grown = wrap_interim_text("Hallo Welt heute", Some(&first), fits, 2);
        assert_eq!(
            grown.lines,
            vec![
                ("Hallo Welt".to_string(), String::new()),
                (String::new(), "heute".to_string()),
            ]
        );

        let ended = wrap_interim_text("Hallo Welt heute.", Some(&grown), fits, 2);
        assert_eq!(
            ended.lines,
            vec![
                ("Hallo Welt".to_string(), String::new()),
                ("heute.".to_string(), String::new()),
            ]
        );

        // Unspaced CJK wraps by character without panicking on multi-byte boundaries.
        let cjk = wrap_interim_text(
            "日本語テスト文字列確認用データ",
            None,
            |s| s.chars().count() <= 6,
            2,
        );
        assert_eq!(cjk.lines.len(), 2);
    }
}
