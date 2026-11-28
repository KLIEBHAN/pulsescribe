//! Deterministic, precision-first local post-transcription vocabulary correction.
//!
//! Applies canonical spellings from `~/.pulsescribe/vocabulary.json` (`keywords`)
//! after ASR transcription (before optional LLM refinement). Designed for German
//! and mixed German/English dictation where offline models such as
//! `parakeet-ultra` lack reliable decoder-level hotword biasing.
//!
//! Matching runs in two tiers, prioritizing zero false positives over aggressive
//! recall:
//! 1. **Exact collapsed alphanumeric match** — fixes casing, word splits, and
//!    hyphens (e.g. `Pulsescribe` -> `PulseScribe`, `Open Router` -> `OpenRouter`,
//!    `Strato 2` -> `strato2`, `Whisper RS` -> `whisper-rs`).
//! 2. **Conservative DE/EN phonetic + Damerau-Levenshtein match** — fixes common
//!    ASR misspellings of longer terms (>= 6 alnum chars) when phonetic distance,
//!    consonant skeleton, capitalization, and string similarity all pass strict
//!    thresholds (e.g. `Pulses Kriebe` -> `PulseScribe`, `Fabian Klipan` ->
//!    `Fabian Kliebhan`, `Forgeo` -> `Forgejo`, `Sharpa ONNX` -> `Sherpa ONNX`).

use std::collections::HashMap;

use crate::vocabulary::casefold_key;

/// Common German and English function words that must never form the boundary
/// of a multi-word fuzzy replacement span.
const BOUNDARY_STOPWORDS: &[&str] = &[
    "aber", "about", "after", "als", "am", "an", "and", "are", "auch", "auf", "aus", "before",
    "bei", "between", "bis", "bitte", "but", "can", "da", "danke", "dann", "das", "dass", "dem",
    "den", "denn", "der", "des", "die", "dort", "du", "durch", "ein", "eine", "einem", "einen",
    "einer", "eines", "er", "es", "euch", "for", "from", "für", "gegen", "haben", "had", "has",
    "hat", "have", "here", "heute", "hier", "ich", "ihr", "im", "in", "into", "ist", "ja", "jetzt",
    "kann", "mal", "man", "mehr", "mit", "muss", "nach", "nein", "nicht", "noch", "not", "nur",
    "ob", "oder", "ohne", "onto", "our", "over", "schon", "sehr", "seit", "sich", "sie", "sind",
    "so", "soll", "than", "that", "the", "their", "then", "there", "this", "um", "und", "under",
    "uns", "unter", "von", "vor", "war", "was", "weil", "wenn", "wer", "were", "what", "when",
    "where", "wie", "will", "wir", "wird", "with", "wo", "you", "your", "zu", "zum", "zur", "über",
];

const PHONETIC_DIGRAPHS: &[(&str, &str)] = &[
    ("sch", "s"),
    ("sh", "s"),
    ("ch", "k"),
    ("ck", "k"),
    ("ph", "f"),
    ("th", "t"),
    ("dt", "t"),
    ("qu", "kw"),
    ("ks", "x"),
    ("cs", "x"),
    ("tz", "z"),
    ("ie", "i"),
    ("ei", "ai"),
    ("ey", "ai"),
    ("ay", "ai"),
    ("ee", "i"),
    ("oo", "u"),
    ("ou", "u"),
    ("aw", "a"),
    ("au", "a"),
    ("eu", "oi"),
];

#[derive(Debug, Clone, Copy)]
struct TokenSpan<'a> {
    start: usize,
    end: usize,
    text: &'a str,
}

#[derive(Debug)]
struct VocabEntry<'a> {
    raw: &'a str,
    leading_punct: &'a str,
    trailing_punct: &'a str,
    col_chars: Vec<char>,
    phon_chars: Vec<char>,
    skel_chars: Vec<char>,
    word_count: usize,
    starts_upper: bool,
    distinctive: bool,
}

#[derive(Debug, Clone, Copy)]
struct MatchCandidate<'a> {
    /// 3 = exact verbatim lock, 2 = Tier-1 collapsed match, 1 = Tier-2 fuzzy match.
    priority: u8,
    token_len: usize,
    replace_start: usize,
    replace_end: usize,
    score: f64,
    replacement: &'a str,
}

