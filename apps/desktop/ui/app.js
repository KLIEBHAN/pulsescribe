import { invoke, listen } from "./api.js";

const ROUTE_TITLES = {
  general: "General",
  models: "Local Models",
  prompts: "Prompts",
  vocabulary: "Vocabulary",
  history: "History",
  onboarding: "Setup Wizard",
  diagnostics: "Diagnostics",
};

const PROVIDERS = [
  { id: "deepgram", label: "Deepgram (Live Streaming)" },
  { id: "openai", label: "OpenAI Whisper API" },
  { id: "groq", label: "Groq Whisper (Ultra Fast)" },
  { id: "openai_compatible", label: "OpenAI-Compatible Endpoint" },
  { id: "local", label: "Local Offline ASR" },
];

const LANGUAGES = [
  ["auto", "Auto-detect"],
  ["de", "German (de)"],
  ["en", "English (en)"],
  ["fr", "French (fr)"],
  ["es", "Spanish (es)"],
  ["it", "Italian (it)"],
  ["nl", "Dutch (nl)"],
  ["pt", "Portuguese (pt)"],
  ["pl", "Polish (pl)"],
];

const API_KEY_LABELS = {
  DEEPGRAM_API_KEY: "Deepgram API Key",
  OPENAI_API_KEY: "OpenAI API Key",
  GROQ_API_KEY: "Groq API Key",
  OPENROUTER_API_KEY: "OpenRouter API Key",
  GEMINI_API_KEY: "Gemini API Key",
  OPENAI_COMPATIBLE_API_KEY: "OpenAI-Compatible API Key",
};

const ONBOARDING_STEPS = [
  { id: "choose_goal", label: "1. Goal" },
  { id: "permissions", label: "2. Permissions" },
  { id: "hotkey", label: "3. Hotkeys" },
  { id: "test_dictation", label: "4. Try It" },
  { id: "cheat_sheet", label: "5. Ready" },
];

const STREAMING_PREVIEW_ENGINE = "sherpa_online_transducer";

const state = {
  route: "general",
  navSeq: 0,
  app: null,
  settings: null,
  draft: null,
  apiKeyDrafts: {},
  apiKeyClears: new Set(),
  hotkeyValidation: { toggle: null, hold: null },
  devices: [],
  autostart: null,
  autostartPending: null,
  permissions: null,
  models: null,
  downloadProgress: {},
  localDownloadsInFlight: new Set(),
  prompts: null,
  activePromptTab: "default",
  promptDrafts: {},
  dirtyPrompts: false,
  vocabulary: null,
  vocabKeywords: [],
  dirtyVocab: false,
  history: [],
  historyQuery: "",
  historyFilter: "all",
  diagnostics: null,
  updateResult: null,
  themePref: { theme: "system", accent: "blue" },
  onboardingStep: "choose_goal",
  onboardingChoice: "fast",
  onboardingHotkeys: { toggle: "", hold: "" },
  onboardingHotkeyValidation: { toggle: null, hold: null },
  testModeEnabled: false,
  dirtySettings: false,
  settingsRev: 0,
  promptsRev: 0,
  vocabRev: 0,
};

const dom = {
  shell: document.getElementById("shell"),
  pageTitle: document.getElementById("page-title"),
  daemonBadge: document.getElementById("daemon-badge"),
  topbarActions: document.getElementById("topbar-actions"),
  viewRoot: document.getElementById("view-root"),
  errorBanner: document.getElementById("error-banner"),
  errorBannerText: document.getElementById("error-banner-text"),
  stickySaveBar: document.getElementById("sticky-save-bar"),
  stickySaveLabel: document.getElementById("sticky-save-label"),
  navStatusDot: document.getElementById("nav-status-dot"),
  toastStack: document.getElementById("toast-stack"),
  confirmDialog: document.getElementById("confirm-dialog"),
  confirmTitle: document.getElementById("confirm-title"),
  confirmMessage: document.getElementById("confirm-message"),
  confirmCancel: document.getElementById("confirm-cancel"),
  confirmOk: document.getElementById("confirm-ok"),
  btnToggleSidebar: document.getElementById("btn-toggle-sidebar"),
  btnExpandSidebar: document.getElementById("btn-expand-sidebar"),
};

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, val] of Object.entries(attrs)) {
    if (val === undefined || val === null || val === false) continue;
    if (key === "className") node.className = val;
    else if (key === "text") node.textContent = val;
    else if (key === "dataset") Object.assign(node.dataset, val);
    else if (key === "value" && (tag === "input" || tag === "textarea" || tag === "select" || tag === "progress")) {
      node.value = val;
    } else if (key.startsWith("on") && typeof val === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), val);
    } else if (val === true) {
      node.setAttribute(key, "");
    } else {
      node.setAttribute(key, String(val));
    }
  }
  for (const child of Array.isArray(children) ? children : [children]) {
    if (child === null || child === undefined || child === false) continue;
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function icon(id, cls = "ic") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#${id}`);
  svg.appendChild(use);
  return svg;
}

function showError(message) {
  dom.errorBannerText.textContent = String(message?.message || message);
  dom.errorBanner.hidden = false;
}

function clearError() {
  dom.errorBanner.hidden = true;
  dom.errorBannerText.textContent = "";
}

function showToast(message, kind = "success") {
  const toast = el("div", { className: `toast toast-${kind}` }, [
    icon(kind === "error" ? "ic-alert" : "ic-check", "ic ic-sm"),
    el("span", { text: message }),
  ]);
  dom.toastStack.appendChild(toast);
  setTimeout(() => toast.remove(), 2800);
}

async function runSafe(fn) {
  try {
    return await fn();
  } catch (err) {
    showError(err);
    return undefined;
  }
}

function confirmAction({ title, message, confirmLabel = "Confirm", destructive = false }) {
  return new Promise((resolve) => {
    dom.confirmTitle.textContent = title;
    dom.confirmMessage.textContent = message;
    dom.confirmOk.textContent = confirmLabel;
    dom.confirmOk.className = destructive ? "btn btn-danger" : "btn btn-primary";

    const cleanup = (accepted) => {
      dom.confirmDialog.removeEventListener("close", onClose);
      dom.confirmDialog.removeEventListener("cancel", onDialogCancel);
      dom.confirmOk.removeEventListener("click", onOk);
      dom.confirmCancel.removeEventListener("click", onCancel);
      resolve(accepted);
    };
    const onOk = () => dom.confirmDialog.close("ok");
    const onCancel = () => dom.confirmDialog.close("cancel");
    const onDialogCancel = () => { dom.confirmDialog.returnValue = "cancel"; };
    const onClose = () => cleanup(dom.confirmDialog.returnValue === "ok");

    dom.confirmDialog.returnValue = "cancel";
    dom.confirmOk.addEventListener("click", onOk);
    dom.confirmCancel.addEventListener("click", onCancel);
    dom.confirmDialog.addEventListener("cancel", onDialogCancel);
    dom.confirmDialog.addEventListener("close", onClose);
    dom.confirmDialog.showModal();
    if (destructive) {
      dom.confirmDialog.focus();
    } else {
      dom.confirmOk.focus();
    }
  });
}

function applyThemePref(pref) {
  state.themePref = pref;
  document.documentElement.dataset.theme = pref.theme;
  document.documentElement.dataset.accent = pref.accent;
  for (const btn of document.querySelectorAll("[data-theme-choice]")) {
    const active = btn.dataset.themeChoice === pref.theme;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
  }
  for (const btn of document.querySelectorAll("[data-accent-choice]")) {
    const active = btn.dataset.accentChoice === pref.accent;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
  }
}

function updateDaemonBadge() {
  const app = state.app;
  if (!app) return;
  const st = app.daemon_state || "idle";
  dom.daemonBadge.textContent = app.status_label || "Ready";
  dom.daemonBadge.className = "badge " + (
    st === "error" ? "badge-error"
      : st === "recording" || st === "listening" ? "badge-success"
      : st === "transcribing" || st === "refining" || st === "loading" ? "badge-warning"
      : "badge-neutral"
  );

  const perms = state.permissions;
  const missingPerm = perms && (
    perms.microphone !== "authorized" || !perms.accessibility || !perms.input_monitoring
  );
  dom.navStatusDot.className = "nav-dot" + (
    st === "error" ? " error" : missingPerm ? " warn" : ""
  );
}

function updateDirtyBar() {
  const show = state.route === "general" && state.dirtySettings;
  dom.stickySaveBar.hidden = !show;
  const hasKeyChanges = Object.keys(state.apiKeyDrafts).length > 0 || state.apiKeyClears.size > 0;
  dom.stickySaveLabel.textContent = hasKeyChanges
    ? "Unsaved settings and API key updates"
    : "Unsaved changes";
}

