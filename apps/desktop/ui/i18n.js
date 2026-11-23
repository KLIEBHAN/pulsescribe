// Lightweight zero-dependency i18n dictionary for PulseScribe Desktop (en / de).

const STRINGS = {
  en: {
    "nav.main": "Main navigation",
    "nav.primary": "Primary navigation",
    "nav.hideSidebar": "Hide sidebar",
    "nav.showSidebar": "Show sidebar",
    "route.general": "General",
    "route.models": "Local Models",
    "route.prompts": "Prompts",
    "route.vocabulary": "Vocabulary",
    "route.history": "History",
    "route.onboarding": "Setup Wizard",
    "route.diagnostics": "Diagnostics",
    "mobile.general": "General",
    "mobile.models": "Models",
    "mobile.prompts": "Prompts",
    "mobile.vocabulary": "Vocab",
    "mobile.history": "History",
    "mobile.onboarding": "Setup",
    "mobile.diagnostics": "Status",

    "theme.group": "Color mode",
    "theme.system": "System",
    "theme.systemAria": "System theme",
    "theme.light": "Light",
    "theme.lightAria": "Light theme",
    "theme.dark": "Dark",
    "theme.darkAria": "Dark theme",
    "accent.label": "Accent",
    "accent.group": "Accent color",
    "accent.blue": "Blue",
    "accent.mono": "Mono",

    "common.ready": "Ready",
    "common.confirm": "Confirm",
    "common.cancel": "Cancel",
    "common.dismissError": "Dismiss error",
    "common.discard": "Discard",
    "common.saveChanges": "Save changes",
    "common.unsavedChanges": "Unsaved changes",
    "common.unsavedWithKeys": "Unsaved settings and API key updates",
    "common.refresh": "Refresh",
    "common.open": "Open",
    "common.add": "Add",
    "common.copy": "Copy",
    "common.delete": "Delete",
    "common.clear": "Clear",
    "common.manage": "Manage",
    "common.active": "Active",
    "common.default": "Default",
    "common.custom": "Custom",
    "common.none": "None",
    "common.notSet": "Not set",
    "common.back": "Back",
    "common.continue": "Continue",

    "action.startDictation": "Start Dictation",
    "action.stopDictation": "Stop Dictation",
    "action.cancelDictation": "Cancel Dictation",
    "action.unloadRam": "Unload RAM",
    "action.resetDefaults": "Reset Defaults",
    "action.savePrompts": "Save Prompts",
    "action.saveVocabulary": "Save Vocabulary",
    "action.clearAll": "Clear All",
    "action.configFolder": "Config Folder",
    "action.exportDiagnostics": "Export Diagnostics",

    "provider.deepgram": "Deepgram (Live Streaming)",
    "provider.openai": "OpenAI Whisper API",
    "provider.groq": "Groq Whisper (Ultra Fast)",
    "provider.openai_compatible": "OpenAI-Compatible Endpoint",
    "provider.local": "Local Offline ASR",

    "lang.auto": "Auto-detect",
    "lang.de": "German (de)",
    "lang.en": "English (en)",
    "lang.fr": "French (fr)",
    "lang.es": "Spanish (es)",
    "lang.it": "Italian (it)",
    "lang.nl": "Dutch (nl)",
    "lang.pt": "Portuguese (pt)",
    "lang.pl": "Polish (pl)",

    "general.loading": "Loading settings…",
    "general.dictationTitle": "Dictation",
    "general.dictationDesc": "Speech recognition engine, model selection, and language.",
    "general.providerTitle": "Transcription Provider",
    "general.providerDesc": "Choose cloud streaming or fully offline local models.",
    "general.localModelTitle": "Local Model",
    "general.localModelDesc": "Runs locally via whisper-rs or sherpa-onnx.",
    "general.installedSuffix": " (Installed)",
    "general.endpointUrlTitle": "Endpoint Base URL",
    "general.endpointUrlDesc": "Custom OpenAI-compatible audio transcription server.",
    "general.customModelTitle": "Custom Model Name",
    "general.cloudModelTitle": "Cloud Model Override",
    "general.cloudModelDesc": "Leave blank for provider default (e.g. nova-3, whisper-large-v3-turbo).",
    "general.cloudModelPlaceholder": "default",
    "general.liveStreamingTitle": "Live WebSocket Streaming",
    "general.liveStreamingDesc": "Streams audio live and shows interim text in the floating overlay.",
    "general.warmSocketTitle": "Keep Warm Deepgram Connection",
    "general.warmSocketDesc": "Pre-connects WebSocket for near-instant start latency.",
    "general.dictationLangTitle": "Dictation Language",
    "general.dictationLangDesc": "Primary dictation language or automatic detection.",

    "general.hotkeysTitle": "Hotkeys & Audio",
    "general.hotkeysDesc": "Global push-to-talk / toggle shortcuts and microphone source.",
    "general.toggleHotkeyTitle": "Toggle Hotkey",
    "general.toggleHotkeyDesc": "Press once to start recording, press again to finish.",
    "general.holdHotkeyTitle": "Hold-to-Record Hotkey",
    "general.holdHotkeyDesc": "Hold to dictate and release to transcribe.",
    "general.micTitle": "Microphone",
    "general.micDesc": "Input device used for audio capture.",
    "general.systemDefault": "System Default",
    "general.defaultSuffix": " (Default)",
    "general.overlayTitle": "Floating Overlay",
    "general.overlayDesc": "Show the native 60 FPS status & live waveform pill.",
    "general.soundTitle": "Sound Feedback",
    "general.soundDesc": "Play subtle audio cues on start, stop, and completion.",
    "general.autoPasteTitle": "Auto-Paste Transcript",
    "general.autoPasteDesc": "Automatically paste into the focused app via {shortcut}.",
    "general.showRtfTitle": "Show Transcription Timing",
    "general.showRtfDesc": "Include elapsed transcription seconds in completion feedback.",
    "general.autostartTitle": "Launch at Login",
    "general.autostartDesc": "Start PulseScribe automatically in the background when you sign in.",
    "general.autostartEnabled": "Launch at Login enabled",
    "general.autostartDisabled": "Launch at Login disabled",

    "general.interfaceTitle": "Interface",
    "general.interfaceDesc": "Language for settings, setup wizard, and menu bar.",
    "general.uiLangTitle": "Interface Language",
    "general.uiLangDesc": "Applies immediately without restarting.",
    "general.uiLangSystem": "System ({lang})",
    "general.uiLangNameEn": "English",
    "general.uiLangNameDe": "Deutsch",
    "general.uiLangSaved": "Interface language updated",

    "general.refineSectionTitle": "Refinement",
    "general.refineSectionDesc": "Optional post-processing using custom prompts per active application.",
    "general.refineToggleTitle": "LLM Refinement",
    "general.refineToggleDesc": "Polish grammar, filler words, and formatting before pasting.",
    "general.refineProviderTitle": "Refinement Provider",
    "general.refineProviderDefault": "Default (Groq)",
    "general.refineModelTitle": "Refinement Model",
    "general.refineModelDesc": "Optional override (e.g. openai/gpt-4.1-nano or llama-3.3-70b-versatile).",
    "general.refineModelPlaceholder": "provider default",
    "general.voiceCommandsTitle": "Spoken Voice Commands",
    "general.voiceCommandsDesc": "Interpret spoken punctuation and paragraph commands.",

    "general.apiKeysTitle": "API Keys",
    "general.apiKeysDesc": "Stored locally in ~/.pulsescribe/.env with 0600 permissions. Existing secrets are masked.",
    "apiKey.DEEPGRAM_API_KEY": "Deepgram API Key",
    "apiKey.OPENAI_API_KEY": "OpenAI API Key",
    "apiKey.GROQ_API_KEY": "Groq API Key",
    "apiKey.OPENROUTER_API_KEY": "OpenRouter API Key",
    "apiKey.GEMINI_API_KEY": "Gemini API Key",
    "apiKey.OPENAI_COMPATIBLE_API_KEY": "OpenAI-Compatible API Key",
    "apiKey.pendingRemoval": "Pending removal",
    "apiKey.pendingUpdate": "Pending update",
    "apiKey.saved": "Saved ({masked})",
    "apiKey.notConfigured": "Not configured",
    "apiKey.placeholderNew": "Enter new key…",
    "apiKey.placeholderPaste": "Paste key…",
    "apiKey.markedForRemoval": "Marked {label} for removal — click Save changes",

    "settings.fixHotkey": "Fix the invalid hotkey before saving.",
    "settings.saved": "Settings saved and reloaded",
    "settings.savedPartial": "Settings saved — newer edits remain unsaved",

    "models.scanning": "Scanning local model cache…",
    "models.slimBuild": "Slim Build",
    "models.installed": "Installed",
    "models.loadedInRam": "Loaded in RAM",
    "models.unloadedToast": "Unloaded local model from RAM",
    "models.refreshedToast": "Model cache refreshed",
    "models.sourceHf": "Hugging Face Cache",
    "models.sourceLocal": "PulseScribe Cache",
    "models.livePreview": "Live Preview",
    "models.downloading": "Downloading {file} ({index}/{total}) — {pct}%",
    "models.startingDownload": "Starting download…",
    "models.connecting": "connecting…",
    "models.progressAria": "Download progress for {name}",
    "models.cancelling": "Cancelling {id}…",
    "models.useModel": "Use Model",
    "models.switchedToast": "Switched local model to {id}",
    "models.deleteAria": "Delete {name}",
    "models.deleteTitle": "Delete {name}?",
    "models.deleteMessage": "This removes the downloaded weights from ~/.pulsescribe/models/.",
    "models.deleteConfirm": "Delete model",
    "models.deletedToast": "Deleted {id}",
    "models.downloadedToast": "Downloaded {name}",
    "models.download": "Download",
    "models.catalogTitle": "Model Catalog",
    "models.catalogDesc": "Existing Hugging Face Hub snapshots are detected automatically without duplicating weights.",

    "prompts.loading": "Loading prompts…",
    "prompts.contextsAria": "Prompt contexts",
    "prompts.revertSection": "Revert section",
    "prompts.editorAria": "{label} prompt",
    "prompts.resetTitle": "Reset all prompts?",
    "prompts.resetMessage": "This removes custom overrides from prompts.toml and restores PulseScribe's default prompts.",
    "prompts.resetConfirm": "Reset defaults",
    "prompts.resetToast": "Prompts reset to defaults",
    "prompts.savedToast": "Prompts saved to prompts.toml",
    "prompts.savedPartialToast": "Prompts saved — newer edits remain unsaved",
    "prompts.ctx.default.label": "Default",
    "prompts.ctx.default.desc": "Fallback instructions used when PulseScribe cannot infer a more specific writing context.",
    "prompts.ctx.email.label": "Email",
    "prompts.ctx.email.desc": "Tune how rewritten dictation should sound in emails, including tone, structure, and sign-offs.",
    "prompts.ctx.chat.label": "Chat",
    "prompts.ctx.chat.desc": "Tune short back-and-forth messages for chat apps and messengers.",
    "prompts.ctx.code.label": "Code",
    "prompts.ctx.code.desc": "Tune formatting and cleanup when you dictate technical text, code, or commit messages.",
    "prompts.ctx.voice_commands.label": "Voice Commands",
    "prompts.ctx.voice_commands.desc": "Define spoken editing commands such as punctuation, formatting, or cursor actions.",
    "prompts.ctx.app_mappings.label": "App Mappings",
    "prompts.ctx.app_mappings.desc": "Map app names to prompt contexts so PulseScribe can choose the right rewrite style automatically.",

    "vocab.loading": "Loading vocabulary…",
    "vocab.count": "{count} terms (local ≤ {local}, Deepgram ≤ {deepgram})",
    "vocab.emptyTitle": "No custom vocabulary yet",
    "vocab.emptyDesc": "Add names, technical acronyms, or brand spellings to improve accuracy.",
    "vocab.removeAria": "Remove {term}",
    "vocab.inputAria": "Add custom vocabulary terms",
    "vocab.inputPlaceholder": "Add terms (comma-separated, e.g. PulseScribe, Deepgram, Kliebhan)…",
    "vocab.sectionTitle": "Custom Keywords",
    "vocab.sectionDesc": "Proper nouns, product names, and domain terms biased during transcription.",
    "vocab.savedToast": "Saved {count} vocabulary terms",
    "vocab.savedPartialToast": "Vocabulary saved — newer edits remain unsaved",

    "history.earlier": "Earlier",
    "history.today": "Today",
    "history.yesterday": "Yesterday",
    "history.clearTitle": "Clear transcript history?",
    "history.clearMessage": "This permanently deletes all {count} saved transcripts from history.jsonl.",
    "history.clearConfirm": "Clear history",
    "history.clearedToast": "History cleared",
    "history.emptyMatchTitle": "No matching transcripts",
    "history.emptyTitle": "No transcripts yet",
    "history.emptyMatchDesc": "Try a different search query or switch the filter back to All.",
    "history.emptyDesc": "Your recent dictations will appear here automatically after your first transcription.",
    "history.refinedBadge": "Refined",
    "history.copyAria": "Copy transcript",
    "history.copiedToast": "Copied transcript to clipboard",
    "history.deleteAria": "Delete transcript",
    "history.deletedToast": "Transcript deleted",
    "history.entryOne": "1 entry",
    "history.entryMany": "{count} entries",
    "history.searchPlaceholder": "Search transcripts…",
    "history.searchAria": "Search transcripts",
    "history.filterAria": "Filter history",
    "history.filterAll": "All",
    "history.filterRefined": "Refined",

    "onboarding.step.choose_goal": "1. Goal",
    "onboarding.step.permissions": "2. Permissions",
    "onboarding.step.hotkey": "3. Hotkeys",
    "onboarding.step.test_dictation": "4. Try It",
    "onboarding.step.cheat_sheet": "5. Ready",
    "onboarding.goalTitle": "How do you want to dictate?",
    "onboarding.goalDesc": "You can change provider, model, and API keys anytime in General.",
    "onboarding.choice.fast.title": "Fast Cloud",
    "onboarding.choice.fast.desc": "Deepgram live streaming with lowest latency and instant interim captions.",
    "onboarding.choice.private.title": "Private Offline",
    "onboarding.choice.private.desc": "100% local transcription with Whisper or Parakeet — audio never leaves your device.",
    "onboarding.choice.advanced.title": "Custom / Advanced",
    "onboarding.choice.advanced.desc": "OpenAI, Groq, or your own OpenAI-compatible server with LLM refinement.",
    "onboarding.perm.granted": "Granted",
    "onboarding.perm.required": "Required",
    "onboarding.perm.request": "Request",
    "onboarding.perm.openSettings": "Open Settings",
    "onboarding.perm.mic.title": "Microphone",
    "onboarding.perm.mic.desc": "Captures your voice while dictating.",
    "onboarding.perm.acc.title": "Accessibility",
    "onboarding.perm.acc.desc": "Allows pasting transcripts into the active window.",
    "onboarding.perm.input.title": "Input Monitoring",
    "onboarding.perm.input.desc": "Enables global hold-to-record hotkeys such as Fn.",
    "onboarding.hotkeyTitle": "Choose your dictation hotkeys",
    "onboarding.hotkeyInvalid": "Fix the invalid hotkey before continuing.",
    "onboarding.testTitle": "Try a test dictation",
    "onboarding.testDesc": "Press your hotkey or click below. Test transcripts stay inside this box and are never pasted.",
    "onboarding.testStop": "Stop & Transcribe",
    "onboarding.testCancel": "Cancel Test",
    "onboarding.testStart": "Start Test Dictation",
    "onboarding.readyTitle": "PulseScribe is ready",
    "onboarding.readyDesc": "Close this window anytime — PulseScribe keeps running silently in your menu bar / system tray with zero WebView RAM.",
    "onboarding.toggleLabel": "Toggle: ",
    "onboarding.holdLabel": " · Hold: ",
    "onboarding.finish": "Finish Setup",
    "onboarding.completeToast": "Setup complete",

    "diag.verdictError": "Daemon reported an error",
    "diag.verdictPerms": "Running with missing permissions",
    "diag.verdictOk": "PulseScribe is running normally",
    "diag.allReady": "All subsystems ready.",
    "diag.metricVersion": "Version",
    "diag.metricPlatform": "Platform",
    "diag.metricProvider": "Provider",
    "diag.metricModels": "Local Models",
    "diag.updatesSection": "Updates & Support",
    "diag.softwareUpdates": "Software Updates",
    "diag.updateAvailable": "Version v{latest} is available (installed: v{current}).",
    "diag.upToDate": "Up to date (v{current}).",
    "diag.checkPrompt": "Check GitHub Releases for newer builds.",
    "diag.downloadRelease": "Download Release",
    "diag.checkForUpdates": "Check for Updates",
    "diag.toastUpdateAvailable": "Update available",
    "diag.toastUpToDate": "Up to date",
    "diag.configDirTitle": "Configuration Directory",
    "diag.exportedBundle": "Exported Diagnostics Bundle",
    "diag.exportedToast": "Diagnostics exported",
  },

  de: {
    "nav.main": "Hauptnavigation",
    "nav.primary": "Primäre Navigation",
    "nav.hideSidebar": "Seitenleiste ausblenden",
    "nav.showSidebar": "Seitenleiste einblenden",
    "route.general": "Allgemein",
    "route.models": "Lokale Modelle",
    "route.prompts": "Prompts",
    "route.vocabulary": "Vokabular",
    "route.history": "Verlauf",
    "route.onboarding": "Einrichtungsassistent",
    "route.diagnostics": "Diagnose",
    "mobile.general": "Allgemein",
    "mobile.models": "Modelle",
    "mobile.prompts": "Prompts",
    "mobile.vocabulary": "Vokabular",
    "mobile.history": "Verlauf",
    "mobile.onboarding": "Setup",
    "mobile.diagnostics": "Status",

    "theme.group": "Farbmodus",
    "theme.system": "System",
    "theme.systemAria": "System-Farbmodus",
    "theme.light": "Hell",
    "theme.lightAria": "Helles Design",
    "theme.dark": "Dunkel",
    "theme.darkAria": "Dunkles Design",
    "accent.label": "Akzent",
    "accent.group": "Akzentfarbe",
    "accent.blue": "Blau",
    "accent.mono": "Monochrom",

    "common.ready": "Bereit",
    "common.confirm": "Bestätigen",
    "common.cancel": "Abbrechen",
    "common.dismissError": "Fehlermeldung schließen",
    "common.discard": "Verwerfen",
    "common.saveChanges": "Änderungen speichern",
    "common.unsavedChanges": "Ungespeicherte Änderungen",
    "common.unsavedWithKeys": "Ungespeicherte Einstellungen und API-Schlüssel",
    "common.refresh": "Aktualisieren",
    "common.open": "Öffnen",
    "common.add": "Hinzufügen",
    "common.copy": "Kopieren",
    "common.delete": "Löschen",
    "common.clear": "Entfernen",
    "common.manage": "Verwalten",
    "common.active": "Aktiv",
    "common.default": "Standard",
    "common.custom": "Angepasst",
    "common.none": "Keines",
    "common.notSet": "Nicht festgelegt",
    "common.back": "Zurück",
    "common.continue": "Weiter",

    "action.startDictation": "Diktat starten",
    "action.stopDictation": "Diktat stoppen",
    "action.cancelDictation": "Diktat abbrechen",
    "action.unloadRam": "RAM leeren",
    "action.resetDefaults": "Zurücksetzen",
    "action.savePrompts": "Prompts speichern",
    "action.saveVocabulary": "Vokabular speichern",
    "action.clearAll": "Alle löschen",
    "action.configFolder": "Konfigurationsordner",
    "action.exportDiagnostics": "Diagnose exportieren",

    "provider.deepgram": "Deepgram (Live-Streaming)",
    "provider.openai": "OpenAI Whisper API",
    "provider.groq": "Groq Whisper (Sehr schnell)",
    "provider.openai_compatible": "OpenAI-kompatibler Endpunkt",
    "provider.local": "Lokale Offline-Erkennung",

    "lang.auto": "Automatisch erkennen",
    "lang.de": "Deutsch (de)",
    "lang.en": "Englisch (en)",
    "lang.fr": "Französisch (fr)",
    "lang.es": "Spanisch (es)",
    "lang.it": "Italienisch (it)",
    "lang.nl": "Niederländisch (nl)",
    "lang.pt": "Portugiesisch (pt)",
    "lang.pl": "Polnisch (pl)",

    "general.loading": "Einstellungen werden geladen…",
    "general.dictationTitle": "Diktat",
    "general.dictationDesc": "Spracherkennungs-Engine, Modellauswahl und Diktiersprache.",
    "general.providerTitle": "Transkriptionsanbieter",
    "general.providerDesc": "Wähle Cloud-Streaming oder vollständig lokale Offline-Modelle.",
    "general.localModelTitle": "Lokales Modell",
    "general.localModelDesc": "Läuft lokal über whisper-rs oder sherpa-onnx.",
    "general.installedSuffix": " (Installiert)",
    "general.endpointUrlTitle": "Endpunkt-Basis-URL",
    "general.endpointUrlDesc": "Eigener OpenAI-kompatibler Transkriptionsserver.",
    "general.customModelTitle": "Eigener Modellname",
    "general.cloudModelTitle": "Cloud-Modell überschreiben",
    "general.cloudModelDesc": "Leer lassen für den Anbieter-Standard (z. B. nova-3, whisper-large-v3-turbo).",
    "general.cloudModelPlaceholder": "Standard",
    "general.liveStreamingTitle": "Live-WebSocket-Streaming",
    "general.liveStreamingDesc": "Überträgt Audio live und zeigt Zwischentext im Overlay.",
    "general.warmSocketTitle": "Deepgram-Verbindung warm halten",
    "general.warmSocketDesc": "Hält den WebSocket vorverbunden für minimalen Startverzug.",
    "general.dictationLangTitle": "Diktiersprache",
    "general.dictationLangDesc": "Hauptsprache für die Spracherkennung oder automatische Erkennung.",

    "general.hotkeysTitle": "Tastenkürzel & Audio",
    "general.hotkeysDesc": "Globale Push-to-Talk- und Umschalt-Kürzel sowie Mikrofon.",
    "general.toggleHotkeyTitle": "Umschalt-Tastenkürzel",
    "general.toggleHotkeyDesc": "Einmal drücken zum Starten, erneut drücken zum Beenden.",
    "general.holdHotkeyTitle": "Halten-Tastenkürzel",
    "general.holdHotkeyDesc": "Zum Diktieren gedrückt halten, zum Transkribieren loslassen.",
    "general.micTitle": "Mikrofon",
    "general.micDesc": "Eingabegerät für die Audioaufnahme.",
    "general.systemDefault": "Systemstandard",
    "general.defaultSuffix": " (Standard)",
    "general.overlayTitle": "Schwebendes Overlay",
    "general.overlayDesc": "Zeigt die native 60-FPS-Status- und Pegelanzeige.",
    "general.soundTitle": "Ton-Feedback",
    "general.soundDesc": "Spielt dezente Signaltöne bei Start, Stopp und Abschluss.",
    "general.autoPasteTitle": "Transkript automatisch einfügen",
    "general.autoPasteDesc": "Fügt den Text automatisch per {shortcut} in die aktive App ein.",
    "general.showRtfTitle": "Transkriptionsdauer anzeigen",
    "general.showRtfDesc": "Zeigt die benötigten Sekunden in der Abschlussmeldung an.",
    "general.autostartTitle": "Bei Anmeldung starten",
    "general.autostartDesc": "Startet PulseScribe beim Anmelden automatisch im Hintergrund.",
    "general.autostartEnabled": "Autostart aktiviert",
    "general.autostartDisabled": "Autostart deaktiviert",

    "general.interfaceTitle": "Oberfläche",
    "general.interfaceDesc": "Sprache für Einstellungen, Einrichtungsassistent und Menüleiste.",
    "general.uiLangTitle": "Oberflächensprache",
    "general.uiLangDesc": "Wird sofort ohne Neustart angewendet.",
    "general.uiLangSystem": "System ({lang})",
    "general.uiLangNameEn": "English",
    "general.uiLangNameDe": "Deutsch",
    "general.uiLangSaved": "Oberflächensprache aktualisiert",

    "general.refineSectionTitle": "Nachbearbeitung",
    "general.refineSectionDesc": "Optionale Textverbesserung mit eigenen Prompts je aktiver App.",
    "general.refineToggleTitle": "LLM-Nachbearbeitung",
    "general.refineToggleDesc": "Glättet Grammatik, Füllwörter und Formatierung vor dem Einfügen.",
    "general.refineProviderTitle": "Anbieter für Nachbearbeitung",
    "general.refineProviderDefault": "Standard (Groq)",
    "general.refineModelTitle": "Modell für Nachbearbeitung",
    "general.refineModelDesc": "Optionale Vorgabe (z. B. openai/gpt-4.1-nano oder llama-3.3-70b-versatile).",
    "general.refineModelPlaceholder": "Anbieter-Standard",
    "general.voiceCommandsTitle": "Gesprochene Sprachbefehle",
    "general.voiceCommandsDesc": "Interpretiert gesprochene Satzzeichen und Absatzbefehle.",

    "general.apiKeysTitle": "API-Schlüssel",
    "general.apiKeysDesc": "Lokal in ~/.pulsescribe/.env mit 0600-Rechten gespeichert. Vorhandene Schlüssel sind maskiert.",
    "apiKey.DEEPGRAM_API_KEY": "Deepgram-API-Schlüssel",
    "apiKey.OPENAI_API_KEY": "OpenAI-API-Schlüssel",
    "apiKey.GROQ_API_KEY": "Groq-API-Schlüssel",
    "apiKey.OPENROUTER_API_KEY": "OpenRouter-API-Schlüssel",
    "apiKey.GEMINI_API_KEY": "Gemini-API-Schlüssel",
    "apiKey.OPENAI_COMPATIBLE_API_KEY": "OpenAI-kompatibler API-Schlüssel",
    "apiKey.pendingRemoval": "Wird entfernt",
    "apiKey.pendingUpdate": "Wird aktualisiert",
    "apiKey.saved": "Gespeichert ({masked})",
    "apiKey.notConfigured": "Nicht konfiguriert",
    "apiKey.placeholderNew": "Neuen Schlüssel eingeben…",
    "apiKey.placeholderPaste": "Schlüssel einfügen…",
    "apiKey.markedForRemoval": "{label} zum Entfernen vorgemerkt — auf Änderungen speichern klicken",

    "settings.fixHotkey": "Bitte ungültiges Tastenkürzel vor dem Speichern korrigieren.",
    "settings.saved": "Einstellungen gespeichert und neu geladen",
    "settings.savedPartial": "Einstellungen gespeichert — neuere Änderungen sind noch ungespeichert",

    "models.scanning": "Lokaler Modell-Cache wird geprüft…",
    "models.slimBuild": "Slim-Build",
    "models.installed": "Installiert",
    "models.loadedInRam": "Im RAM geladen",
    "models.unloadedToast": "Lokales Modell aus dem RAM entladen",
    "models.refreshedToast": "Modell-Cache aktualisiert",
    "models.sourceHf": "Hugging-Face-Cache",
    "models.sourceLocal": "PulseScribe-Cache",
    "models.livePreview": "Live-Vorschau",
    "models.downloading": "Lade {file} ({index}/{total}) — {pct} %",
    "models.startingDownload": "Download wird gestartet…",
    "models.connecting": "Verbindung wird hergestellt…",
    "models.progressAria": "Download-Fortschritt für {name}",
    "models.cancelling": "Breche {id} ab…",
    "models.useModel": "Verwenden",
    "models.switchedToast": "Lokales Modell auf {id} umgestellt",
    "models.deleteAria": "{name} löschen",
    "models.deleteTitle": "{name} löschen?",
    "models.deleteMessage": "Entfernt die heruntergeladenen Modelldaten aus ~/.pulsescribe/models/.",
    "models.deleteConfirm": "Modell löschen",
    "models.deletedToast": "{id} gelöscht",
    "models.downloadedToast": "{name} heruntergeladen",
    "models.download": "Herunterladen",
    "models.catalogTitle": "Modellkatalog",
    "models.catalogDesc": "Vorhandene Hugging-Face-Hub-Snapshots werden automatisch erkannt, ohne Gewichte zu duplizieren.",

    "prompts.loading": "Prompts werden geladen…",
    "prompts.contextsAria": "Prompt-Kontexte",
    "prompts.revertSection": "Abschnitt zurücksetzen",
    "prompts.editorAria": "{label}-Prompt",
    "prompts.resetTitle": "Alle Prompts zurücksetzen?",
    "prompts.resetMessage": "Entfernt eigene Anpassungen aus prompts.toml und stellt die Standard-Prompts wieder her.",
    "prompts.resetConfirm": "Auf Standard zurücksetzen",
    "prompts.resetToast": "Prompts auf Standard zurückgesetzt",
    "prompts.savedToast": "Prompts in prompts.toml gespeichert",
    "prompts.savedPartialToast": "Prompts gespeichert — neuere Änderungen sind noch ungespeichert",
    "prompts.ctx.default.label": "Standard",
    "prompts.ctx.default.desc": "Allgemeine Anweisungen, wenn kein spezifischer App-Kontext erkannt wird.",
    "prompts.ctx.email.label": "E-Mail",
    "prompts.ctx.email.desc": "Steuert Ton, Struktur und Grußformeln für diktierte E-Mails.",
    "prompts.ctx.chat.label": "Chat",
    "prompts.ctx.chat.desc": "Optimiert kurze, lockere Nachrichten für Chat-Apps und Messenger.",
    "prompts.ctx.code.label": "Code",
    "prompts.ctx.code.desc": "Steuert Formatierung und Fachbegriffe für technischen Text, Code und Commit-Nachrichten.",
    "prompts.ctx.voice_commands.label": "Sprachbefehle",
    "prompts.ctx.voice_commands.desc": "Definiert gesprochene Bearbeitungsbefehle wie Satzzeichen und Absätze.",
    "prompts.ctx.app_mappings.label": "App-Zuordnungen",
    "prompts.ctx.app_mappings.desc": "Ordnet App-Namen einem Prompt-Kontext zu, damit PulseScribe den Stil automatisch wählt.",

    "vocab.loading": "Vokabular wird geladen…",
    "vocab.count": "{count} Begriffe (lokal ≤ {local}, Deepgram ≤ {deepgram})",
    "vocab.emptyTitle": "Noch kein eigenes Vokabular",
    "vocab.emptyDesc": "Füge Namen, Fachbegriffe oder Markenschreibweisen hinzu, um die Erkennung zu verbessern.",
    "vocab.removeAria": "{term} entfernen",
    "vocab.inputAria": "Eigene Vokabularbegriffe hinzufügen",
    "vocab.inputPlaceholder": "Begriffe eingeben (kommagetrennt, z. B. PulseScribe, Deepgram, Kliebhan)…",
    "vocab.sectionTitle": "Eigene Fachbegriffe",
    "vocab.sectionDesc": "Eigennamen, Produktnamen und Fachbegriffe zur gezielten Erkennung beim Diktat.",
    "vocab.savedToast": "{count} Vokabularbegriffe gespeichert",
    "vocab.savedPartialToast": "Vokabular gespeichert — neuere Änderungen sind noch ungespeichert",

    "history.earlier": "Früher",
    "history.today": "Heute",
    "history.yesterday": "Gestern",
    "history.clearTitle": "Transkriptverlauf löschen?",
    "history.clearMessage": "Löscht alle {count} gespeicherten Transkripte dauerhaft aus history.jsonl.",
    "history.clearConfirm": "Verlauf löschen",
    "history.clearedToast": "Verlauf gelöscht",
    "history.emptyMatchTitle": "Keine passenden Transkripte",
    "history.emptyTitle": "Noch keine Transkripte",
    "history.emptyMatchDesc": "Versuche einen anderen Suchbegriff oder stelle den Filter zurück auf Alle.",
    "history.emptyDesc": "Deine letzten Diktate erscheinen hier automatisch nach der ersten Transkription.",
    "history.refinedBadge": "Bearbeitet",
    "history.copyAria": "Transkript kopieren",
    "history.copiedToast": "Transkript in die Zwischenablage kopiert",
    "history.deleteAria": "Transkript löschen",
    "history.deletedToast": "Transkript gelöscht",
    "history.entryOne": "1 Eintrag",
    "history.entryMany": "{count} Einträge",
    "history.searchPlaceholder": "Transkripte durchsuchen…",
    "history.searchAria": "Transkripte durchsuchen",
    "history.filterAria": "Verlauf filtern",
    "history.filterAll": "Alle",
    "history.filterRefined": "Bearbeitet",

    "onboarding.step.choose_goal": "1. Ziel",
    "onboarding.step.permissions": "2. Berechtigungen",
    "onboarding.step.hotkey": "3. Tastenkürzel",
    "onboarding.step.test_dictation": "4. Testen",
    "onboarding.step.cheat_sheet": "5. Fertig",
    "onboarding.goalTitle": "Wie möchtest du diktieren?",
    "onboarding.goalDesc": "Anbieter, Modell und API-Schlüssel lassen sich jederzeit unter Allgemein ändern.",
    "onboarding.choice.fast.title": "Schnell (Cloud)",
    "onboarding.choice.fast.desc": "Deepgram Live-Streaming mit geringster Latenz und sofortiger Live-Vorschau.",
    "onboarding.choice.private.title": "Privat (Offline)",
    "onboarding.choice.private.desc": "100 % lokale Transkription mit Whisper oder Parakeet — Audio verlässt dein Gerät nicht.",
    "onboarding.choice.advanced.title": "Eigene Konfiguration",
    "onboarding.choice.advanced.desc": "OpenAI, Groq oder eigener OpenAI-kompatibler Server mit LLM-Nachbearbeitung.",
    "onboarding.perm.granted": "Erteilt",
    "onboarding.perm.required": "Erforderlich",
    "onboarding.perm.request": "Anfragen",
    "onboarding.perm.openSettings": "Einstellungen öffnen",
    "onboarding.perm.mic.title": "Mikrofon",
    "onboarding.perm.mic.desc": "Nimmt deine Stimme während des Diktats auf.",
    "onboarding.perm.acc.title": "Bedienungshilfen",
    "onboarding.perm.acc.desc": "Erlaubt das Einfügen von Transkripten in das aktive Fenster.",
    "onboarding.perm.input.title": "Eingabeüberwachung",
    "onboarding.perm.input.desc": "Ermöglicht globale Halten-Tastenkürzel wie Fn.",
    "onboarding.hotkeyTitle": "Wähle deine Diktat-Tastenkürzel",
    "onboarding.hotkeyInvalid": "Bitte ungültiges Tastenkürzel vor dem Fortfahren korrigieren.",
    "onboarding.testTitle": "Testdiktat ausprobieren",
    "onboarding.testDesc": "Drücke dein Tastenkürzel oder klicke unten. Test-Transkripte bleiben in diesem Feld und werden nicht eingefügt.",
    "onboarding.testStop": "Stoppen & transkribieren",
    "onboarding.testCancel": "Test abbrechen",
    "onboarding.testStart": "Testdiktat starten",
    "onboarding.readyTitle": "PulseScribe ist bereit",
    "onboarding.readyDesc": "Du kannst dieses Fenster jederzeit schließen — PulseScribe läuft lautlos in der Menüleiste bzw. im System-Tray ohne WebView-RAM weiter.",
    "onboarding.toggleLabel": "Umschalten: ",
    "onboarding.holdLabel": " · Halten: ",
    "onboarding.finish": "Einrichtung abschließen",
    "onboarding.completeToast": "Einrichtung abgeschlossen",

    "diag.verdictError": "Daemon meldet einen Fehler",
    "diag.verdictPerms": "Läuft mit fehlenden Berechtigungen",
    "diag.verdictOk": "PulseScribe läuft normal",
    "diag.allReady": "Alle Subsysteme bereit.",
    "diag.metricVersion": "Version",
    "diag.metricPlatform": "Plattform",
    "diag.metricProvider": "Anbieter",
    "diag.metricModels": "Lokale Modelle",
    "diag.updatesSection": "Updates & Support",
    "diag.softwareUpdates": "Software-Updates",
    "diag.updateAvailable": "Version v{latest} ist verfügbar (installiert: v{current}).",
    "diag.upToDate": "Auf dem neuesten Stand (v{current}).",
    "diag.checkPrompt": "GitHub Releases auf neuere Versionen prüfen.",
    "diag.downloadRelease": "Release herunterladen",
    "diag.checkForUpdates": "Nach Updates suchen",
    "diag.toastUpdateAvailable": "Update verfügbar",
    "diag.toastUpToDate": "Auf dem neuesten Stand",
    "diag.configDirTitle": "Konfigurationsordner",
    "diag.exportedBundle": "Exportiertes Diagnosepaket",
    "diag.exportedToast": "Diagnose exportiert",
  },
};

