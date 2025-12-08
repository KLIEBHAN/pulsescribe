"""LLM-Prompts und Kontext-Mappings für whisper_go.

Enthält alle Prompts für die LLM-Nachbearbeitung (Refine) sowie
das App-zu-Kontext Mapping für die automatische Kontext-Erkennung.
"""

# =============================================================================
# LLM-Prompts für Nachbearbeitung
# =============================================================================

DEFAULT_REFINE_PROMPT = """Korrigiere dieses Transkript:
- Entferne Füllwörter (ähm, also, quasi, sozusagen)
- Korrigiere Grammatik und Rechtschreibung
- Formatiere in saubere Absätze
- Behalte den originalen Inhalt und Stil bei

Gib NUR den korrigierten Text zurück, keine Erklärungen."""

# Voice-Commands für Diktat (werden in alle Prompts eingefügt)
VOICE_COMMANDS_INSTRUCTION = """
Interpretiere gesprochene Befehle und entferne sie aus dem Text:
- "neuer Absatz" / "new paragraph" → Absatz (doppelter Zeilenumbruch)
- "neue Zeile" / "new line" → Zeilenumbruch
- "Punkt" / "period" → Satz mit Punkt beenden
- "Komma" / "comma" → Komma einfügen
- "Fragezeichen" / "question mark" → Fragezeichen
- "Ausrufezeichen" / "exclamation mark" → Ausrufezeichen
- "Doppelpunkt" / "colon" → Doppelpunkt
- "Semikolon" / "semicolon" → Semikolon
Wende die Befehle an der gesprochenen Stelle an und entferne den Befehlstext."""

# Kontext-spezifische Prompts für LLM-Nachbearbeitung
CONTEXT_PROMPTS = {
    "email": """Korrigiere dieses Transkript für eine E-Mail:
- Formeller, professioneller Ton
- Vollständige, grammatikalisch korrekte Sätze
- Grußformeln und Anrede beibehalten
- Klar strukturierte Absätze

Gib NUR den korrigierten Text zurück.""",
    "chat": """Korrigiere dieses Transkript für eine Chat-Nachricht:
- Lockerer, natürlicher Ton
- Kurz und prägnant
- Emojis können beibehalten werden
- Keine übermäßige Formalisierung

Gib NUR den korrigierten Text zurück.""",
    "code": """Korrigiere dieses Transkript für technischen Kontext:
- Technische Fachbegriffe exakt beibehalten
- Code-Snippets, Variablennamen und Befehle nicht ändern
- Camel/Snake-Case erkennen und beibehalten
- Englische Begriffe nicht eindeutschen

Gib NUR den korrigierten Text zurück.""",
    "default": DEFAULT_REFINE_PROMPT,
}


def get_prompt_for_context(context: str, voice_commands: bool = True) -> str:
    """Gibt den Prompt für einen Kontext zurück, mit Fallback auf 'default'.

    Args:
        context: Kontext-Typ (email, chat, code, default)
        voice_commands: Voice-Commands Instruktionen einfügen (default: True)

    Returns:
        Der passende Prompt-Text. Bei unbekanntem Kontext → default.
    """
    prompt = CONTEXT_PROMPTS.get(context, CONTEXT_PROMPTS["default"])
    if voice_commands:
        # Prepend Voice-Commands (robuster als replace)
        prompt = VOICE_COMMANDS_INSTRUCTION + "\n" + prompt
    return prompt


# =============================================================================
# App-zu-Kontext Mapping
# =============================================================================

# Mapping von App-Namen zu Kontext-Typen für automatische Erkennung
DEFAULT_APP_CONTEXTS = {
    # Email-Clients
    "Mail": "email",
    "Outlook": "email",
    "Spark": "email",
    "Thunderbird": "email",
    # Chat/Messenger
    "Slack": "chat",
    "Discord": "chat",
    "Telegram": "chat",
    "WhatsApp": "chat",
    "Messages": "chat",
    "Signal": "chat",
    "Nachrichten": "chat",
    # Code-Editoren
    "Code": "code",
    "VS Code": "code",
    "Visual Studio Code": "code",
    "Cursor": "code",
    "Zed": "code",
    "PyCharm": "code",
    "IntelliJ IDEA": "code",
    "Xcode": "code",
    "Terminal": "code",
    "iTerm2": "code",
    "Ghostty": "code",
}