function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return "0 MB";
  const mb = bytes / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${Math.round(mb)} MB`;
}

function localDateKey(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function formatDayHeading(isoTimestamp) {
  const datePart = (isoTimestamp || "").slice(0, 10);
  if (!datePart) return "Earlier";
  const now = new Date();
  const today = localDateKey(now);
  const yesterday = localDateKey(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1));
  if (datePart === today) return "Today";
  if (datePart === yesterday) return "Yesterday";
  return datePart;
}

function formatTimeOnly(isoTimestamp) {
  return (isoTimestamp || "").slice(11, 16) || "--:--";
}

async function syncTestHotkeyMode(route) {
  const shouldEnable = route === "onboarding" && state.onboardingStep === "test_dictation";
  if (shouldEnable !== state.testModeEnabled) {
    state.testModeEnabled = shouldEnable;
    try { await invoke("set_test_hotkey_mode", { enabled: shouldEnable }); } catch { /* ignore */ }
  }
}

async function navigate(route) {
  const seq = ++state.navSeq;
  state.route = ROUTE_TITLES[route] ? route : "general";
  dom.pageTitle.textContent = ROUTE_TITLES[state.route];
  for (const btn of document.querySelectorAll("[data-route]")) {
    const active = btn.dataset.route === state.route;
    btn.classList.toggle("active", active);
    if (active) btn.setAttribute("aria-current", "page");
    else btn.removeAttribute("aria-current");
  }
  clearError();
  await loadRouteData(state.route, seq);
  if (seq !== state.navSeq) return;
  await syncTestHotkeyMode(state.route);
  if (seq !== state.navSeq) return;
  render();
  updateDirtyBar();
}

async function loadRouteData(route, seq) {
  try {
    if (route === "general") {
      const [settings, devices, models, autostart] = await Promise.all([
        invoke("get_settings"),
        invoke("list_audio_devices").catch(() => []),
        state.models ? Promise.resolve(state.models) : invoke("list_local_models").catch(() => null),
        invoke("get_autostart").catch(() => null),
      ]);
      if (seq !== state.navSeq) return;
      state.settings = settings;
      if (!state.dirtySettings) state.draft = structuredClone(settings);
      state.devices = devices;
      state.autostart = autostart;
      if (models) {
        reconcileDownloadProgress(models);
        state.models = models;
      }
    } else if (route === "models") {
      const [models, settings] = await Promise.all([
        invoke("list_local_models"),
        invoke("get_settings"),
      ]);
      if (seq !== state.navSeq) return;
      reconcileDownloadProgress(models);
      state.models = models;
      state.settings = settings;
      if (!state.dirtySettings) state.draft = structuredClone(settings);
    } else if (route === "prompts") {
      const prompts = await invoke("get_prompts");
      if (seq !== state.navSeq) return;
      state.prompts = prompts;
      if (!state.dirtyPrompts) state.promptDrafts = { ...prompts.drafts };
    } else if (route === "vocabulary") {
      const vocab = await invoke("get_vocabulary");
      if (seq !== state.navSeq) return;
      state.vocabulary = vocab;
      if (!state.dirtyVocab) state.vocabKeywords = [...vocab.keywords];
    } else if (route === "history") {
      const history = await invoke("get_history", { limit: 200 });
      if (seq !== state.navSeq) return;
      state.history = history;
    } else if (route === "onboarding") {
      const [app, settings, perms] = await Promise.all([
        invoke("get_app_state"),
        invoke("get_settings"),
        invoke("check_permissions"),
      ]);
      if (seq !== state.navSeq) return;
      state.app = app;
      state.settings = settings;
      state.permissions = perms;
      state.onboardingHotkeys = {
        toggle: settings.toggle_hotkey || "",
        hold: settings.hold_hotkey || "",
      };
      state.onboardingHotkeyValidation = { toggle: null, hold: null };
      if (app?.onboarding_step && app.onboarding_step !== "done") {
        state.onboardingStep = app.onboarding_step;
      } else {
        state.onboardingStep = "choose_goal";
      }
      if (app?.onboarding_choice) {
        state.onboardingChoice = app.onboarding_choice;
      }
    } else if (route === "diagnostics") {
      const [perms, models] = await Promise.all([
        invoke("check_permissions"),
        invoke("list_local_models").catch(() => null),
      ]);
      if (seq !== state.navSeq) return;
      state.permissions = perms;
      if (models) {
        reconcileDownloadProgress(models);
        state.models = models;
      }
      updateDaemonBadge();
    }
  } catch (err) {
    if (seq === state.navSeq) showError(err);
  }
}

function makeSwitch(checked, label, onToggle) {
  const btn = el("button", {
    type: "button",
    className: "switch",
    role: "switch",
    "aria-checked": checked ? "true" : "false",
    "aria-label": label,
    onClick: () => {
      const next = btn.getAttribute("aria-checked") !== "true";
      btn.setAttribute("aria-checked", next ? "true" : "false");
      onToggle(next);
    },
  });
  return btn;
}

function renderTopbarActions() {
  dom.topbarActions.replaceChildren();
  const st = state.app?.daemon_state || "idle";
  const isRecording = st === "listening" || st === "recording";
  const isProcessing = st === "transcribing" || st === "refining";

  if (state.route === "general") {
    const label = isRecording
      ? "Stop Dictation"
      : isProcessing
      ? "Cancel Dictation"
      : "Start Dictation";
    dom.topbarActions.append(
      el("button", {
        type: "button",
        className: isRecording || isProcessing ? "btn btn-danger" : "btn btn-primary",
        onClick: () => runSafe(() => invoke(isProcessing ? "cancel_dictation" : "toggle_dictation")),
      }, [
        icon(isRecording || isProcessing ? "ic-square" : "ic-play", "ic ic-sm"),
        el("span", { text: label }),
      ]),
    );
  } else if (state.route === "models") {
    if (state.models?.loaded_model_id) {
      dom.topbarActions.append(
        el("button", {
          type: "button",
          className: "btn",
          onClick: () => runSafe(async () => {
            await invoke("unload_local_model");
            const models = await invoke("list_local_models");
            reconcileDownloadProgress(models);
            state.models = models;
            showToast("Unloaded local model from RAM");
            render();
          }),
        }, [el("span", { text: "Unload RAM" })]),
      );
    }
    dom.topbarActions.append(
      el("button", {
        type: "button",
        className: "btn",
        onClick: () => runSafe(async () => {
          const models = await invoke("list_local_models");
          reconcileDownloadProgress(models);
          state.models = models;
          showToast("Model cache refreshed");
          render();
        }),
      }, [icon("ic-refresh", "ic ic-sm"), el("span", { text: "Refresh" })]),
    );
  } else if (state.route === "prompts") {
    dom.topbarActions.append(
      el("button", {
        type: "button",
        className: "btn btn-danger-ghost",
        onClick: () => runSafe(async () => {
          const ok = await confirmAction({
            title: "Reset all prompts?",
            message: "This removes custom overrides from prompts.toml and restores PulseScribe's default prompts.",
            confirmLabel: "Reset defaults",
            destructive: true,
          });
          if (!ok) return;
          state.promptsRev += 1;
          state.prompts = await invoke("reset_prompts");
          state.promptDrafts = { ...state.prompts.drafts };
          state.dirtyPrompts = false;
          showToast("Prompts reset to defaults");
          render();
        }),
      }, [el("span", { text: "Reset Defaults" })]),
      el("button", {
        type: "button",
        className: "btn btn-primary",
        onClick: savePromptsAction,
      }, [icon("ic-check", "ic ic-sm"), el("span", { text: "Save Prompts" })]),
    );
  } else if (state.route === "vocabulary") {
    dom.topbarActions.append(
      el("button", {
        type: "button",
        className: "btn btn-primary",
        onClick: saveVocabularyAction,
      }, [icon("ic-check", "ic ic-sm"), el("span", { text: "Save Vocabulary" })]),
    );
  } else if (state.route === "history") {
    if (state.history.length > 0) {
      dom.topbarActions.append(
        el("button", {
          type: "button",
          className: "btn btn-danger-ghost",
          onClick: () => runSafe(async () => {
            const ok = await confirmAction({
              title: "Clear transcript history?",
              message: `This permanently deletes all ${state.history.length} saved transcripts from history.jsonl.`,
              confirmLabel: "Clear history",
              destructive: true,
            });
            if (!ok) return;
            await invoke("clear_history");
            state.history = [];
            showToast("History cleared");
            render();
          }),
        }, [icon("ic-trash", "ic ic-sm"), el("span", { text: "Clear All" })]),
      );
    }
  } else if (state.route === "diagnostics") {
    dom.topbarActions.append(
      el("button", {
        type: "button",
        className: "btn",
        onClick: () => runSafe(() => invoke("open_config_dir")),
      }, [icon("ic-folder", "ic ic-sm"), el("span", { text: "Config Folder" })]),
      el("button", {
        type: "button",
        className: "btn btn-primary",
        onClick: () => runSafe(async () => {
          state.diagnostics = await invoke("export_diagnostics");
          showToast("Diagnostics exported");
          render();
        }),
      }, [icon("ic-download", "ic ic-sm"), el("span", { text: "Export Diagnostics" })]),
    );
  }
}

function setDraftField(key, value) {
  if (!state.draft) return;
  state.draft[key] = value;
  state.settingsRev += 1;
  state.dirtySettings = true;
  updateDirtyBar();
}

const EMPTY_HOTKEY_OK = { normalized: "", level: "ok", message: null };

async function validateSingleHotkey(kind, value, otherProposed) {
  const trimmed = (value ?? "").trim();
  // Empty disables that slot (e.g. macOS defaults to toggle="" and hold="fn").
  if (!trimmed) return EMPTY_HOTKEY_OK;
  const savedToggle = state.settings?.toggle_hotkey ?? "";
  const savedHold = state.settings?.hold_hotkey ?? "";
  return invoke("validate_hotkey", {
    kind,
    value: trimmed,
    // Pass the persisted hotkey for the slot being validated so the backend only
    // skips Carbon probing for our own already-registered shortcut, and pass the
    // proposed draft for the opposite slot so pair conflicts are still caught.
    toggleCurrent: kind === "toggle" ? savedToggle : (otherProposed ?? ""),
    holdCurrent: kind === "hold" ? savedHold : (otherProposed ?? ""),
    replaceOther: false,
  });
}

async function validateHotkeyPair(toggleValue, holdValue) {
  const toggleStr = (toggleValue ?? "").trim();
  const holdStr = (holdValue ?? "").trim();
  const [toggleRes, holdRes] = await Promise.all([
    validateSingleHotkey("toggle", toggleStr, holdStr),
    validateSingleHotkey("hold", holdStr, toggleStr),
  ]);
  return { toggle: toggleRes, hold: holdRes };
}

async function saveSettingsAction() {
  if (!state.draft) return;
  clearError();

  // Capture a consistent snapshot upfront so edits made while validation or saving
  // are in flight are never validated against stale drafts or discarded on completion.
  const saveRev = state.settingsRev;
  const submittedDraft = structuredClone(state.draft);
  const submittedKeyDrafts = {};
  const submittedKeyClears = new Set();
  const apiKeysPatch = {};
  for (const [k, v] of Object.entries(state.apiKeyDrafts)) {
    const trimmed = (v || "").trim();
    if (trimmed) {
      apiKeysPatch[k] = trimmed;
      submittedKeyDrafts[k] = v;
    }
  }
  for (const k of state.apiKeyClears) {
    if (!Object.prototype.hasOwnProperty.call(apiKeysPatch, k)) {
      apiKeysPatch[k] = "";
      submittedKeyClears.add(k);
    }
  }

  const validation = await runSafe(() =>
    validateHotkeyPair(submittedDraft.toggle_hotkey, submittedDraft.hold_hotkey),
  );
  if (!validation) return;
  state.hotkeyValidation = validation;
  updateHotkeyNoteNode("toggle");
  updateHotkeyNoteNode("hold");
  const invalidHotkey = validation.toggle?.level === "error"
    ? validation.toggle
    : validation.hold?.level === "error"
    ? validation.hold
    : null;
  if (invalidHotkey) {
    showError(invalidHotkey.message || "Fix the invalid hotkey before saving.");
    return;
  }

  const patch = {
    mode: submittedDraft.mode,
    model: submittedDraft.model,
    local_model: submittedDraft.local_model,
    language: submittedDraft.language,
    refine: submittedDraft.refine,
    refine_provider: submittedDraft.refine_provider,
    refine_model: submittedDraft.refine_model,
    context: submittedDraft.context,
    voice_commands: submittedDraft.voice_commands,
    streaming: submittedDraft.streaming,
    deepgram_warm_websocket: submittedDraft.deepgram_warm_websocket,
    overlay_enabled: submittedDraft.overlay_enabled,
    sound_enabled: submittedDraft.sound_enabled,
    auto_paste: submittedDraft.auto_paste,
    show_rtf: submittedDraft.show_rtf,
    input_device: submittedDraft.input_device,
    toggle_hotkey: submittedDraft.toggle_hotkey,
    hold_hotkey: submittedDraft.hold_hotkey,
    transcribing_timeout_secs: Number(submittedDraft.transcribing_timeout_secs) || 45,
    openai_compatible_base_url: submittedDraft.openai_compatible_base_url,
    openai_compatible_model: submittedDraft.openai_compatible_model,
    openrouter_provider_order: submittedDraft.openrouter_provider_order,
    openrouter_allow_fallbacks: submittedDraft.openrouter_allow_fallbacks,
    api_keys: apiKeysPatch,
  };
  await runSafe(async () => {
    const saved = await invoke("save_settings", { patch });
    state.settings = saved;
    state.app = await invoke("get_app_state");
    updateDaemonBadge();
    if (state.settingsRev === saveRev) {
      state.draft = structuredClone(saved);
      state.apiKeyDrafts = {};
      state.apiKeyClears.clear();
      state.hotkeyValidation = { toggle: null, hold: null };
      state.dirtySettings = false;
      updateDirtyBar();
      showToast("Settings saved and reloaded");
      render();
    } else {
      for (const [k, v] of Object.entries(submittedKeyDrafts)) {
        if (state.apiKeyDrafts[k] === v) delete state.apiKeyDrafts[k];
      }
      for (const k of submittedKeyClears) {
        if (!Object.prototype.hasOwnProperty.call(state.apiKeyDrafts, k)) {
          state.apiKeyClears.delete(k);
        }
      }
      if (state.draft) {
        state.draft.api_keys_configured = saved.api_keys_configured;
        state.draft.api_keys_masked = saved.api_keys_masked;
      }
      state.dirtySettings = true;
      updateDirtyBar();
      showToast("Settings saved — newer edits remain unsaved");
    }
  });
}

function updateHotkeyNoteNode(slot) {
  const host = document.getElementById(`hotkey-note-${slot}`);
  if (!host) return;
  const v = state.hotkeyValidation[slot];
  if (!v || !v.message) {
    host.replaceChildren();
    return;
  }
  const cls = v.level === "error" ? "badge-error" : v.level === "warning" ? "badge-warning" : "badge-success";
  host.replaceChildren(el("span", { className: `badge ${cls}`, text: v.message }));
}

async function validateHotkeyField(kind, value) {
  await runSafe(async () => {
    const otherProposed = kind === "toggle"
      ? (state.draft?.hold_hotkey ?? state.settings?.hold_hotkey ?? "")
      : (state.draft?.toggle_hotkey ?? state.settings?.toggle_hotkey ?? "");
    const res = await validateSingleHotkey(kind, value, otherProposed);
    state.hotkeyValidation[kind] = res;
    // Update badge in place so focus on the next Tab target is never stolen.
    updateHotkeyNoteNode(kind);
  });
}

function renderGeneralView() {
  const d = state.draft;
  if (!d) return el("p", { className: "muted", text: "Loading settings…" });

  const stack = el("div", { className: "page-stack" });

  // 1. Dictation Provider
  const providerPanel = el("div", { className: "panel" }, [
    el("div", { className: "panel-row" }, [
      el("div", { className: "row-copy" }, [
        el("div", { className: "row-title", text: "Transcription Provider" }),
        el("div", { className: "row-sub", text: "Choose cloud streaming or fully offline local models." }),
      ]),
      el("div", { className: "row-ctrl" }, [
        el("select", {
          className: "select",
          id: "setting-mode",
          "aria-label": "Transcription Provider",
          onChange: (e) => { setDraftField("mode", e.target.value); render(); },
        }, PROVIDERS.map((p) => el("option", { value: p.id, text: p.label, selected: d.mode === p.id }))),
      ]),
    ]),
  ]);

  if (d.mode === "local") {
    const offlineModels = (state.models?.models || []).filter((m) => m.engine !== STREAMING_PREVIEW_ENGINE);
    providerPanel.append(
      el("div", { className: "panel-row" }, [
        el("div", { className: "row-copy" }, [
          el("div", { className: "row-title", text: "Local Model" }),
          el("div", { className: "row-sub", text: "Runs locally via whisper-rs or sherpa-onnx." }),
        ]),
        el("div", { className: "row-ctrl" }, [
          el("select", {
            className: "select",
            "aria-label": "Local Model",
            onChange: (e) => setDraftField("local_model", e.target.value),
          }, (offlineModels.length > 0 ? offlineModels : [{ id: d.local_model || "large-v3-turbo", display_name: d.local_model || "large-v3-turbo" }]).map((m) =>
            el("option", { value: m.id, text: `${m.display_name || m.id}${m.installed ? " (Installed)" : ""}`, selected: d.local_model === m.id }),
          )),
          el("button", { type: "button", className: "btn btn-sm", text: "Manage", onClick: () => navigate("models") }),
        ]),
      ]),
    );
  } else if (d.mode === "openai_compatible") {
    providerPanel.append(
      el("div", { className: "panel-row" }, [
        el("div", { className: "row-copy" }, [
          el("div", { className: "row-title", text: "Endpoint Base URL" }),
          el("div", { className: "row-sub", text: "Custom OpenAI-compatible audio transcription server." }),
        ]),
        el("div", { className: "row-ctrl" }, [
          el("input", {
            className: "input",
            type: "url",
            "aria-label": "Endpoint Base URL",
            value: d.openai_compatible_base_url,
            placeholder: "http://localhost:8000/v1",
            onInput: (e) => setDraftField("openai_compatible_base_url", e.target.value),
          }),
        ]),
      ]),
      el("div", { className: "panel-row" }, [
        el("div", { className: "row-copy" }, [
          el("div", { className: "row-title", text: "Custom Model Name" }),
        ]),
        el("div", { className: "row-ctrl" }, [
          el("input", {
            className: "input",
            type: "text",
            "aria-label": "Custom Model Name",
            value: d.openai_compatible_model,
            placeholder: "whisper-1",
            onInput: (e) => setDraftField("openai_compatible_model", e.target.value),
          }),
        ]),
      ]),
    );
  } else {
    providerPanel.append(
      el("div", { className: "panel-row" }, [
        el("div", { className: "row-copy" }, [
          el("div", { className: "row-title", text: "Cloud Model Override" }),
          el("div", { className: "row-sub", text: "Leave blank for provider default (e.g. nova-3, whisper-large-v3-turbo)." }),
        ]),
        el("div", { className: "row-ctrl" }, [
          el("input", {
            className: "input",
            type: "text",
            "aria-label": "Cloud Model Override",
            value: d.model,
            placeholder: d.mode === "deepgram" ? "nova-3" : "default",
            onInput: (e) => setDraftField("model", e.target.value),
          }),
        ]),
      ]),
    );
  }

  if (d.mode === "deepgram") {
    providerPanel.append(
      el("div", { className: "panel-row" }, [
        el("div", { className: "row-copy" }, [
          el("div", { className: "row-title", text: "Live WebSocket Streaming" }),
          el("div", { className: "row-sub", text: "Streams audio live and shows interim text in the floating overlay." }),
        ]),
        el("div", { className: "row-ctrl" }, [
          makeSwitch(d.streaming, "Live WebSocket Streaming", (v) => setDraftField("streaming", v)),
        ]),
      ]),
      el("div", { className: "panel-row" }, [
        el("div", { className: "row-copy" }, [
          el("div", { className: "row-title", text: "Keep Warm Deepgram Connection" }),
          el("div", { className: "row-sub", text: "Pre-connects WebSocket for near-instant start latency." }),
        ]),
        el("div", { className: "row-ctrl" }, [
          makeSwitch(d.deepgram_warm_websocket, "Keep Warm Deepgram Connection", (v) => setDraftField("deepgram_warm_websocket", v)),
        ]),
      ]),
    );
  }

  providerPanel.append(
    el("div", { className: "panel-row" }, [
      el("div", { className: "row-copy" }, [
        el("div", { className: "row-title", text: "Language" }),
        el("div", { className: "row-sub", text: "Primary dictation language or automatic detection." }),
      ]),
      el("div", { className: "row-ctrl" }, [
        el("select", {
          className: "select",
          "aria-label": "Language",
          onChange: (e) => setDraftField("language", e.target.value),
        }, LANGUAGES.map(([code, label]) => el("option", { value: code, text: label, selected: d.language === code }))),
      ]),
    ]),
  );

  stack.append(
    el("section", { className: "section" }, [
      el("h2", { className: "section-title", text: "Dictation" }),
      el("p", { className: "section-desc", text: "Speech recognition engine, model selection, and language." }),
      providerPanel,
    ]),
  );

  // 2. Hotkeys & Audio
  const makeHotkeyNoteHost = (slot) => {
    const v = state.hotkeyValidation[slot];
    const badge = v && v.message
      ? el("span", {
          className: `badge ${v.level === "error" ? "badge-error" : v.level === "warning" ? "badge-warning" : "badge-success"}`,
          text: v.message,
        })
      : null;
    return el("div", { className: "row-note", id: `hotkey-note-${slot}` }, badge ? [badge] : []);
  };

  const deviceOptions = [
    el("option", { value: "", text: "System Default", selected: !d.input_device }),
    ...state.devices.map((dev) => el("option", {
      value: dev.name,
      text: `${dev.name}${dev.is_default ? " (Default)" : ""}`,
      selected: d.input_device === dev.name,
    })),
  ];

  stack.append(
    el("section", { className: "section" }, [
      el("h2", { className: "section-title", text: "Hotkeys & Audio" }),
      el("p", { className: "section-desc", text: "Global push-to-talk / toggle shortcuts and microphone source." }),
      el("div", { className: "panel" }, [
        el("div", { className: "panel-row" }, [
          el("div", { className: "row-copy" }, [
            el("div", { className: "row-title", text: "Toggle Hotkey" }),
            el("div", { className: "row-sub", text: "Press once to start recording, press again to finish." }),
            makeHotkeyNoteHost("toggle"),
          ]),
          el("div", { className: "row-ctrl" }, [
            el("input", {
              className: "input input-sm mono",
              id: "input-toggle-hotkey",
              type: "text",
              "aria-label": "Toggle Hotkey",
              value: d.toggle_hotkey,
              placeholder: state.app?.platform === "windows" ? "ctrl+alt+r" : "f19",
              onInput: (e) => setDraftField("toggle_hotkey", e.target.value),
              onBlur: (e) => validateHotkeyField("toggle", e.target.value),
            }),
          ]),
        ]),
        el("div", { className: "panel-row" }, [
          el("div", { className: "row-copy" }, [
            el("div", { className: "row-title", text: "Hold-to-Record Hotkey" }),
            el("div", { className: "row-sub", text: "Hold to dictate and release to transcribe." }),
            makeHotkeyNoteHost("hold"),
          ]),
          el("div", { className: "row-ctrl" }, [
            el("input", {
              className: "input input-sm mono",
              id: "input-hold-hotkey",
              type: "text",
              "aria-label": "Hold-to-Record Hotkey",
              value: d.hold_hotkey,
              placeholder: state.app?.platform === "windows" ? "ctrl+win" : "fn",
              onInput: (e) => setDraftField("hold_hotkey", e.target.value),
              onBlur: (e) => validateHotkeyField("hold", e.target.value),
            }),
          ]),
        ]),
        el("div", { className: "panel-row" }, [
          el("div", { className: "row-copy" }, [
            el("div", { className: "row-title", text: "Microphone" }),
            el("div", { className: "row-sub", text: "Input device used for audio capture." }),
          ]),
          el("div", { className: "row-ctrl" }, [
            el("select", {
              className: "select",
              "aria-label": "Microphone",
              onChange: (e) => setDraftField("input_device", e.target.value),
            }, deviceOptions),
          ]),
        ]),
        el("div", { className: "panel-row" }, [
          el("div", { className: "row-copy" }, [
            el("div", { className: "row-title", text: "Floating Overlay" }),
            el("div", { className: "row-sub", text: "Show the native 60 FPS status & live waveform pill." }),
          ]),
          el("div", { className: "row-ctrl" }, [
            makeSwitch(d.overlay_enabled, "Floating Overlay", (v) => setDraftField("overlay_enabled", v)),
          ]),
        ]),
        el("div", { className: "panel-row" }, [
          el("div", { className: "row-copy" }, [
            el("div", { className: "row-title", text: "Sound Feedback" }),
            el("div", { className: "row-sub", text: "Play subtle audio cues on start, stop, and completion." }),
          ]),
          el("div", { className: "row-ctrl" }, [
            makeSwitch(d.sound_enabled, "Sound Feedback", (v) => setDraftField("sound_enabled", v)),
          ]),
        ]),
        el("div", { className: "panel-row" }, [
          el("div", { className: "row-copy" }, [
            el("div", { className: "row-title", text: "Auto-Paste Transcript" }),
            el("div", { className: "row-sub", text: `Automatically paste into the focused app via ${state.app?.paste_shortcut || "⌘V"}.` }),
          ]),
          el("div", { className: "row-ctrl" }, [
            makeSwitch(d.auto_paste, "Auto-Paste Transcript", (v) => setDraftField("auto_paste", v)),
          ]),
        ]),
        el("div", { className: "panel-row" }, [
          el("div", { className: "row-copy" }, [
            el("div", { className: "row-title", text: "Show Transcription Timing" }),
            el("div", { className: "row-sub", text: "Include elapsed transcription seconds in completion feedback." }),
          ]),
          el("div", { className: "row-ctrl" }, [
            makeSwitch(d.show_rtf, "Show Transcription Timing", (v) => setDraftField("show_rtf", v)),
          ]),
        ]),
        state.autostart?.supported
          ? el("div", { className: "panel-row" }, [
              el("div", { className: "row-copy" }, [
                el("div", { className: "row-title", text: "Launch at Login" }),
                el("div", {
                  className: "row-sub",
                  text: "Start PulseScribe automatically in the background when you sign in.",
                }),
              ]),
              el("div", { className: "row-ctrl" }, [
                (() => {
                  const isPending = state.autostartPending !== null;
                  const checked = isPending ? state.autostartPending : Boolean(state.autostart.enabled);
                  const sw = makeSwitch(checked, "Launch at Login", async (next) => {
                    if (state.autostartPending !== null) return;
                    state.autostartPending = next;
                    sw.disabled = true;
                    clearError();
                    try {
                      state.autostart = await invoke("set_autostart", { enabled: next });
                      showToast(state.autostart.enabled ? "Launch at Login enabled" : "Launch at Login disabled");
                    } catch (err) {
                      showError(err);
                      state.autostart = await invoke("get_autostart").catch(() => state.autostart);
                    } finally {
                      state.autostartPending = null;
                      const currentSw = document.getElementById("autostart-switch");
                      if (currentSw) {
                        currentSw.setAttribute("aria-checked", state.autostart?.enabled ? "true" : "false");
                        currentSw.disabled = false;
                      }
                    }
                  });
                  sw.id = "autostart-switch";
                  sw.disabled = isPending;
                  return sw;
                })(),
              ]),
            ])
          : null,
      ]),
    ]),
  );

  // 3. LLM Refinement (toggles dependent block in place without destroying switch focus)
  const refineDetails = el("div", { id: "refine-details", hidden: !d.refine }, [
    el("div", { className: "panel-row" }, [
      el("div", { className: "row-copy" }, [
        el("div", { className: "row-title", text: "Refinement Provider" }),
      ]),
      el("div", { className: "row-ctrl" }, [
        el("select", {
          className: "select",
          "aria-label": "Refinement Provider",
          onChange: (e) => setDraftField("refine_provider", e.target.value),
        }, [
          ["", "Default (Groq)"],
          ["groq", "Groq"],
          ["openrouter", "OpenRouter"],
          ["openai", "OpenAI"],
          ["gemini", "Google Gemini"],
        ].map(([v, l]) => el("option", { value: v, text: l, selected: d.refine_provider === v }))),
      ]),
    ]),
    el("div", { className: "panel-row" }, [
      el("div", { className: "row-copy" }, [
        el("div", { className: "row-title", text: "Refinement Model" }),
        el("div", { className: "row-sub", text: "Optional override (e.g. openai/gpt-4.1-nano or llama-3.3-70b-versatile)." }),
      ]),
      el("div", { className: "row-ctrl" }, [
        el("input", {
          className: "input",
          type: "text",
          "aria-label": "Refinement Model",
          value: d.refine_model,
          placeholder: "provider default",
          onInput: (e) => setDraftField("refine_model", e.target.value),
        }),
      ]),
    ]),
    el("div", { className: "panel-row" }, [
      el("div", { className: "row-copy" }, [
        el("div", { className: "row-title", text: "Spoken Voice Commands" }),
        el("div", { className: "row-sub", text: "Interpret spoken punctuation and paragraph commands." }),
      ]),
      el("div", { className: "row-ctrl" }, [
        makeSwitch(d.voice_commands, "Spoken Voice Commands", (v) => setDraftField("voice_commands", v)),
      ]),
    ]),
  ]);

  const refinePanel = el("div", { className: "panel" }, [
    el("div", { className: "panel-row" }, [
      el("div", { className: "row-copy" }, [
        el("div", { className: "row-title", text: "LLM Refinement" }),
        el("div", { className: "row-sub", text: "Polish grammar, filler words, and formatting before pasting." }),
      ]),
      el("div", { className: "row-ctrl" }, [
        makeSwitch(d.refine, "LLM Refinement", (v) => {
          setDraftField("refine", v);
          refineDetails.hidden = !v;
        }),
      ]),
    ]),
    refineDetails,
  ]);

  stack.append(
    el("section", { className: "section" }, [
      el("h2", { className: "section-title", text: "Refinement" }),
      el("p", { className: "section-desc", text: "Optional post-processing using custom prompts per active application." }),
      refinePanel,
    ]),
  );

  // 4. API Keys (typing & clearing back to empty never deletes an existing key — only explicit Clear does!)
  const keyRows = Object.entries(API_KEY_LABELS).map(([envKey, label]) => {
    const configured = Boolean(d.api_keys_configured?.[envKey]);
    const masked = d.api_keys_masked?.[envKey];
    const markedForClear = state.apiKeyClears.has(envKey);
    const badgeNode = el("span", {
      className: `badge ${markedForClear ? "badge-warning" : configured ? "badge-success" : "badge-neutral"}`,
      text: markedForClear
        ? "Pending removal"
        : configured
        ? `Saved (${masked || "••••"})`
        : "Not configured",
    });

    const inputNode = el("input", {
      className: "input input-sm mono",
      type: "password",
      autocomplete: "off",
      "aria-label": label,
      value: state.apiKeyDrafts[envKey] || "",
      placeholder: configured ? "Enter new key…" : "Paste key…",
      onInput: (e) => {
        const val = e.target.value;
        state.settingsRev += 1;
        if (val.trim()) {
          state.apiKeyDrafts[envKey] = val;
          state.apiKeyClears.delete(envKey);
          badgeNode.className = "badge badge-warning";
          badgeNode.textContent = "Pending update";
          state.dirtySettings = true;
        } else {
          delete state.apiKeyDrafts[envKey];
          badgeNode.className = `badge ${state.apiKeyClears.has(envKey) ? "badge-warning" : configured ? "badge-success" : "badge-neutral"}`;
          badgeNode.textContent = state.apiKeyClears.has(envKey)
            ? "Pending removal"
            : configured
            ? `Saved (${masked || "••••"})`
            : "Not configured";
        }
        updateDirtyBar();
      },
    });

    return el("div", { className: "panel-row" }, [
      el("div", { className: "row-copy" }, [
        el("div", { className: "row-title", text: label }),
        el("div", { className: "row-sub" }, [badgeNode]),
      ]),
      el("div", { className: "row-ctrl" }, [
        inputNode,
        configured ? el("button", {
          type: "button",
          className: "btn btn-sm btn-danger-ghost",
          text: "Clear",
          onClick: () => {
            state.settingsRev += 1;
            delete state.apiKeyDrafts[envKey];
            state.apiKeyClears.add(envKey);
            inputNode.value = "";
            badgeNode.className = "badge badge-warning";
            badgeNode.textContent = "Pending removal";
            state.dirtySettings = true;
            updateDirtyBar();
            showToast(`Marked ${label} for removal — click Save changes`);
          },
        }) : null,
      ]),
    ]);
  });

  stack.append(
    el("section", { className: "section" }, [
      el("h2", { className: "section-title", text: "API Keys" }),
      el("p", { className: "section-desc", text: "Stored locally in ~/.pulsescribe/.env with 0600 permissions. Existing secrets are masked." }),
      el("div", { className: "panel" }, keyRows),
    ]),
  );

  return stack;
}

function reconcileDownloadProgress(modelsPayload) {
  const active = new Set(modelsPayload?.active_downloads || []);
  for (const id of Object.keys(state.downloadProgress)) {
    if (!active.has(id) && !state.localDownloadsInFlight.has(id)) {
      delete state.downloadProgress[id];
    }
  }
}

function updateModelProgressInPlace(progress) {
  const metaNode = document.getElementById(`dl-meta-${progress.model_id}`);
  const barNode = document.getElementById(`dl-bar-${progress.model_id}`);
  if (!metaNode || !barNode) return false;
  const pct = progress.total_bytes
    ? Math.min(100, Math.round((progress.bytes_downloaded / progress.total_bytes) * 100))
    : 0;
  metaNode.textContent = `Downloading ${progress.file} (${progress.file_index}/${progress.total_files}) — ${pct}%`;
  barNode.value = pct;
  return true;
}

function renderModelsView() {
  const data = state.models;
  if (!data) return el("p", { className: "muted", text: "Scanning local model cache…" });

  const stack = el("div", { className: "page-stack" });
  const caps = data.capabilities || {};

  stack.append(
    el("section", { className: "section" }, [
      el("div", { className: "panel" }, [
        el("div", { className: "metrics-grid" }, [
          el("div", { className: "metric-cell" }, [
            el("span", { className: "metric-label", text: "whisper-rs" }),
            el("span", { className: "metric-value", text: caps.whisper_compiled ? "Ready" : "Slim Build" }),
          ]),
          el("div", { className: "metric-cell" }, [
            el("span", { className: "metric-label", text: "sherpa-onnx" }),
            el("span", { className: "metric-value", text: caps.sherpa_compiled ? "Ready" : "Slim Build" }),
          ]),
          el("div", { className: "metric-cell" }, [
            el("span", { className: "metric-label", text: "Installed" }),
            el("span", {
              className: "metric-value tabular",
              text: `${data.models.filter((m) => m.installed).length} / ${data.models.length}`,
            }),
          ]),
          el("div", { className: "metric-cell" }, [
            el("span", { className: "metric-label", text: "Loaded in RAM" }),
            el("span", {
              className: "metric-value metric-text",
              title: data.loaded_model_id || "None",
              text: data.loaded_model_id || "None",
            }),
          ]),
        ]),
      ]),
    ]),
  );

  const rows = data.models.map((m) => {
    const downloading = data.active_downloads.includes(m.id)
      || state.localDownloadsInFlight.has(m.id)
      || Boolean(state.downloadProgress[m.id]);
    const prog = state.downloadProgress[m.id];
    const pct = prog && prog.total_bytes
      ? Math.min(100, Math.round((prog.bytes_downloaded / prog.total_bytes) * 100))
      : 0;
    const isActive = state.settings?.mode === "local" && state.settings?.local_model === m.id;
    const isStreamingPreview = m.engine === STREAMING_PREVIEW_ENGINE;

    const sourceLabel = m.source === "hugging_face_hub_cache"
      ? "Hugging Face Cache"
      : m.source === "pulse_scribe_cache"
      ? "PulseScribe Cache"
      : null;

    const copy = el("div", { className: "row-copy" }, [
      el("div", { className: "row-title" }, [
        el("span", { text: m.display_name }),
        " ",
        isActive ? el("span", { className: "badge badge-success", text: "Active" }) : null,
        isStreamingPreview ? el("span", { className: "badge badge-neutral", text: "Live Preview" }) : null,
      ]),
      el("div", { className: "row-sub tabular" }, [
        el("span", { text: `${m.id} · ${m.installed_bytes ? formatBytes(m.installed_bytes) : `~${m.approx_size_mb} MB`}` }),
        sourceLabel ? el("span", { text: ` · ${sourceLabel}` }) : null,
      ]),
    ]);

    if (downloading) {
      copy.append(
        el("div", {
          className: "meta tabular",
          id: `dl-meta-${m.id}`,
          text: prog
            ? `Downloading ${prog.file} (${prog.file_index}/${prog.total_files}) — ${pct}%`
            : "Starting download…",
        }),
        el("progress", {
          className: "progress-bar",
          id: `dl-bar-${m.id}`,
          max: 100,
          value: pct,
          "aria-label": `Download progress for ${m.display_name}`,
        }),
      );
    }

    const controls = el("div", { className: "row-ctrl" });
    if (downloading) {
      controls.append(
        el("button", {
          type: "button",
          className: "btn btn-sm btn-danger-ghost",
          text: "Cancel",
          onClick: () => runSafe(async () => {
            const cancelled = await invoke("cancel_model_download", { modelId: m.id });
            delete state.downloadProgress[m.id];
            if (cancelled) {
              showToast(`Cancelling ${m.id}…`);
            } else {
              const models = await invoke("list_local_models");
              reconcileDownloadProgress(models);
              state.models = models;
            }
            render();
          }),
        }),
      );
    } else if (m.installed) {
      if (!isStreamingPreview && !isActive) {
        controls.append(
          el("button", {
            type: "button",
            className: "btn btn-sm",
            text: "Use Model",
            onClick: () => runSafe(async () => {
              state.settings = await invoke("save_settings", {
                patch: { mode: "local", local_model: m.id, api_keys: {} },
              });
              if (!state.dirtySettings) {
                state.draft = structuredClone(state.settings);
              } else if (state.draft) {
                state.draft.mode = "local";
                state.draft.local_model = m.id;
              }
              state.app = await invoke("get_app_state");
              showToast(`Switched local model to ${m.id}`);
              render();
            }),
          }),
        );
      }
      if (m.source === "pulse_scribe_cache") {
        controls.append(
          el("button", {
            type: "button",
            className: "btn btn-sm btn-danger-ghost",
            "aria-label": `Delete ${m.display_name}`,
            onClick: () => runSafe(async () => {
              const ok = await confirmAction({
                title: `Delete ${m.display_name}?`,
                message: "This removes the downloaded weights from ~/.pulsescribe/models/.",
                confirmLabel: "Delete model",
                destructive: true,
              });
              if (!ok) return;
              state.models = await invoke("delete_local_model", { modelId: m.id });
              showToast(`Deleted ${m.id}`);
              render();
            }),
          }, [icon("ic-trash", "ic ic-sm")]),
        );
      }
    } else {
      controls.append(
        el("button", {
          type: "button",
          className: "btn btn-sm btn-primary",
          onClick: async () => {
            clearError();
            state.localDownloadsInFlight.add(m.id);
            state.downloadProgress[m.id] = {
              model_id: m.id,
              file: "connecting…",
              file_index: 1,
              total_files: 1,
              bytes_downloaded: 0,
              total_bytes: 0,
            };
            render();
            try {
              const models = await invoke("download_local_model", { modelId: m.id });
              state.localDownloadsInFlight.delete(m.id);
              delete state.downloadProgress[m.id];
              reconcileDownloadProgress(models);
              state.models = models;
              showToast(`Downloaded ${m.display_name}`);
              render();
            } catch (e) {
              state.localDownloadsInFlight.delete(m.id);
              delete state.downloadProgress[m.id];
              showError(e);
              const models = await invoke("list_local_models").catch(() => state.models);
              reconcileDownloadProgress(models);
              state.models = models;
              render();
            }
          },
        }, [icon("ic-download", "ic ic-sm"), el("span", { text: "Download" })]),
      );
    }

    return el("div", { className: "panel-row" }, [copy, controls]);
  });

  stack.append(
    el("section", { className: "section" }, [
      el("h2", { className: "section-title", text: "Model Catalog" }),
      el("p", { className: "section-desc", text: "Existing Hugging Face Hub snapshots are detected automatically without duplicating weights." }),
      el("div", { className: "panel" }, rows),
    ]),
  );

  return stack;
}

async function savePromptsAction() {
  clearError();
  const saveRev = state.promptsRev;
  const submittedDrafts = { ...state.promptDrafts };
  await runSafe(async () => {
    const saved = await invoke("save_prompts", { drafts: submittedDrafts });
    state.prompts = saved;
    if (state.promptsRev === saveRev) {
      state.promptDrafts = { ...saved.drafts };
      state.dirtyPrompts = false;
      showToast("Prompts saved to prompts.toml");
      render();
    } else {
      state.dirtyPrompts = true;
      showToast("Prompts saved — newer edits remain unsaved");
    }
  });
}

function renderPromptsView() {
  const data = state.prompts;
  if (!data) return el("p", { className: "muted", text: "Loading prompts…" });

  const currentCtx = data.contexts.find((c) => c.key === state.activePromptTab) || data.contexts[0];
  const currentVal = state.promptDrafts[currentCtx.key] ?? "";
  const defaultVal = data.defaults[currentCtx.key] ?? "";
  const isModified = currentVal !== defaultVal;

  const switchPromptTab = (key, focusNew = false) => {
    state.activePromptTab = key;
    render();
    if (focusNew) {
      document.getElementById(`prompt-tab-${key}`)?.focus();
    }
  };

  return el("div", { className: "page-stack" }, [
    el("section", { className: "section" }, [
      el("div", {
        className: "tab-bar",
        role: "tablist",
        "aria-label": "Prompt contexts",
        onKeydown: (e) => {
          if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
          e.preventDefault();
          const idx = data.contexts.findIndex((c) => c.key === currentCtx.key);
          const next = (idx + (e.key === "ArrowRight" ? 1 : -1) + data.contexts.length) % data.contexts.length;
          switchPromptTab(data.contexts[next].key, true);
        },
      }, data.contexts.map((ctx) => {
        const custom = (state.promptDrafts[ctx.key] ?? "") !== (data.defaults[ctx.key] ?? "");
        const selected = ctx.key === currentCtx.key;
        return el("button", {
          type: "button",
          role: "tab",
          id: `prompt-tab-${ctx.key}`,
          tabindex: selected ? "0" : "-1",
          className: `tab-btn${selected ? " active" : ""}`,
          "aria-selected": selected ? "true" : "false",
          "aria-controls": "prompt-tabpanel",
          onClick: () => switchPromptTab(ctx.key, true),
        }, [
          el("span", { text: ctx.label }),
          custom ? el("span", { className: "badge badge-warning", text: "Custom" }) : null,
        ]);
      })),
      el("div", {
        className: "panel",
        id: "prompt-tabpanel",
        role: "tabpanel",
        "aria-labelledby": `prompt-tab-${currentCtx.key}`,
      }, [
        el("div", { className: "panel-row stack" }, [
          el("div", { className: "section-head" }, [
            el("div", { className: "row-title", text: currentCtx.label }),
            isModified ? el("button", {
              type: "button",
              className: "btn btn-sm btn-ghost",
              text: "Revert section",
              onClick: () => {
                state.promptDrafts[currentCtx.key] = defaultVal;
                state.promptsRev += 1;
                state.dirtyPrompts = true;
                render();
              },
            }) : el("span", { className: "badge badge-neutral", text: "Default" }),
          ]),
          el("p", { className: "section-desc", text: currentCtx.description }),
          el("textarea", {
            className: "textarea",
            id: "prompt-editor",
            "aria-label": `${currentCtx.label} prompt`,
            value: currentVal,
            onInput: (e) => {
              state.promptDrafts[currentCtx.key] = e.target.value;
              state.promptsRev += 1;
              state.dirtyPrompts = true;
            },
          }),
        ]),
      ]),
    ]),
  ]);
}

async function saveVocabularyAction() {
  clearError();
  const saveRev = state.vocabRev;
  const submittedText = state.vocabKeywords.join("\n");
  await runSafe(async () => {
    const saved = await invoke("save_vocabulary", { rawText: submittedText });
    state.vocabulary = saved;
    if (state.vocabRev === saveRev) {
      state.vocabKeywords = [...saved.keywords];
      state.dirtyVocab = false;
      showToast(`Saved ${state.vocabKeywords.length} vocabulary terms`);
      render();
    } else {
      state.dirtyVocab = true;
      showToast("Vocabulary saved — newer edits remain unsaved");
    }
  });
}

function renderVocabularyView() {
  const data = state.vocabulary;
  if (!data) return el("p", { className: "muted", text: "Loading vocabulary…" });

  const countBadge = el("span", { className: "badge tabular" });
  const chipsHost = el("div", { className: "panel-row stack" });

  const refreshVocabDom = () => {
    const count = state.vocabKeywords.length;
    const overLocal = count > data.local_limit;
    const overDeepgram = count > data.deepgram_limit;
    countBadge.className = `badge tabular ${overDeepgram ? "badge-error" : overLocal ? "badge-warning" : "badge-neutral"}`;
    countBadge.textContent = `${count} terms (local ≤ ${data.local_limit}, Deepgram ≤ ${data.deepgram_limit})`;

    if (count === 0) {
      chipsHost.replaceChildren(
        el("div", { className: "empty-state" }, [
          icon("ic-book"),
          el("h3", { text: "No custom vocabulary yet" }),
          el("p", { text: "Add names, technical acronyms, or brand spellings to improve accuracy." }),
        ]),
      );
    } else {
      chipsHost.replaceChildren(
        el("div", { className: "chip-list" }, state.vocabKeywords.map((kw, idx) =>
          el("span", { className: "chip" }, [
            el("span", { text: kw }),
            el("button", {
              type: "button",
              className: "chip-remove",
              "aria-label": `Remove ${kw}`,
              onClick: () => {
                state.vocabKeywords.splice(idx, 1);
                state.vocabRev += 1;
                state.dirtyVocab = true;
                refreshVocabDom();
              },
            }, [icon("ic-x", "ic ic-sm")]),
          ]),
        )),
      );
    }
  };

  const addInput = el("input", {
    className: "input input-wide",
    id: "vocab-add-input",
    type: "text",
    "aria-label": "Add custom vocabulary terms",
    placeholder: "Add terms (comma-separated, e.g. PulseScribe, Deepgram, Kliebhan)…",
    onKeydown: (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        addTermsFromInput();
      }
    },
  });

  const addTermsFromInput = () => {
    const raw = addInput.value.trim();
    if (!raw) return;
    const incoming = raw.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);
    const seen = new Set(state.vocabKeywords.map((k) => k.toLowerCase()));
    let changed = false;
    for (const term of incoming) {
      if (!seen.has(term.toLowerCase())) {
        state.vocabKeywords.push(term);
        seen.add(term.toLowerCase());
        changed = true;
      }
    }
    if (changed) {
      state.vocabRev += 1;
      state.dirtyVocab = true;
    }
    addInput.value = "";
    refreshVocabDom();
    addInput.focus();
  };

  refreshVocabDom();

  return el("div", { className: "page-stack" }, [
    el("section", { className: "section" }, [
      el("div", { className: "section-head" }, [
        el("h2", { className: "section-title", text: "Custom Keywords" }),
        countBadge,
      ]),
      el("p", { className: "section-desc", text: "Proper nouns, product names, and domain terms biased during transcription." }),
      el("div", { className: "panel" }, [
        el("div", { className: "panel-row" }, [
          addInput,
          el("button", {
            type: "button",
            className: "btn",
            onClick: addTermsFromInput,
          }, [icon("ic-plus", "ic ic-sm"), el("span", { text: "Add" })]),
        ]),
        chipsHost,
      ]),
    ]),
  ]);
}

function renderHistoryList(container) {
  const q = state.historyQuery.trim().toLowerCase();
  const filtered = state.history.filter((entry) => {
    if (state.historyFilter === "refined" && !entry.refined) return false;
    if (!q) return true;
    return (entry.text || "").toLowerCase().includes(q)
      || (entry.app || "").toLowerCase().includes(q)
      || (entry.mode || "").toLowerCase().includes(q);
  });

  if (filtered.length === 0) {
    container.replaceChildren(
      el("div", { className: "panel" }, [
        el("div", { className: "empty-state" }, [
          icon("ic-clock"),
          el("h3", { text: q ? "No matching transcripts" : "No transcripts yet" }),
          el("p", {
            text: q
              ? "Try a different search query or switch the filter back to All."
              : "Your recent dictations will appear here automatically after your first transcription.",
          }),
        ]),
      ]),
    );
    return;
  }

  const groups = new Map();
  for (const item of filtered) {
    const day = formatDayHeading(item.timestamp);
    if (!groups.has(day)) groups.set(day, []);
    groups.get(day).push(item);
  }

  const sections = [];
  for (const [day, items] of groups.entries()) {
    const rows = items.map((entry) => {
      const metaParts = [formatTimeOnly(entry.timestamp), entry.mode, entry.language, entry.app].filter(Boolean);
      return el("div", { className: "panel-row" }, [
        el("div", { className: "row-copy" }, [
          el("div", { className: "history-text", text: entry.text }),
          el("div", { className: "row-sub tabular" }, [
            el("span", { text: metaParts.join(" · ") }),
            entry.refined ? " " : null,
            entry.refined ? el("span", { className: "badge badge-success", text: "Refined" }) : null,
          ]),
        ]),
        el("div", { className: "row-ctrl" }, [
          el("button", {
            type: "button",
            className: "icon-btn",
            "aria-label": "Copy transcript",
            title: "Copy",
            onClick: () => runSafe(async () => {
              await invoke("copy_text_to_clipboard", { text: entry.text });
              showToast("Copied transcript to clipboard");
            }),
          }, [icon("ic-copy", "ic ic-sm")]),
          el("button", {
            type: "button",
            className: "icon-btn",
            "aria-label": "Delete transcript",
            title: "Delete",
            onClick: () => runSafe(async () => {
              await invoke("delete_history_entry", { timestamp: entry.timestamp, text: entry.text });
              state.history = await invoke("get_history", { limit: 200 });
              showToast("Transcript deleted");
              renderHistoryList(container);
              renderTopbarActions();
            }),
          }, [icon("ic-trash", "ic ic-sm")]),
        ]),
      ]);
    });

    sections.push(
      el("section", { className: "section" }, [
        el("div", { className: "day-head" }, [
          el("span", { className: "day-title", text: day }),
          el("span", { className: "meta tabular", text: `${items.length} ${items.length === 1 ? "entry" : "entries"}` }),
        ]),
        el("div", { className: "panel" }, rows),
      ]),
    );
  }
  container.replaceChildren(...sections);
}

function renderHistoryView() {
  const stack = el("div", { className: "page-stack" });
  const listContainer = el("div", { className: "page-stack", id: "history-list" });

  const searchInput = el("input", {
    className: "input",
    id: "history-search",
    type: "search",
    value: state.historyQuery,
    placeholder: "Search transcripts…",
    "aria-label": "Search transcripts",
    onInput: (e) => {
      state.historyQuery = e.target.value;
      renderHistoryList(listContainer);
    },
  });

  const btnAll = el("button", {
    type: "button",
    className: `seg-btn${state.historyFilter === "all" ? " active" : ""}`,
    "aria-pressed": state.historyFilter === "all" ? "true" : "false",
    text: "All",
    onClick: () => setFilter("all"),
  });
  const btnRefined = el("button", {
    type: "button",
    className: `seg-btn${state.historyFilter === "refined" ? " active" : ""}`,
    "aria-pressed": state.historyFilter === "refined" ? "true" : "false",
    text: "Refined",
    onClick: () => setFilter("refined"),
  });

  const setFilter = (next) => {
    state.historyFilter = next;
    btnAll.classList.toggle("active", next === "all");
    btnAll.setAttribute("aria-pressed", next === "all" ? "true" : "false");
    btnRefined.classList.toggle("active", next === "refined");
    btnRefined.setAttribute("aria-pressed", next === "refined" ? "true" : "false");
    renderHistoryList(listContainer);
  };

  stack.append(
    el("div", { className: "toolbar" }, [
      el("div", { className: "search-wrap" }, [icon("ic-search", "ic ic-sm"), searchInput]),
      el("div", { className: "segmented", role: "group", "aria-label": "Filter history" }, [btnAll, btnRefined]),
    ]),
    listContainer,
  );

  renderHistoryList(listContainer);
  return stack;
}

async function setOnboardingStepAction(stepId) {
  state.onboardingStep = stepId;
  await syncTestHotkeyMode("onboarding");
  try {
    await invoke("set_onboarding_state", { step: stepId, choice: state.onboardingChoice });
    if (state.app) {
      state.app.onboarding_step = stepId;
      state.app.onboarding_choice = state.onboardingChoice;
    }
  } catch { /* ignore */ }
  render();
}

function renderOnboardingView() {
  const stack = el("div", { className: "page-stack" });
  const currentIdx = Math.max(0, ONBOARDING_STEPS.findIndex((s) => s.id === state.onboardingStep));

  stack.append(
    el("div", { className: "stepper", role: "list" }, ONBOARDING_STEPS.map((step, i) =>
      el("span", {
        role: "listitem",
        className: `step-pill${i === currentIdx ? " active" : i < currentIdx ? " done" : ""}`,
        "aria-current": i === currentIdx ? "step" : null,
        text: step.label,
      }),
    )),
  );

  const panel = el("div", { className: "panel" });
  const step = state.onboardingStep;

  if (step === "choose_goal") {
    const choices = [
      { id: "fast", title: "Fast Cloud", desc: "Deepgram live streaming with lowest latency and instant interim captions." },
      { id: "private", title: "Private Offline", desc: "100% local transcription with Whisper or Parakeet — audio never leaves your device." },
      { id: "advanced", title: "Custom / Advanced", desc: "OpenAI, Groq, or your own OpenAI-compatible server with LLM refinement." },
    ];
    panel.append(
      el("div", { className: "panel-row stack" }, [
        el("h2", { className: "section-title", text: "How do you want to dictate?" }),
        el("p", { className: "section-desc", text: "You can change provider, model, and API keys anytime in General." }),
        el("div", { className: "choice-grid" }, choices.map((c) => {
          const selected = state.onboardingChoice === c.id;
          return el("button", {
            type: "button",
            className: `choice-card${selected ? " selected" : ""}`,
            "aria-pressed": selected ? "true" : "false",
            onClick: () => { state.onboardingChoice = c.id; render(); },
          }, [
            el("div", { className: "row-title", text: c.title }),
            el("div", { className: "row-sub", text: c.desc }),
          ]);
        })),
      ]),
    );
  } else if (step === "permissions") {
    const perms = state.permissions || { microphone: "unknown", accessibility: false, input_monitoring: false };
    const permRow = (kind, title, desc, granted) => el("div", { className: "panel-row" }, [
      el("div", { className: "row-copy" }, [
        el("div", { className: "row-title", text: title }),
        el("div", { className: "row-sub", text: desc }),
      ]),
      el("div", { className: "row-ctrl" }, [
        el("span", { className: `badge ${granted ? "badge-success" : "badge-warning"}`, text: granted ? "Granted" : "Required" }),
        !granted ? el("button", {
          type: "button",
          className: "btn btn-sm",
          text: "Request",
          onClick: () => runSafe(async () => {
            await invoke("request_permission", { kind });
            state.permissions = await invoke("check_permissions");
            updateDaemonBadge();
            render();
          }),
        }) : null,
        !granted ? el("button", {
          type: "button",
          className: "btn btn-sm btn-ghost",
          text: "Open Settings",
          onClick: () => runSafe(() => invoke("open_privacy_settings", { kind })),
        }) : null,
      ]),
    ]);
    panel.append(
      permRow("microphone", "Microphone", "Captures your voice while dictating.", perms.microphone === "authorized"),
      permRow("accessibility", "Accessibility", "Allows pasting transcripts into the active window.", perms.accessibility),
      permRow("input_monitoring", "Input Monitoring", "Enables global hold-to-record hotkeys such as Fn.", perms.input_monitoring),
    );
  } else if (step === "hotkey") {
    const wizBadge = (res) => {
      if (!res || !res.message) return null;
      const cls = res.level === "error" ? "badge-error" : res.level === "warning" ? "badge-warning" : "badge-success";
      return el("span", { className: `badge ${cls}`, text: res.message });
    };
    panel.append(
      el("div", { className: "panel-row stack" }, [
        el("h2", { className: "section-title", text: "Choose your dictation hotkeys" }),
        el("div", { className: "field-grid" }, [
          el("div", { className: "field" }, [
            el("label", { for: "wiz-toggle", text: "Toggle Hotkey" }),
            el("input", {
              className: "input input-wide mono",
              id: "wiz-toggle",
              value: state.onboardingHotkeys.toggle,
              onInput: (e) => {
                state.onboardingHotkeys.toggle = e.target.value;
                state.onboardingHotkeyValidation.toggle = null;
                document.getElementById("wiz-hotkey-note-toggle")?.replaceChildren();
              },
            }),
            el("div", { id: "wiz-hotkey-note-toggle" }, [wizBadge(state.onboardingHotkeyValidation.toggle)]),
          ]),
          el("div", { className: "field" }, [
            el("label", { for: "wiz-hold", text: "Hold-to-Record Hotkey" }),
            el("input", {
              className: "input input-wide mono",
              id: "wiz-hold",
              value: state.onboardingHotkeys.hold,
              onInput: (e) => {
                state.onboardingHotkeys.hold = e.target.value;
                state.onboardingHotkeyValidation.hold = null;
                document.getElementById("wiz-hotkey-note-hold")?.replaceChildren();
              },
            }),
            el("div", { id: "wiz-hotkey-note-hold" }, [wizBadge(state.onboardingHotkeyValidation.hold)]),
          ]),
        ]),
      ]),
    );
  } else if (step === "test_dictation") {
    const transcript = state.app?.last_test_transcript;
    const testErr = state.app?.last_test_error;
    const st = state.app?.daemon_state || "idle";
    const isRecording = st === "listening" || st === "recording";
    const isProcessing = st === "transcribing" || st === "refining";
    panel.append(
      el("div", { className: "panel-row stack" }, [
        el("h2", { className: "section-title", text: "Try a test dictation" }),
        el("p", {
          className: "section-desc",
          text: "Press your hotkey or click below. Test transcripts stay inside this box and are never pasted.",
        }),
        el("div", { className: "row-ctrl" }, [
          isRecording
            ? el("button", {
                type: "button",
                className: "btn btn-danger",
                onClick: () => runSafe(() => invoke("toggle_dictation")),
              }, [icon("ic-square", "ic ic-sm"), el("span", { text: "Stop & Transcribe" })])
            : isProcessing
            ? el("button", {
                type: "button",
                className: "btn btn-danger",
                onClick: () => runSafe(() => invoke("cancel_dictation")),
              }, [icon("ic-x", "ic ic-sm"), el("span", { text: "Cancel Test" })])
            : el("button", {
                type: "button",
                className: "btn btn-primary",
                onClick: () => runSafe(() => invoke("start_test_dictation")),
              }, [icon("ic-mic", "ic ic-sm"), el("span", { text: "Start Test Dictation" })]),
        ]),
        transcript ? el("pre", { className: "code-pre", text: transcript }) : null,
        testErr ? el("span", { className: "badge badge-error", text: testErr }) : null,
      ]),
    );
  } else {
    const toggleText = state.settings?.toggle_hotkey || "Not set";
    const holdText = state.settings?.hold_hotkey || "Not set";
    panel.append(
      el("div", { className: "panel-row stack" }, [
        el("h2", { className: "section-title", text: "PulseScribe is ready" }),
        el("p", {
          className: "section-desc",
          text: "Close this window anytime — PulseScribe keeps running silently in your menu bar / system tray with zero WebView RAM.",
        }),
        el("div", { className: "row-sub" }, [
          "Toggle: ",
          el("span", { className: "kbd", text: toggleText }),
          " · Hold: ",
          el("span", { className: "kbd", text: holdText }),
        ]),
      ]),
    );
  }

  const navBar = el("div", { className: "panel-row" }, [
    currentIdx > 0
      ? el("button", {
          type: "button",
          className: "btn btn-ghost",
          text: "Back",
          onClick: () => setOnboardingStepAction(ONBOARDING_STEPS[currentIdx - 1].id),
        })
      : el("span"),
    currentIdx < ONBOARDING_STEPS.length - 1
      ? el("button", {
          type: "button",
          className: "btn btn-primary",
          text: "Continue",
          onClick: () => runSafe(async () => {
            clearError();
            if (step === "choose_goal") {
              const patch = state.onboardingChoice === "private"
                ? { mode: "local", refine: false, api_keys: {} }
                : { mode: "deepgram", api_keys: {} };
              state.settings = await invoke("save_settings", { patch });
              if (!state.dirtySettings) state.draft = structuredClone(state.settings);
            } else if (step === "hotkey") {
              const toggleVal = state.onboardingHotkeys.toggle;
              const holdVal = state.onboardingHotkeys.hold;
              const validation = await validateHotkeyPair(toggleVal, holdVal);
              state.onboardingHotkeyValidation = validation;
              const invalid = validation.toggle?.level === "error"
                ? validation.toggle
                : validation.hold?.level === "error"
                ? validation.hold
                : null;
              if (invalid) {
                showError(invalid.message || "Fix the invalid hotkey before continuing.");
                render();
                return;
              }
              state.settings = await invoke("save_settings", {
                patch: {
                  toggle_hotkey: toggleVal,
                  hold_hotkey: holdVal,
                  api_keys: {},
                },
              });
              state.onboardingHotkeyValidation = { toggle: null, hold: null };
              if (!state.dirtySettings) state.draft = structuredClone(state.settings);
            }
            await setOnboardingStepAction(ONBOARDING_STEPS[currentIdx + 1].id);
          }),
        })
      : el("button", {
          type: "button",
          className: "btn btn-primary",
          text: "Finish Setup",
          onClick: () => runSafe(async () => {
            await invoke("set_onboarding_state", { step: "done", choice: state.onboardingChoice });
            state.onboardingStep = "choose_goal";
            state.app = await invoke("get_app_state");
            showToast("Setup complete");
            await navigate("general");
          }),
        }),
  ]);
  panel.append(navBar);

  stack.append(panel);
  return stack;
}

function renderDiagnosticsView() {
  const stack = el("div", { className: "page-stack" });
  const app = state.app || {};
  const perms = state.permissions || { microphone: "unknown", accessibility: false, input_monitoring: false };
  const isError = app.daemon_state === "error";
  const missingPerms = perms.microphone !== "authorized" || !perms.accessibility || !perms.input_monitoring;

  const verdictCls = isError ? "error" : missingPerms ? "warn" : "";
  const verdictText = isError
    ? "Daemon reported an error"
    : missingPerms
    ? "Running with missing permissions"
    : "PulseScribe is running normally";

  stack.append(
    el("div", { className: "verdict" }, [
      el("span", { className: `verdict-icon ${verdictCls}` }, [
        icon(isError ? "ic-alert" : missingPerms ? "ic-shield" : "ic-check"),
      ]),
      el("div", {}, [
        el("h2", { className: "verdict-title", text: verdictText }),
        el("div", { className: "meta", text: app.status_hint || "All subsystems ready." }),
      ]),
    ]),
  );

  stack.append(
    el("div", { className: "panel" }, [
      el("div", { className: "metrics-grid" }, [
        el("div", { className: "metric-cell" }, [
          el("span", { className: "metric-label", text: "Version" }),
          el("span", { className: "metric-value tabular", text: `v${app.version || "0.1.0"}` }),
        ]),
        el("div", { className: "metric-cell" }, [
          el("span", { className: "metric-label", text: "Platform" }),
          el("span", { className: "metric-value", text: (app.platform || "desktop").toUpperCase() }),
        ]),
        el("div", { className: "metric-cell" }, [
          el("span", { className: "metric-label", text: "Provider" }),
          el("span", { className: "metric-value metric-text", text: app.mode || "deepgram" }),
        ]),
        el("div", { className: "metric-cell" }, [
          el("span", { className: "metric-label", text: "Local Models" }),
          el("span", {
            className: "metric-value tabular",
            text: String((state.models?.models || []).filter((m) => m.installed).length),
          }),
        ]),
      ]),
    ]),
  );

  const upd = state.updateResult;
  stack.append(
    el("section", { className: "section" }, [
      el("h2", { className: "section-title", text: "Updates & Support" }),
      el("div", { className: "panel" }, [
        el("div", { className: "panel-row" }, [
          el("div", { className: "row-copy" }, [
            el("div", { className: "row-title", text: "Software Updates" }),
            el("div", {
              className: "row-sub",
              text: upd
                ? upd.error || (upd.update_available
                    ? `Version v${upd.latest_version} is available (installed: v${upd.current_version}).`
                    : `Up to date (v${upd.current_version}).`)
                : "Check GitHub Releases for newer builds.",
            }),
          ]),
          el("div", { className: "row-ctrl" }, [
            upd?.update_available && upd.release_url
              ? el("button", {
                  type: "button",
                  className: "btn btn-sm btn-primary",
                  text: "Download Release",
                  onClick: () => runSafe(() => invoke("open_release_url", { url: upd.release_url })),
                })
              : null,
            el("button", {
              type: "button",
              className: "btn btn-sm",
              text: "Check for Updates",
              onClick: () => runSafe(async () => {
                state.updateResult = await invoke("check_for_updates");
                if (state.updateResult.error) {
                  showError(state.updateResult.error);
                } else {
                  showToast(state.updateResult.update_available ? "Update available" : "Up to date");
                }
                render();
              }),
            }),
          ]),
        ]),
        el("div", { className: "panel-row" }, [
          el("div", { className: "row-copy" }, [
            el("div", { className: "row-title", text: "Configuration Directory" }),
            el("div", { className: "row-sub mono", text: app.config_dir || "~/.pulsescribe" }),
          ]),
          el("div", { className: "row-ctrl" }, [
            el("button", {
              type: "button",
              className: "btn btn-sm",
              text: "Open",
              onClick: () => runSafe(() => invoke("open_config_dir")),
            }),
          ]),
        ]),
      ]),
    ]),
  );

  if (state.diagnostics) {
    stack.append(
      el("div", { className: "panel" }, [
        el("details", { className: "panel-details", open: true }, [
          el("summary", {}, [
            el("div", {}, [
              el("div", { className: "row-title", text: "Exported Diagnostics Bundle" }),
              el("div", { className: "row-sub mono", text: state.diagnostics.path }),
            ]),
            icon("ic-chevron-right", "ic chev"),
          ]),
          el("div", { className: "details-body" }, [
            el("pre", { className: "code-pre", text: state.diagnostics.preview }),
          ]),
        ]),
      ]),
    );
  }

  return stack;
}

function render() {
  renderTopbarActions();
  let view;
  switch (state.route) {
    case "models": view = renderModelsView(); break;
    case "prompts": view = renderPromptsView(); break;
    case "vocabulary": view = renderVocabularyView(); break;
    case "history": view = renderHistoryView(); break;
    case "onboarding": view = renderOnboardingView(); break;
    case "diagnostics": view = renderDiagnosticsView(); break;
    default: view = renderGeneralView(); break;
  }
  dom.viewRoot.replaceChildren(view);
}

async function bootstrap() {
  for (const btn of document.querySelectorAll("[data-route]")) {
    btn.addEventListener("click", () => navigate(btn.dataset.route));
  }

  const toggleSidebar = () => {
    const collapsed = dom.shell.classList.toggle("sidebar-collapsed");
    dom.btnToggleSidebar?.setAttribute("aria-expanded", collapsed ? "false" : "true");
    dom.btnExpandSidebar?.setAttribute("aria-expanded", collapsed ? "false" : "true");
    (collapsed ? dom.btnExpandSidebar : dom.btnToggleSidebar)?.focus();
  };
  dom.btnToggleSidebar?.addEventListener("click", toggleSidebar);
  dom.btnExpandSidebar?.addEventListener("click", toggleSidebar);

  document.getElementById("btn-dismiss-error")?.addEventListener("click", clearError);
  document.getElementById("btn-save-changes")?.addEventListener("click", saveSettingsAction);
  document.getElementById("btn-discard-changes")?.addEventListener("click", () => {
    state.settingsRev += 1;
    state.draft = structuredClone(state.settings);
    state.apiKeyDrafts = {};
    state.apiKeyClears.clear();
    state.hotkeyValidation = { toggle: null, hold: null };
    state.dirtySettings = false;
    updateDirtyBar();
    render();
  });

  for (const btn of document.querySelectorAll("[data-theme-choice]")) {
    btn.addEventListener("click", async () => {
      const next = { theme: btn.dataset.themeChoice, accent: state.themePref.accent };
      applyThemePref(next);
      try { await invoke("save_theme_preference", next); } catch { /* ignore */ }
    });
  }
  for (const btn of document.querySelectorAll("[data-accent-choice]")) {
    btn.addEventListener("click", async () => {
      const next = { theme: state.themePref.theme, accent: btn.dataset.accentChoice };
      applyThemePref(next);
      try { await invoke("save_theme_preference", next); } catch { /* ignore */ }
    });
  }

  await listen("pulsescribe://navigate", (ev) => {
    if (typeof ev.payload === "string") navigate(ev.payload);
  });

  await listen("pulsescribe://daemon-event", async (ev) => {
    const payload = ev.payload;
    if (!payload) return;
    if (payload.type === "state_changed" || payload.type === "test_transcript" || payload.type === "transcript_delivered") {
      state.app = await invoke("get_app_state");
      updateDaemonBadge();
      if (state.route === "history" && payload.type === "transcript_delivered") {
        state.history = await invoke("get_history", { limit: 200 });
        const listHost = document.getElementById("history-list");
        if (listHost) {
          renderHistoryList(listHost);
          renderTopbarActions();
        } else {
          render();
        }
      } else if (state.route === "onboarding" && state.onboardingStep === "test_dictation") {
        render();
      } else {
        renderTopbarActions();
      }
    }
  });

  await listen("pulsescribe://model-download-progress", (ev) => {
    const p = ev.payload;
    if (p && p.model_id) {
      state.downloadProgress[p.model_id] = p;
      if (state.route === "models" && !updateModelProgressInPlace(p)) {
        render();
      }
    }
  });

  await listen("pulsescribe://model-download-finished", async (ev) => {
    const p = ev.payload;
    if (!p || !p.model_id) return;
    delete state.downloadProgress[p.model_id];
    if (!state.localDownloadsInFlight.has(p.model_id)) {
      if (!p.ok && p.error) showError(p.error);
      try {
        const models = await invoke("list_local_models");
        reconcileDownloadProgress(models);
        state.models = models;
      } catch { /* ignore */ }
      if (state.route === "models" || state.route === "general" || state.route === "diagnostics") {
        render();
      }
    }
  });

  await runSafe(async () => {
    const [app, themePref, perms] = await Promise.all([
      invoke("get_app_state"),
      invoke("get_theme_preference"),
      invoke("check_permissions"),
    ]);
    state.app = app;
    state.permissions = perms;
    applyThemePref(themePref);
    updateDaemonBadge();
    await navigate(app.initial_route || "general");
  });
}

bootstrap();