/// Applies deterministic vocabulary corrections to `transcript` using canonical
/// terms from `keywords`.
///
/// Returns `transcript` unchanged when `keywords` is empty or no span passes
/// the precision guards. Idempotent: applying twice yields the same output.
pub fn apply_vocabulary_corrections(transcript: &str, keywords: &[String]) -> String {
    if transcript.trim().is_empty() || keywords.is_empty() {
        return transcript.to_owned();
    }

    let index = VocabIndex::build(keywords);
    if index.entries.is_empty() {
        return transcript.to_owned();
    }

    let tokens = tokenize(transcript);
    if tokens.is_empty() {
        return transcript.to_owned();
    }

    let mut out = String::with_capacity(transcript.len() + 32);
    let mut last_pos = 0usize;
    let mut i = 0usize;
    let n = tokens.len();

    while i < n {
        let mut best: Option<(usize, MatchCandidate<'_>)> = None;
        let max_end = n.min(i + index.max_span_tokens);

        for j in (i + 1..=max_end).rev() {
            if !span_has_valid_gaps(transcript, &tokens[i..j]) {
                continue;
            }
            if let Some(candidate) = index.match_span(transcript, last_pos, &tokens[i..j]) {
                let replace_best = match best {
                    None => true,
                    Some((_, prev)) => is_better_candidate(candidate, prev),
                };
                if replace_best {
                    best = Some((j, candidate));
                }
                if candidate.priority >= 2 {
                    break;
                }
            }
        }

        if let Some((end_idx, candidate)) = best {
            out.push_str(&transcript[last_pos..candidate.replace_start]);
            out.push_str(candidate.replacement);
            last_pos = candidate.replace_end;
            i = end_idx;
        } else {
            i += 1;
        }
    }

    out.push_str(&transcript[last_pos..]);
    out
}

fn is_better_candidate(a: MatchCandidate<'_>, b: MatchCandidate<'_>) -> bool {
    let a_bytes = a.replace_end.saturating_sub(a.replace_start);
    let b_bytes = b.replace_end.saturating_sub(b.replace_start);
    match a.priority.cmp(&b.priority) {
        std::cmp::Ordering::Greater => true,
        std::cmp::Ordering::Less => false,
        std::cmp::Ordering::Equal => match a.token_len.cmp(&b.token_len) {
            std::cmp::Ordering::Greater => true,
            std::cmp::Ordering::Less => false,
            std::cmp::Ordering::Equal => match a_bytes.cmp(&b_bytes) {
                std::cmp::Ordering::Greater => true,
                std::cmp::Ordering::Less => false,
                std::cmp::Ordering::Equal => a.score > b.score,
            },
        },
    }
}

struct VocabIndex<'a> {
    entries: Vec<VocabEntry<'a>>,
    exact_map: HashMap<String, Vec<usize>>,
    fuzzy_buckets: HashMap<char, Vec<usize>>,
    max_span_tokens: usize,
}

impl<'a> VocabIndex<'a> {
    fn build(keywords: &'a [String]) -> Self {
        let mut entries: Vec<VocabEntry<'a>> = Vec::with_capacity(keywords.len());
        let mut exact_map: HashMap<String, Vec<usize>> = HashMap::with_capacity(keywords.len());
        let mut fuzzy_buckets: HashMap<char, Vec<usize>> = HashMap::new();
        let mut max_kw_words = 1usize;

        for raw in keywords {
            let kw = raw.trim();
            if kw.is_empty() {
                continue;
            }
            let col = collapse_alnum(kw);
            if col.is_empty() {
                continue;
            }
            let (leading_punct, trailing_punct) = split_boundary_punct(kw);
            if exact_map.get(&col).is_some_and(|indices| {
                indices.iter().any(|&idx| {
                    entries[idx].leading_punct == leading_punct
                        && entries[idx].trailing_punct == trailing_punct
                })
            }) {
                continue;
            }

            let word_count = tokenize(kw).len().max(1);
            max_kw_words = max_kw_words.max(word_count);

            let col_chars: Vec<char> = col.chars().collect();
            let phon_chars: Vec<char> = phonetic_de_en(kw).chars().collect();
            let skel_chars = consonant_skeleton(&phon_chars);

            let idx = entries.len();
            exact_map.entry(col).or_default().push(idx);

            if leading_punct.is_empty() && trailing_punct.is_empty() && col_chars.len() >= 6 {
                if let Some(&first_phon) = phon_chars.first() {
                    fuzzy_buckets.entry(first_phon).or_default().push(idx);
                }
            }

            entries.push(VocabEntry {
                raw: kw,
                leading_punct,
                trailing_punct,
                col_chars,
                phon_chars,
                skel_chars,
                word_count,
                starts_upper: kw
                    .chars()
                    .find(|ch| ch.is_alphabetic())
                    .is_some_and(char::is_uppercase),
                distinctive: is_distinctive(kw),
            });
        }

        Self {
            entries,
            exact_map,
            fuzzy_buckets,
            // Allow at least 3 tokens for exact split matching (e.g. "Post Gres QL" ->
            // "PostgreSQL") regardless of whether unrelated multi-word keywords exist.
            max_span_tokens: (max_kw_words + 1).clamp(3, 4),
        }
    }

