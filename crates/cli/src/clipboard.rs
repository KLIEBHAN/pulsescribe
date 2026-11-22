//! Cross-platform clipboard export via `arboard` for `-c/--copy`.

/// Copy UTF-8 text (including umlauts and multi-line transcripts) into the system clipboard.
pub fn copy_to_clipboard(text: &str) -> Result<(), String> {
    let mut clipboard = arboard::Clipboard::new().map_err(|err| err.to_string())?;
    clipboard
        .set_text(text.to_owned())
        .map_err(|err| err.to_string())
}
