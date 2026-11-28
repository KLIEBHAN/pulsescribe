// Thin Tauri v2 IPC client with a deterministic in-memory browser preview fallback
// so the UI works identically inside Tauri WebView and in headless/browser verification.

const hasTauri = () => Boolean(window.__TAURI__?.core?.invoke);

const previewListeners = new Map();

function emitPreview(event, payload) {
  const handlers = previewListeners.get(event);
  if (!handlers) return;
  for (const fn of handlers) fn({ payload });
}

function localIsoStamp(offsetMs = 0) {
  const d = new Date(Date.now() + offsetMs);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function createPreviewStore() {
  let autostartEnabled = false;
  let uiLanguagePref = "system";
  try {
    const saved = window.localStorage?.getItem("pulsescribe_ui_language");
    if (saved === "system" || saved === "en" || saved === "de") uiLanguagePref = saved;
  } catch { /* ignore */ }
  let uiLanguageResolved = uiLanguagePref === "de" ? "de" : "en";
  const settings = {
    mode: "deepgram",
    model: "nova-3",
    local_model: "large-v3-turbo",
    language: "de",
    refine: true,
    refine_provider: "openrouter",
    refine_model: "openai/gpt-4.1-nano",
    context: "",
    voice_commands: true,
    streaming: true,
    deepgram_warm_websocket: true,
    overlay_enabled: true,
    sound_enabled: true,
    auto_paste: true,
    show_rtf: true,
    input_device: "",
    toggle_hotkey: "f19",
    hold_hotkey: "fn",
    transcribing_timeout_secs: 45,
    openai_compatible_base_url: "",
    openai_compatible_model: "",
    openrouter_provider_order: "",
    openrouter_allow_fallbacks: true,
    api_keys_configured: {
      DEEPGRAM_API_KEY: true,
      OPENAI_API_KEY: false,
      GROQ_API_KEY: true,
      OPENROUTER_API_KEY: true,
      GEMINI_API_KEY: false,
      OPENAI_COMPATIBLE_API_KEY: false,
    },
    api_keys_masked: {
      DEEPGRAM_API_KEY: "dg_…7f2a",
      GROQ_API_KEY: "gsk…91c0",
      OPENROUTER_API_KEY: "sk-…4b1e",
    },
  };

  let themePref = { theme: "system", accent: "blue" };
  let daemonState = "idle";
  let onboardingStep = "done";
  let onboardingChoice = "fast";
  let lastTestTranscript = null;

  const models = [
    {
      id: "large-v3-turbo",
      display_name: "Whisper Large v3 Turbo (Q5)",
      engine: "whisper",
      approx_size_mb: 574,
      installed: true,
      source: "pulse_scribe_cache",
      installed_bytes: 574 * 1024 * 1024,
      path: "~/.pulsescribe/models/large-v3-turbo",
    },
    {
      id: "large-v3-turbo-german",
      display_name: "Whisper Large v3 Turbo German (Q5_0, primeline)",
      engine: "whisper",
      approx_size_mb: 574,
      installed: false,
      source: null,
      installed_bytes: null,
      path: null,
    },
    {
      id: "small",
      display_name: "Whisper Small",
      engine: "whisper",
      approx_size_mb: 488,
      installed: false,
      source: null,
      installed_bytes: null,
      path: null,
    },
    {
      id: "parakeet-ultra",
      display_name: "Parakeet Ultra 0.6B (int8, 25 EU-Sprachen, CPU)",
      engine: "sherpa_offline_transducer",
      approx_size_mb: 671,
      installed: true,
      source: "pulse_scribe_cache",
      installed_bytes: 671 * 1024 * 1024,
      path: "~/.pulsescribe/models/parakeet-ultra",
    },
    {
      id: "parakeet-v3",
      display_name: "NVIDIA Parakeet TDT v3 (25 EU Languages)",
      engine: "sherpa_offline_transducer",
      approx_size_mb: 670,
      installed: true,
      source: "hugging_face_hub_cache",
      installed_bytes: 670 * 1024 * 1024,
      path: "~/.cache/huggingface/hub/models--csukuangfj--sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
    },
    {
      id: "canary-1b-v2",
      display_name: "NVIDIA Canary 1B v2",
      engine: "sherpa_offline_canary",
      approx_size_mb: 1020,
      installed: false,
      source: null,
      installed_bytes: null,
      path: null,
    },
    {
      id: "cohere-transcribe",
      display_name: "Cohere Transcribe 2B (int8, 14 Sprachen, CPU)",
      engine: "sherpa_offline_cohere",
      approx_size_mb: 2888,
      installed: false,
      source: null,
      installed_bytes: null,
      path: null,
    },
    {
      id: "qwen3-asr-0.6b",
      display_name: "Qwen3-ASR 0.6B (int8, 30 Sprachen + Vokabular, CPU)",
      engine: "sherpa_offline_qwen3_asr",
      approx_size_mb: 987,
      installed: false,
      source: null,
      installed_bytes: null,
      path: null,
    },
    {
      id: "nemotron-streaming",
      display_name: "Nemotron Streaming Preview",
      engine: "sherpa_online_transducer",
      approx_size_mb: 310,
      installed: false,
      source: null,
      installed_bytes: null,
      path: null,
    },
  ];
  let loadedModelId = "large-v3-turbo";
  const activeDownloads = new Set();

  let history = [
    {
      timestamp: localIsoStamp(0),
      text: "Kurzer Test der nativen Rust-Desktop-Oberfläche mit Live-Overlay und Tauri.",
      mode: "deepgram",
      language: "de",
      app: "Cursor",
      refined: true,
    },
    {
      timestamp: localIsoStamp(-1800_000),
      text: "Bitte morgen um 10 Uhr das Architektur-Review für Phase 6 einplanen.",
      mode: "local",
      language: "de",
      app: "Mail",
      refined: true,
    },
    {
      timestamp: localIsoStamp(-86_400_000),
      text: "Check Whisper Metal inference latency on Apple Silicon.",
      mode: "local",
      language: "en",
      app: "Ghostty",
      refined: false,
    },
  ];

  let vocabulary = ["PulseScribe", "Deepgram", "whisper-rs", "sherpa-onnx", "Tauri"];

  const defaultPrompts = {
    default: "Korrigiere dieses Transkript:\n- Entferne Füllwörter\n- Korrigiere Grammatik und Rechtschreibung\n\nGib NUR den korrigierten Text zurück.",
    email: "Formeller, professioneller E-Mail-Ton. Gib NUR den korrigierten Text zurück.",
    chat: "Lockerer, prägnanter Chat-Ton. Gib NUR den korrigierten Text zurück.",
    code: "Technische Fachbegriffe und Bezeichner exakt beibehalten.",
    voice_commands: "Interpretiere gesprochene Befehle wie neuer Absatz, Punkt, Komma.",
    app_mappings: "# App → Context Mappings (one per line: AppName = context)\nCursor = code\nMail = email\nSlack = chat",
  };
  let prompts = { ...defaultPrompts };

  const promptContexts = [
    { key: "default", label: "Default", description: "Fallback instructions when no specific app context matches." },
    { key: "email", label: "Email", description: "Tune tone, structure, and sign-offs for email apps." },
    { key: "chat", label: "Chat", description: "Keep chat and messenger replies natural and concise." },
    { key: "code", label: "Code", description: "Preserve identifiers, CLI flags, and technical terms." },
    { key: "voice_commands", label: "Voice Commands", description: "Spoken editing instructions such as punctuation or new line." },
    { key: "app_mappings", label: "App Mappings", description: "Map app names to contexts (AppName = email|chat|code)." },
  ];

  const permissions = {
    microphone: "authorized",
    accessibility: true,
    input_monitoring: true,
  };

  function buildModelsPayload() {
    return {
      models: models.map((m) => ({ ...m })),
      active_downloads: [...activeDownloads],
      loaded_model_id: loadedModelId,
      capabilities: { whisper_compiled: true, sherpa_compiled: true },
      default_model_id: "large-v3-turbo",
    };
  }

  function buildPromptsPayload() {
    const hasCustom = Object.keys(defaultPrompts).some((k) => prompts[k] !== defaultPrompts[k]);
    return {
      contexts: promptContexts,
      drafts: { ...prompts },
      defaults: { ...defaultPrompts },
      has_custom_overrides: hasCustom,
    };
  }

  return async function mockInvoke(cmd, args = {}) {
    switch (cmd) {
      case "get_app_state":
        return {
          version: "2.2.0",
          platform: "macos",
          paste_shortcut: "⌘V",
          config_dir: "~/.pulsescribe",
          daemon_state: daemonState,
          status_label: daemonState === "recording" ? "Recording — speak now" : "Ready to dictate",
          status_hint: "Use your hotkey to start dictation.",
          status_detail: null,
          status_error: null,
          last_transcript: history[0]?.text ?? null,
          last_test_transcript: lastTestTranscript,
          last_test_error: null,
          onboarding_complete: onboardingStep === "done",
          onboarding_step: onboardingStep,
          onboarding_choice: onboardingChoice,
          show_welcome_on_startup: false,
          initial_route: "general",
          local_capabilities: { whisper_compiled: true, sherpa_compiled: true },
          toggle_hotkey: settings.toggle_hotkey,
          hold_hotkey: settings.hold_hotkey,
          mode: settings.mode,
          ui_language: uiLanguagePref,
          resolved_ui_language: uiLanguageResolved,
        };
      case "get_settings":
        return structuredClone(settings);
      case "save_settings": {
        const patch = args.patch || {};
        for (const [k, v] of Object.entries(patch)) {
          if (k === "api_keys") {
            for (const [ak, av] of Object.entries(v || {})) {
              const trimmed = (av || "").trim();
              settings.api_keys_configured[ak] = Boolean(trimmed);
              if (trimmed) {
                settings.api_keys_masked[ak] = trimmed.length > 8
                  ? `${trimmed.slice(0, 3)}…${trimmed.slice(-4)}`
                  : "••••";
              } else {
                delete settings.api_keys_masked[ak];
              }
            }
          } else if (v !== undefined) {
            settings[k] = v;
          }
        }
        return structuredClone(settings);
      }
      case "validate_hotkey": {
        const norm = (args.value || "").trim().toLowerCase();
        if (!norm) return { normalized: "", level: "error", message: "The hotkey is empty." };
        const current = ((args.kind === "hold" ? args.holdCurrent : args.toggleCurrent) || "").trim().toLowerCase();
        const other = ((args.kind === "hold" ? args.toggleCurrent : args.holdCurrent) || "").trim().toLowerCase();
        if (!args.replaceOther && other && norm === other) {
          return { normalized: norm, level: "error", message: `Conflicts with ${other}.` };
        }
        if (current && norm === current) return { normalized: norm, level: "ok", message: null };
        if (norm.includes("bogus")) return { normalized: norm, level: "error", message: "Unknown key: bogus" };
        if (norm === "cmd+space") return { normalized: norm, level: "error", message: "Shortcut is already reserved by macOS." };
        return { normalized: norm, level: "ok", message: null };
      }
      case "check_permissions":
        return { ...permissions };
      case "request_permission":
        if (args.kind === "microphone") permissions.microphone = "authorized";
        return null;
      case "open_privacy_settings":
        return null;
      case "list_audio_devices":
        return [
          { id: "default", name: "MacBook Pro Microphone", default_sample_rate: 48000, default_channels: 1, is_default: true },
          { id: "usb", name: "Shure MV7 USB", default_sample_rate: 48000, default_channels: 1, is_default: false },
        ];
      case "get_history":
        return history.slice(0, args.limit || 100);
      case "clear_history":
        history = [];
        return null;
      case "delete_history_entry": {
        const idx = history.findIndex((h) => h.timestamp === args.timestamp && h.text === args.text);
        if (idx >= 0) {
          history.splice(idx, 1);
          return true;
        }
        return false;
      }
      case "copy_text_to_clipboard":
        if (navigator.clipboard?.writeText) {
          try { await navigator.clipboard.writeText(args.text || ""); } catch { /* preview fallback */ }
        }
        return null;
      case "get_vocabulary":
        return { keywords: [...vocabulary], issues: [], local_limit: 50, deepgram_limit: 100 };
      case "save_vocabulary": {
        const parts = (args.rawText || "").split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);
        vocabulary = [...new Set(parts)];
        return { keywords: [...vocabulary], issues: [], local_limit: 50, deepgram_limit: 100 };
      }
      case "get_prompts":
        return buildPromptsPayload();
      case "save_prompts":
        prompts = { ...prompts, ...(args.drafts || {}) };
        return buildPromptsPayload();
      case "reset_prompts":
        prompts = { ...defaultPrompts };
        return buildPromptsPayload();
      case "list_local_models":
        return buildModelsPayload();
      case "download_local_model": {
        const target = models.find((m) => m.id === args.modelId);
        if (!target) throw new Error(`Unknown model '${args.modelId}'`);
        activeDownloads.add(target.id);
        const total = target.approx_size_mb * 1024 * 1024;
        for (const pct of [0.25, 0.65, 1.0]) {
          if (!activeDownloads.has(target.id)) {
            emitPreview("pulsescribe://model-download-finished", {
              model_id: target.id,
              ok: false,
              error: "local ASR operation cancelled",
            });
            throw new Error("local ASR operation cancelled");
          }
          emitPreview("pulsescribe://model-download-progress", {
            model_id: target.id,
            file: `${target.id}.bin`,
            file_index: 1,
            total_files: 1,
            bytes_downloaded: Math.round(total * pct),
            total_bytes: total,
          });
          await new Promise((r) => setTimeout(r, 90));
        }
        activeDownloads.delete(target.id);
        target.installed = true;
        target.source = "pulse_scribe_cache";
        target.installed_bytes = total;
        target.path = `~/.pulsescribe/models/${target.id}`;
        emitPreview("pulsescribe://model-download-finished", {
          model_id: target.id,
          ok: true,
          error: null,
        });
        return buildModelsPayload();
      }
      case "cancel_model_download":
        return activeDownloads.delete(args.modelId);
      case "delete_local_model": {
        const target = models.find((m) => m.id === args.modelId);
        if (target) {
          target.installed = false;
          target.source = null;
          target.installed_bytes = null;
          target.path = null;
          if (loadedModelId === target.id) loadedModelId = null;
        }
        return buildModelsPayload();
      }
      case "unload_local_model":
        loadedModelId = null;
        return null;
      case "toggle_dictation":
        daemonState = daemonState === "recording" ? "idle" : "recording";
        emitPreview("pulsescribe://daemon-event", {
          type: "state_changed",
          state: daemonState,
          detail: daemonState === "recording" ? "Preview recording" : null,
          error: null,
          generation: Date.now(),
        });
        return null;
      case "start_test_dictation":
        lastTestTranscript = "Hallo PulseScribe — Testdiktat erfolgreich empfangen.";
        emitPreview("pulsescribe://daemon-event", {
          type: "test_transcript",
          run_id: 1,
          text: lastTestTranscript,
          error: null,
        });
        return null;
      case "cancel_dictation":
        daemonState = "idle";
        return null;
      case "set_test_hotkey_mode":
        return null;
      case "copy_last_transcript":
        return history[0]?.text ?? null;
      case "set_onboarding_state":
        if (args.step) onboardingStep = args.step;
        if (args.choice !== undefined) onboardingChoice = args.choice || null;
        return null;
      case "get_theme_preference":
        return { ...themePref };
      case "save_theme_preference":
        themePref = { theme: args.theme || "system", accent: args.accent || "blue" };
        return { ...themePref };
      case "save_ui_language": {
        const next = args.language === "de" || args.language === "en" ? args.language : "system";
        uiLanguagePref = next;
        uiLanguageResolved = next === "system" ? (args.resolved === "de" ? "de" : "en") : next;
        try { window.localStorage?.setItem("pulsescribe_ui_language", uiLanguagePref); } catch { /* ignore */ }
        return { language: uiLanguagePref, resolved: uiLanguageResolved };
      }
      case "open_config_dir":
        return null;
      case "open_release_url":
        return null;
      case "get_autostart":
        return {
          supported: true,
          enabled: autostartEnabled,
          detail: "~/Library/LaunchAgents/com.kliebhan.pulsescribe.plist",
        };
      case "set_autostart":
        autostartEnabled = Boolean(args.enabled);
        return {
          supported: true,
          enabled: autostartEnabled,
          detail: "~/Library/LaunchAgents/com.kliebhan.pulsescribe.plist",
        };
      case "export_diagnostics":
        return {
          path: "~/.pulsescribe/diagnostics-preview.json",
          preview: JSON.stringify(
            {
              version: "2.2.0",
              os: "macos",
              mode: settings.mode,
              permissions,
              api_keys_configured: settings.api_keys_configured,
            },
            null,
            2,
          ),
        };
      case "get_update_state":
        return {
          current_version: "2.2.0",
          status: "idle",
          latest_version: null,
          release_url: null,
          release_notes: null,
          asset_name: null,
          percent: 0,
          automatic: false,
          can_install_in_app: true,
          manual: false,
          hint: null,
          error: null,
          error_context: null,
        };
      case "check_for_updates":
        return {
          current_version: "2.2.0",
          status: "idle",
          latest_version: "2.2.0",
          release_url: "https://github.com/KLIEBHAN/pulsescribe/releases",
          release_notes: null,
          asset_name: null,
          percent: 0,
          automatic: false,
          can_install_in_app: true,
          manual: true,
          hint: null,
          error: null,
          error_context: null,
        };
      case "download_update":
        return {
          current_version: "2.2.0",
          status: "ready",
          latest_version: "2.3.0",
          release_url: "https://github.com/KLIEBHAN/pulsescribe/releases/tag/v2.3.0",
          release_notes: "Automatic updates preview.",
          asset_name: "PulseScribe-2.3.0-macOS-arm64-Full.dmg",
          percent: 100,
          automatic: true,
          can_install_in_app: true,
          manual: true,
          hint: "automatic",
          error: null,
          error_context: null,
        };
      case "install_update":
        return {
          current_version: "2.2.0",
          status: "ready",
          latest_version: "2.3.0",
          release_url: "https://github.com/KLIEBHAN/pulsescribe/releases/tag/v2.3.0",
          release_notes: "Automatic updates preview.",
          asset_name: "PulseScribe-2.3.0-macOS-arm64-Full.dmg",
          percent: 100,
          automatic: true,
          can_install_in_app: true,
          manual: true,
          hint: "automatic",
          error: null,
          error_context: null,
        };
      default:
        throw new Error(`Unhandled preview command: ${cmd}`);
    }
  };
}

const mockInvoke = createPreviewStore();

export async function invoke(cmd, args) {
  if (hasTauri()) {
    return window.__TAURI__.core.invoke(cmd, args);
  }
  return mockInvoke(cmd, args);
}

export async function listen(event, handler) {
  if (window.__TAURI__?.event?.listen) {
    return window.__TAURI__.event.listen(event, handler);
  }
  if (!previewListeners.has(event)) previewListeners.set(event, new Set());
  previewListeners.get(event).add(handler);
  return () => previewListeners.get(event)?.delete(handler);
}

export function isTauriRuntime() {
  return hasTauri();
}