const DE_DAEMON_STATE_LABELS = {
  idle: "Bereit zum Diktieren",
  loading: "Transkription wird vorbereitet",
  listening: "Höre zu",
  recording: "Aufnahme läuft — jetzt sprechen",
  transcribing: "Transkribiere",
  refining: "Bearbeite Text",
  done: "Transkript eingefügt",
  no_speech: "Keine Sprache erkannt",
  error: "Etwas ist schiefgelaufen",
};

const DE_DAEMON_STATE_HINTS = {
  idle: "Tastenkürzel nutzen, um ein Diktat zu starten.",
  loading: "Erster Start oder Anbieterwechsel kann einen Moment dauern.",
  listening: "Jetzt sprechen oder Halten-Taste zum Abbrechen loslassen.",
  recording: "Taste loslassen oder Umschalt-Kürzel zum Beenden drücken.",
  transcribing: "PulseScribe wandelt deine Sprache in Text um.",
  refining: "PulseScribe optimiert Zeichensetzung und Formatierung.",
  done: "Bereit für das nächste Diktat.",
  no_speech: "Erneut versuchen und nach dem Signal kurz sprechen.",
  error: "Erneut versuchen oder Diagnose exportieren, falls der Fehler bestehen bleibt.",
};

const DE_ERROR_LABELS = {
  missing_api_key: "API-Schlüssel fehlt",
  busy: "PulseScribe ist beschäftigt",
  timeout: "Zeitüberschreitung bei Transkription",
  invalid_provider: "Ungültige Anbieter-Einstellung",
  input_monitoring: "Eingabeüberwachung erforderlich",
  accessibility_permission: "Bedienungshilfen erforderlich",
  microphone_permission: "Mikrofonzugriff erforderlich",
  microphone_unavailable: "Mikrofon nicht verfügbar",
  connection: "Transkriptionsdienst nicht erreichbar",
  missing_dependency: "Fehlende Abhängigkeit",
};