    fn match_span(
        &self,
        transcript: &str,
        last_pos: usize,
        span_tokens: &[TokenSpan<'_>],
    ) -> Option<MatchCandidate<'a>> {
        let base_start = span_tokens[0].start;
        let base_end = span_tokens[span_tokens.len() - 1].end;
        let raw_span = &transcript[base_start..base_end];
        let col = collapse_alnum(raw_span);
        if col.is_empty() {
            return None;
        }
        let span_len = span_tokens.len();
        let span_has_upper = raw_span.chars().any(char::is_uppercase);

        // Tier 1: exact collapsed alphanumeric match (fixes casing, splits, hyphens),
        // including canonical leading/trailing symbols (e.g. `C++`, `C#`, `.NET`).
        if let Some(indices) = self.exact_map.get(&col) {
            let mut best_exact: Option<MatchCandidate<'a>> = None;
            for &idx in indices {
                let entry = &self.entries[idx];
                let Some((replace_start, replace_end)) =
                    match_boundary_punct(transcript, last_pos, base_start, base_end, entry)
                else {
                    continue;
                };
                let full_span = &transcript[replace_start..replace_end];
                let candidate = if full_span == entry.raw {
                    MatchCandidate {
                        priority: 3,
                        token_len: span_len,
                        replace_start,
                        replace_end,
                        score: 1.0,
                        replacement: entry.raw,
                    }
                } else {
                    if entry.starts_upper && !span_has_upper {
                        continue;
                    }
                    if span_len == 1 && !entry.distinctive && !entry.starts_upper && span_has_upper
                    {
                        continue;
                    }
                    if span_len > 1
                        && entry.word_count == 1
                        && !entry.distinctive
                        && entry.col_chars.len() < 7
                    {
                        continue;
                    }
                    MatchCandidate {
                        priority: 2,
                        token_len: span_len,
                        replace_start,
                        replace_end,
                        score: 1.0,
                        replacement: entry.raw,
                    }
                };
                if best_exact.is_none_or(|prev| is_better_candidate(candidate, prev)) {
                    best_exact = Some(candidate);
                }
            }
            if best_exact.is_some() {
                return best_exact;
            }
        }

        // Tier 2: conservative DE/EN phonetic + consonant-skeleton + DL fuzzy match.
        let col_chars: Vec<char> = col.chars().collect();
        if col_chars.len() < 5 {
            return None;
        }
        if span_len > 1
            && (is_stopword(span_tokens[0].text) || is_stopword(span_tokens[span_len - 1].text))
        {
            return None;
        }

        let phon_chars: Vec<char> = phonetic_de_en(raw_span).chars().collect();
        if phon_chars.len() < 4 {
            return None;
        }
        let first_phon = phon_chars[0];
        let bucket = self.fuzzy_buckets.get(&first_phon)?;
        let span_skel = consonant_skeleton(&phon_chars);

        let mut best: Option<MatchCandidate<'a>> = None;

        for &idx in bucket {
            let entry = &self.entries[idx];
            let kw_len = entry.col_chars.len();
            if span_len > entry.word_count + 1 {
                continue;
            }
            if entry.starts_upper && !span_has_upper {
                continue;
            }
            let max_len_delta = (kw_len / 4).max(2);
            if col_chars.len().abs_diff(kw_len) > max_len_delta {
                continue;
            }

            let p_dist = damerau_levenshtein(&phon_chars, &entry.phon_chars);
            if p_dist > 1 {
                continue;
            }

            let sim = dl_similarity(&col_chars, &entry.col_chars);
            if p_dist == 0 {
                if sim < 0.70 {
                    continue;
                }
            } else if span_skel == entry.skel_chars {
                // Vowel-only difference: require >= 7 alnum chars to avoid short-word collisions
                // like German "Decker"/"Dicker" -> "Docker" or "Strata" -> "Strato".
                if kw_len < 7 || sim < 0.75 {
                    continue;
                }
            } else if kw_len < 9 || sim < 0.78 {
                continue;
            }

            let score = sim - 0.08 * (p_dist as f64)
                + if span_len == entry.word_count {
                    0.02
                } else {
                    0.0
                };
            let candidate = MatchCandidate {
                priority: 1,
                token_len: span_len,
                replace_start: base_start,
                replace_end: base_end,
                score,
                replacement: entry.raw,
            };
            if best.is_none_or(|prev| candidate.score > prev.score) {
                best = Some(candidate);
            }
        }

        best
    }
}