const DE_ERROR_HINTS = {
  missing_api_key: "Erforderlichen Schlüssel unter Allgemein eintragen und erneut versuchen.",
  busy: "Kurz warten, bis die laufende Aufgabe abgeschlossen ist.",
  timeout: "PulseScribe ist gleich wieder bereit. Bei Wiederholung Diagnose exportieren.",
  invalid_provider: "Unterstützten Anbieter in den Einstellungen auswählen.",
  input_monitoring: "Fehlende Berechtigung im Einrichtungsassistenten erteilen.",
  accessibility_permission: "Fehlende Berechtigung im Einrichtungsassistenten erteilen.",
  microphone_permission: "Mikrofonzugriff im Einrichtungsassistenten erlauben.",
  microphone_unavailable: "Mikrofonzugriff und gewähltes Eingabegerät prüfen.",
  connection: "Internetverbindung oder Anbieterstatus prüfen und erneut versuchen.",
  missing_dependency: "Fehlende Komponente installieren oder Anbieter in den Einstellungen wechseln.",
};

let currentPref = "system";
let currentLang = "en";

export function normalizeUiLanguagePref(raw) {
  const lower = String(raw || "").trim().toLowerCase();
  if (lower === "de" || lower.startsWith("de-") || lower.startsWith("de_") || lower === "deutsch" || lower === "german") {
    return "de";
  }
  if (lower === "en" || lower.startsWith("en-") || lower.startsWith("en_") || lower === "english") {
    return "en";
  }
  return "system";
}

export function detectSystemLanguage() {
  const candidates = Array.isArray(navigator.languages) && navigator.languages.length > 0
    ? navigator.languages
    : [navigator.language || "en"];
  for (const locale of candidates) {
    const lower = String(locale || "").trim().toLowerCase();
    if (!lower) continue;
    if (lower === "de" || lower.startsWith("de-") || lower.startsWith("de_")) return "de";
    if (lower === "en" || lower.startsWith("en-") || lower.startsWith("en_")) return "en";
  }
  return "en";
}

export function resolveUiLanguage(pref) {
  const normalized = normalizeUiLanguagePref(pref);
  if (normalized === "de" || normalized === "en") return normalized;
  return detectSystemLanguage();
}

export function getUiLanguagePref() {
  return currentPref;
}

export function getUiLanguage() {
  return currentLang;
}

export function setUiLanguagePref(pref) {
  currentPref = normalizeUiLanguagePref(pref);
  currentLang = resolveUiLanguage(currentPref);
  document.documentElement.lang = currentLang;
  return { pref: currentPref, resolved: currentLang };
}

export function t(key, vars = null) {
  const table = STRINGS[currentLang] || STRINGS.en;
  let template = Object.prototype.hasOwnProperty.call(table, key)
    ? table[key]
    : Object.prototype.hasOwnProperty.call(STRINGS.en, key)
    ? STRINGS.en[key]
    : key;
  if (!vars) return template;
  return template.replace(/\{([a-zA-Z0-9_]+)\}/g, (_, name) =>
    vars[name] !== undefined && vars[name] !== null ? String(vars[name]) : `{${name}}`,
  );
}