fn split_boundary_punct(keyword: &str) -> (&str, &str) {
    let Some((first_start, _)) = keyword.char_indices().find(|&(_, ch)| ch.is_alphanumeric())
    else {
        return ("", "");
    };
    let Some((last_start, last_ch)) = keyword
        .char_indices()
        .rev()
        .find(|&(_, ch)| ch.is_alphanumeric())
    else {
        return ("", "");
    };
    (
        &keyword[..first_start],
        &keyword[last_start + last_ch.len_utf8()..],
    )
}

/// Verifies and extends `[base_start..base_end]` to cover canonical leading and
/// trailing punctuation belonging to `entry` (e.g. `.` in `.NET`, `++` in `C++`).
fn match_boundary_punct(
    transcript: &str,
    last_pos: usize,
    base_start: usize,
    base_end: usize,
    entry: &VocabEntry<'_>,
) -> Option<(usize, usize)> {
    let replace_start = if entry.leading_punct.is_empty() {
        base_start
    } else {
        let prefix = &transcript[last_pos..base_start];
        if !prefix.ends_with(entry.leading_punct) {
            return None;
        }
        let start = base_start - entry.leading_punct.len();
        // Avoid matching `.NET` inside identifiers or domains like `example.NET`.
        if transcript[..start]
            .chars()
            .next_back()
            .is_some_and(char::is_alphanumeric)
        {
            return None;
        }
        start
    };

    let replace_end = if entry.trailing_punct.is_empty() {
        base_end
    } else {
        let suffix = &transcript[base_end..];
        if !suffix.starts_with(entry.trailing_punct) {
            return None;
        }
        let end = base_end + entry.trailing_punct.len();
        if transcript[end..]
            .chars()
            .next()
            .is_some_and(char::is_alphanumeric)
        {
            return None;
        }
        end
    };

    Some((replace_start, replace_end))
}

/// Splits text into alphanumeric word tokens, preserving internal apostrophes
/// (e.g. `O'Connor`, `don't`) while splitting on hyphens so compounds such as
/// `Pulsescribe-App` or `whisper-rs` can match either whole or by component.
fn tokenize(text: &str) -> Vec<TokenSpan<'_>> {
    let mut tokens = Vec::new();
    let mut iter = text.char_indices().peekable();

    while let Some(&(start, ch)) = iter.peek() {
        if !ch.is_alphanumeric() {
            iter.next();
            continue;
        }
        let mut end = start + ch.len_utf8();
        iter.next();

        while let Some(&(idx, next_ch)) = iter.peek() {
            if next_ch.is_alphanumeric() {
                end = idx + next_ch.len_utf8();
                iter.next();
            } else if next_ch == '\'' {
                let mut lookahead = iter.clone();
                lookahead.next();
                if lookahead.peek().is_some_and(|&(_, c)| c.is_alphanumeric()) {
                    end = idx + next_ch.len_utf8();
                    iter.next();
                } else {
                    break;
                }
            } else {
                break;
            }
        }

        tokens.push(TokenSpan {
            start,
            end,
            text: &text[start..end],
        });
    }

    tokens
}