export function applyStaticTranslations() {
  for (const node of document.querySelectorAll("[data-i18n]")) {
    node.textContent = t(node.dataset.i18n);
  }
  for (const node of document.querySelectorAll("[data-i18n-aria]")) {
    node.setAttribute("aria-label", t(node.dataset.i18nAria));
  }
  for (const node of document.querySelectorAll("[data-i18n-title]")) {
    node.setAttribute("title", t(node.dataset.i18nTitle));
  }
}

const DE_DELIVERY_OUTCOMES = {
  copiedPrefix: "Copied to clipboard",
  testCompletePrefix: "Test complete",
  pasteFailedMacos: "Copied — press ⌘V",
  pasteFailedOther: "Copied — press Ctrl+V",
  copyFailed: "Copy failed — use Copy Last Transcript",
  pasteFailedHintDe: "Manuell einfügen oder „Letztes Transkript kopieren“ wählen.",
  copyFailedHintDe: "Im Menü „Letztes Transkript kopieren“ wählen, um es erneut zu versuchen.",
};

function localizeDeDeliveryOutcome(state, detail) {
  if (!detail) return null;
  if (state === "done") {
    if (detail.startsWith(DE_DELIVERY_OUTCOMES.copiedPrefix)) {
      return { label: "In Zwischenablage kopiert", hint: null };
    }
    if (detail.startsWith(DE_DELIVERY_OUTCOMES.testCompletePrefix)) {
      return { label: "Test abgeschlossen", hint: null };
    }
    return null;
  }
  if (state === "error") {
    if (detail === DE_DELIVERY_OUTCOMES.pasteFailedMacos) {
      return { label: "Kopiert — ⌘V drücken", hint: DE_DELIVERY_OUTCOMES.pasteFailedHintDe };
    }
    if (detail === DE_DELIVERY_OUTCOMES.pasteFailedOther) {
      return { label: "Kopiert — Strg+V drücken", hint: DE_DELIVERY_OUTCOMES.pasteFailedHintDe };
    }
    if (detail === DE_DELIVERY_OUTCOMES.copyFailed) {
      return {
        label: "Kopieren fehlgeschlagen — Letztes Transkript kopieren",
        hint: DE_DELIVERY_OUTCOMES.copyFailedHintDe,
      };
    }
  }
  return null;
}

export function localizeDaemonStatus(app) {
  if (!app) return { label: t("common.ready"), hint: t("diag.allReady") };
  if (currentLang !== "de") {
    return {
      label: app.status_label || t("common.ready"),
      hint: app.status_hint || t("diag.allReady"),
    };
  }
  const st = app.daemon_state || "idle";
  const errCode = app.status_error?.code;
  const detail = String(app.status_detail || app.status_error?.detail || "").trim();
  const delivery = localizeDeDeliveryOutcome(st, detail);

  let label = delivery?.label || (errCode && DE_ERROR_LABELS[errCode]) || null;
  if (!label && detail.startsWith("Downloading ")) {
    label = `Lade ${detail.slice("Downloading ".length)}`;
  } else if (!label && detail.startsWith("Loading ")) {
    label = `Lade ${detail.slice("Loading ".length)}`;
  }
  if (!label) {
    label = DE_DAEMON_STATE_LABELS[st] || app.status_label || t("common.ready");
  }

  const hint = delivery?.hint
    || (errCode && DE_ERROR_HINTS[errCode])
    || DE_DAEMON_STATE_HINTS[st]
    || app.status_hint
    || t("diag.allReady");

  return { label, hint };
}

export function localizePromptContext(ctx) {
  const labelKey = `prompts.ctx.${ctx.key}.label`;
  const descKey = `prompts.ctx.${ctx.key}.desc`;
  const table = STRINGS[currentLang] || STRINGS.en;
  return {
    label: Object.prototype.hasOwnProperty.call(table, labelKey) ? table[labelKey] : ctx.label,
    description: Object.prototype.hasOwnProperty.call(table, descKey) ? table[descKey] : ctx.description,
  };
}