fn span_has_valid_gaps(text: &str, tokens: &[TokenSpan<'_>]) -> bool {
    for pair in tokens.windows(2) {
        let gap = &text[pair[0].end..pair[1].start];
        if gap.is_empty() || gap.chars().any(|ch| !matches!(ch, ' ' | '\t' | '-')) {
            return false;
        }
    }
    true
}

fn collapse_alnum(input: &str) -> String {
    casefold_key(input)
        .chars()
        .filter(|ch| ch.is_alphanumeric())
        .collect()
}

fn is_distinctive(keyword: &str) -> bool {
    if keyword
        .chars()
        .any(|ch| ch.is_ascii_digit() || matches!(ch, '-' | '_' | '.' | '/' | '+'))
    {
        return true;
    }
    let mut letters = keyword.chars().filter(|ch| ch.is_alphabetic());
    let Some(first) = letters.next() else {
        return false;
    };
    let mut count = 1usize;
    let mut all_upper = first.is_uppercase();
    let mut has_internal_upper = false;
    for ch in letters {
        count += 1;
        if ch.is_uppercase() {
            has_internal_upper = true;
        } else {
            all_upper = false;
        }
    }
    (count >= 2 && all_upper) || has_internal_upper
}

fn is_stopword(word: &str) -> bool {
    let folded = casefold_key(word);
    BOUNDARY_STOPWORDS.binary_search(&folded.as_str()).is_ok()
}

/// Normalizes German and English words into a compact phonetic key.
///
/// Keeps `w`/`v` distinct from `f` so words like `fireguard` do not collide
/// with `WireGuard`, while folding `v -> w` so `Lavacrawl`/`Lawacrawl` and
/// `ECOVIS`/`Ekovis` match cleanly.
fn phonetic_de_en(input: &str) -> String {
    let mut s = String::with_capacity(input.len());
    for ch in casefold_key(input).chars() {
        match ch {
            'ä' => s.push('e'),
            'ö' => s.push('o'),
            'ü' => s.push('u'),
            c if c.is_alphanumeric() => s.push(c),
            _ => {}
        }
    }

    for &(from, to) in PHONETIC_DIGRAPHS {
        if s.contains(from) {
            s = s.replace(from, to);
        }
    }

    let mut out = String::with_capacity(s.len());
    let mut last: Option<char> = None;

    for (idx, ch) in s.chars().enumerate() {
        let mapped = match ch {
            'c' | 'q' => 'k',
            'y' | 'j' => 'i',
            'v' => 'w',
            'd' => 't',
            'b' => 'p',
            'g' => 'k',
            'z' => 's',
            'h' if idx > 0 => continue,
            other => other,
        };
        if last != Some(mapped) {
            out.push(mapped);
            last = Some(mapped);
        }
    }

    out
}

fn consonant_skeleton(phon: &[char]) -> Vec<char> {
    let mut skel = Vec::with_capacity(phon.len());
    for (idx, &ch) in phon.iter().enumerate() {
        if idx == 0 || !matches!(ch, 'a' | 'e' | 'i' | 'o' | 'u') {
            skel.push(ch);
        }
    }
    skel
}

/// Optimal String Alignment (restricted Damerau-Levenshtein) distance using
/// three flat row buffers (no 2D allocations).
fn damerau_levenshtein(a: &[char], b: &[char]) -> usize {
    let la = a.len();
    let lb = b.len();
    if a == b {
        return 0;
    }
    if la == 0 {
        return lb;
    }
    if lb == 0 {
        return la;
    }

    let mut prev2 = vec![0usize; lb + 1];
    let mut prev1: Vec<usize> = (0..=lb).collect();
    let mut curr = vec![0usize; lb + 1];

    for i in 1..=la {
        curr[0] = i;
        for j in 1..=lb {
            let cost = usize::from(a[i - 1] != b[j - 1]);
            let mut val = (prev1[j] + 1).min(curr[j - 1] + 1).min(prev1[j - 1] + cost);
            if i > 1 && j > 1 && a[i - 1] == b[j - 2] && a[i - 2] == b[j - 1] {
                val = val.min(prev2[j - 2] + 1);
            }
            curr[j] = val;
        }
        std::mem::swap(&mut prev2, &mut prev1);
        std::mem::swap(&mut prev1, &mut curr);
    }

    prev1[lb]
}

fn dl_similarity(a: &[char], b: &[char]) -> f64 {
    let max_len = a.len().max(b.len());
    if max_len == 0 {
        1.0
    } else {
        1.0 - (damerau_levenshtein(a, b) as f64) / (max_len as f64)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_vocab() -> Vec<String> {
        [
            "PulseScribe",
            "TATEX GmbH",
            "TATEX",
            "Fabian Kliebhan",
            "Kliebhan",
            "Tauri",
            "Rust",
            "Blackmine",
            "Forgejo",
            "Lavacrawl",
            "strato2",
            "Bark",
            "Lexware",
            "Parakeet Ultra",
            "Parakeet",
            "Sherpa ONNX",
            "whisper-rs",
            "Deepgram",
            "Groq",
            "OpenRouter",
            "Antigravity",
            "Tailscale",
            "Synology",
            "DiskStation",
            "Nextcloud",
            "Cloudflare",
            "Hetzner",
            "macOS",
            "iPhone",
            "TypeScript",
            "Kubernetes",
            "PostgreSQL",
            "Cloudflared",
            "WireGuard",
            "BorgBackup",
            "ECOVIS",
            "Strato",
            "GitHub",
            "Homebrew",
            "Docker",
            "Nginx",
            "FastAPI",
            "SQLite",
            "PyTorch",
            "Ollama",
            "Anthropic",
            "OpenAI",
            "Raycast",
            "Obsidian",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect()
    }

    #[test]
    fn stopwords_table_is_sorted_for_binary_search() {
        assert!(BOUNDARY_STOPWORDS.windows(2).all(|w| w[0] < w[1]));
    }

    #[test]
    fn fixes_real_german_and_mixed_asr_errors() {
        let vocab = sample_vocab();
        let cases = [
            (
                "Pulsescribe läuft mit Paraket Ultra auf Mac OS.",
                "PulseScribe läuft mit Parakeet Ultra auf macOS.",
            ),
            (
                "Wir nutzen Pulses Kriebe für die Transkription bei Tateks GmbH.",
                "Wir nutzen PulseScribe für die Transkription bei TATEX GmbH.",
            ),
            (
                "Fabian Klipan entwickelt Pulsescribe mit Tauri und Rust.",
                "Fabian Kliebhan entwickelt PulseScribe mit Tauri und Rust.",
            ),
            (
                "Fabian Klithan pusht nach Forgeo und öffnet ein Ticket in Black Mine.",
                "Fabian Kliebhan pusht nach Forgejo und öffnet ein Ticket in Blackmine.",
            ),
            (
                "Auf Strato 2 laufen Lavakrawl, Next Cloud und Borg Backup.",
                "Auf strato2 laufen Lavacrawl, Nextcloud und BorgBackup.",
            ),
            (
                "Die Buchhaltung machen wir in Lex Ware mit Ekovis.",
                "Die Buchhaltung machen wir in Lexware mit ECOVIS.",
            ),
            (
                "Sharpa ONNX und Whisper RS laufen lokal, Deep Gram und Open Router in der Cloud.",
                "Sherpa ONNX und whisper-rs laufen lokal, Deepgram und OpenRouter in der Cloud.",
            ),
            (
                "Per Tail Scale greifen wir auf die Disk Station von Synologie zu.",
                "Per Tailscale greifen wir auf die DiskStation von Synology zu.",
            ),
            (
                "Am I Phone kommt eine Bark-Nachricht an, wenn Antigravity fertig ist.",
                "Am iPhone kommt eine Bark-Nachricht an, wenn Antigravity fertig ist.",
            ),
            (
                "Unser Backend nutzt Fast API, Post Gres QL, SQL Lite und Type Script.",
                "Unser Backend nutzt FastAPI, PostgreSQL, SQLite und TypeScript.",
            ),
            (
                "Wir testen Kubernetis, Antropik, Open AI, Olama, Ray Cast und Obsidien.",
                "Wir testen Kubernetes, Anthropic, OpenAI, Ollama, Raycast und Obsidian.",
            ),
            (
                "Die Pulsescribe-App verbindet sich per Wire Guard mit Cloud Flare.",
                "Die PulseScribe-App verbindet sich per WireGuard mit Cloudflare.",
            ),
        ];

        for (input, expected) in cases {
            let actual = apply_vocabulary_corrections(input, &vocab);
            assert_eq!(actual, expected, "input: {input}");
            // Idempotence: a second pass must be a no-op.
            assert_eq!(apply_vocabulary_corrections(&actual, &vocab), actual);
        }
    }

    #[test]
    fn zero_false_positives_on_adversarial_german_and_english_prose() {
        let vocab = sample_vocab();
        let sentences = [
            "Der Hetzer hat im Park viel Rost auf der Gruppe gesehen.",
            "Die Taten waren im Text über die Taxe klar beschrieben.",
            "An den Klippen kleben viele Muscheln und wir essen ein Paket Kekse.",
            "Das Parkett hat eine Scherbe abbekommen in der Straße 2.",
            "Der Doktor sitzt auf dem Hocker beim Bäcker und trinkt Kaffee.",
            "Decker und Dicker wohnen neben dem Stratege an der Feuerwehr.",
            "Strata und Strati sind Begriffe aus der Geologie.",
            "We need an open router, a black mine shaft, and a tail scale measurement.",
            "The anti-gravity chamber sits near the desk station with a deep gram weight.",
            "He wore a fireguard helmet near the bark of the tree to stop the rust.",
            "Eine kleine Pause im Park hilft gegen den Stress bei der Arbeit.",
            "Bitte schick mir das Dokument per Mail bis heute Abend.",
        ];

        for sentence in sentences {
            assert_eq!(
                apply_vocabulary_corrections(sentence, &vocab),
                sentence,
                "false positive on: {sentence}"
            );
        }
    }

    #[test]
    fn preserves_punctuation_newlines_and_unicode_offsets() {
        let vocab = vec!["PulseScribe".to_owned(), "TATEX GmbH".to_owned()];
        let input = "Grüße! „Pulsescribe“, sagte er.\nBei Tateks GmbH (München) — top!";
        let expected = "Grüße! „PulseScribe“, sagte er.\nBei TATEX GmbH (München) — top!";
        assert_eq!(apply_vocabulary_corrections(input, &vocab), expected);
    }

    #[test]
    fn never_merges_across_sentence_punctuation() {
        let vocab = vec!["OpenRouter".to_owned(), "PulseScribe".to_owned()];
        let input = "Open. Router läuft! Pulse, Scribe auch.";
        assert_eq!(apply_vocabulary_corrections(input, &vocab), input);
    }

    #[test]
    fn empty_or_whitespace_inputs_are_passthrough() {
        let vocab = vec!["PulseScribe".to_owned()];
        assert_eq!(apply_vocabulary_corrections("", &vocab), "");
        assert_eq!(apply_vocabulary_corrections("   ", &vocab), "   ");
        assert_eq!(
            apply_vocabulary_corrections("Pulsescribe", &[]),
            "Pulsescribe"
        );
    }

    #[test]
    fn does_not_lowercase_sentence_start_for_plain_lowercase_keyword() {
        let vocab = vec!["rust".to_owned(), "strato2".to_owned()];
        assert_eq!(
            apply_vocabulary_corrections("Rust läuft auf Strato 2.", &vocab),
            "Rust läuft auf strato2."
        );
    }

    #[test]
    fn preserves_canonical_boundary_punctuation_without_duplication() {
        let vocab = vec![
            "C++".to_owned(),
            "C#".to_owned(),
            ".NET".to_owned(),
            "C".to_owned(),
        ];

        // Already-canonical punctuated terms must stay identical across repeated passes.
        let canonical = "I use C++, C#, and .NET (plus .NET).";
        let once = apply_vocabulary_corrections(canonical, &vocab);
        assert_eq!(once, canonical);
        assert_eq!(apply_vocabulary_corrections(&once, &vocab), canonical);

        // Casing fix preserves surrounding sentence punctuation and avoids duplicating `.`.
        assert_eq!(
            apply_vocabulary_corrections("We migrate .Net apps to C++.", &vocab),
            "We migrate .NET apps to C++."
        );

        // Plain words without the canonical symbols and domain suffixes stay untouched.
        let non_matches = "Option C has Net worth on example.NET.";
        assert_eq!(
            apply_vocabulary_corrections(non_matches, &vocab),
            non_matches
        );
    }

    #[test]
    fn splits_single_word_keyword_into_three_tokens_without_other_keywords() {
        let vocab = vec!["PostgreSQL".to_owned()];
        assert_eq!(
            apply_vocabulary_corrections("Unser Backend nutzt Post Gres QL.", &vocab),
            "Unser Backend nutzt PostgreSQL."
        );
    }
}
