import { invoke, listen } from "./api.js";
import {
  applyStaticTranslations,
  detectSystemLanguage,
  getUiLanguagePref,
  localizeDaemonStatus,
  localizePromptContext,
  setUiLanguagePref,
  t,
} from "./i18n.js";

const VALID_ROUTES = new Set([
  "general",
  "models",
  "prompts",
  "vocabulary",
  "history",
  "onboarding",
  "diagnostics",
]);

const PROVIDER_IDS = ["deepgram", "openai", "groq", "openai_compatible", "local"];

const LANGUAGE_CODES = ["auto", "de", "en", "fr", "es", "it", "nl", "pt", "pl"];

const API_KEY_ENV_KEYS = [
  "DEEPGRAM_API_KEY",
  "OPENAI_API_KEY",
  "GROQ_API_KEY",
  "OPENROUTER_API_KEY",
  "GEMINI_API_KEY",
  "OPENAI_COMPATIBLE_API_KEY",
];

const ONBOARDING_STEP_IDS = [
  "choose_goal",
  "permissions",
  "hotkey",
  "test_dictation",
  "cheat_sheet",
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
  committedUiLanguagePref: "system",
  uiLanguageSaveSeq: 0,
  uiLanguageSaveChain: Promise.resolve(),
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

function confirmAction({ title, message, confirmLabel = t("common.confirm"), destructive = false }) {
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
  const status = localizeDaemonStatus(app);
  dom.daemonBadge.textContent = status.label;
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
    ? t("common.unsavedWithKeys")
    : t("common.unsavedChanges");
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
  if (!datePart) return t("history.earlier");
  const now = new Date();
  const today = localDateKey(now);
  const yesterday = localDateKey(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1));
  if (datePart === today) return t("history.today");
  if (datePart === yesterday) return t("history.yesterday");
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
  state.route = VALID_ROUTES.has(route) ? route : "general";
  dom.pageTitle.textContent = t(`route.${state.route}`);
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
      ? t("action.stopDictation")
      : isProcessing
      ? t("action.cancelDictation")
      : t("action.startDictation");
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
            showToast(t("models.unloadedToast"));
            render();
          }),
        }, [el("span", { text: t("action.unloadRam") })]),
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
          showToast(t("models.refreshedToast"));
          render();
        }),
      }, [icon("ic-refresh", "ic ic-sm"), el("span", { text: t("common.refresh") })]),
    );
  } else if (state.route === "prompts") {
    dom.topbarActions.append(
      el("button", {
        type: "button",
        className: "btn btn-danger-ghost",
        onClick: () => runSafe(async () => {
          const ok = await confirmAction({
            title: t("prompts.resetTitle"),
            message: t("prompts.resetMessage"),
            confirmLabel: t("prompts.resetConfirm"),
            destructive: true,
          });
          if (!ok) return;
          state.promptsRev += 1;
          state.prompts = await invoke("reset_prompts");
          state.promptDrafts = { ...state.prompts.drafts };
          state.dirtyPrompts = false;
          showToast(t("prompts.resetToast"));
          render();
        }),
      }, [el("span", { text: t("action.resetDefaults") })]),
      el("button", {
        type: "button",
        className: "btn btn-primary",
        onClick: savePromptsAction,
      }, [icon("ic-check", "ic ic-sm"), el("span", { text: t("action.savePrompts") })]),
    );
  } else if (state.route === "vocabulary") {
    dom.topbarActions.append(
      el("button", {
        type: "button",
        className: "btn btn-primary",
        onClick: saveVocabularyAction,
      }, [icon("ic-check", "ic ic-sm"), el("span", { text: t("action.saveVocabulary") })]),
    );
  } else if (state.route === "history") {
    if (state.history.length > 0) {
      dom.topbarActions.append(
        el("button", {
          type: "button",
          className: "btn btn-danger-ghost",
          onClick: () => runSafe(async () => {
            const ok = await confirmAction({
              title: t("history.clearTitle"),
              message: t("history.clearMessage", { count: state.history.length }),
              confirmLabel: t("history.clearConfirm"),
              destructive: true,
            });
            if (!ok) return;
            await invoke("clear_history");
            state.history = [];
            showToast(t("history.clearedToast"));
            render();
          }),
        }, [icon("ic-trash", "ic ic-sm"), el("span", { text: t("action.clearAll") })]),
      );
    }
  } else if (state.route === "diagnostics") {
    dom.topbarActions.append(
      el("button", {
        type: "button",
        className: "btn",
        onClick: () => runSafe(() => invoke("open_config_dir")),
      }, [icon("ic-folder", "ic ic-sm"), el("span", { text: t("action.configFolder") })]),
      el("button", {
        type: "button",
        className: "btn btn-primary",
        onClick: () => runSafe(async () => {
          state.diagnostics = await invoke("export_diagnostics");
          showToast(t("diag.exportedToast"));
          render();
        }),
      }, [icon("ic-download", "ic ic-sm"), el("span", { text: t("action.exportDiagnostics") })]),
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
    showError(invalidHotkey.message || t("settings.fixHotkey"));
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
      showToast(t("settings.saved"));
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
      showToast(t("settings.savedPartial"));
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

function applyUiLanguageToDom(nextPref) {
  const { pref, resolved } = setUiLanguagePref(nextPref);
  if (state.app) {
    state.app.ui_language = pref;
    state.app.resolved_ui_language = resolved;
  }
  applyStaticTranslations();
  dom.pageTitle.textContent = t(`route.${state.route}`);
  updateDaemonBadge();
  updateDirtyBar();
  render();
  return { pref, resolved };
}

function enqueueUiLanguageSave({ pref, resolved, silent = false }) {
  const seq = ++state.uiLanguageSaveSeq;
  const run = async () => {
    if (seq !== state.uiLanguageSaveSeq) return;
    try {
      await invoke("save_ui_language", { language: pref, resolved });
      state.committedUiLanguagePref = pref;
      if (!silent && seq === state.uiLanguageSaveSeq) {
        showToast(t("general.uiLangSaved"));
      }
    } catch (err) {
      if (silent) return;
      if (seq === state.uiLanguageSaveSeq) {
        applyUiLanguageToDom(state.committedUiLanguagePref);
        showError(err);
      }
    }
  };
  state.uiLanguageSaveChain = state.uiLanguageSaveChain.then(run, run);
  return state.uiLanguageSaveChain;
}

function applyUiLanguageChange(nextPref) {
  clearError();
  const { pref, resolved } = applyUiLanguageToDom(nextPref);
  return enqueueUiLanguageSave({ pref, resolved, silent: false });
}

function renderGeneralView() {
  const d = state.draft;
  if (!d) return el("p", { className: "muted", text: t("general.loading") });

  const stack = el("div", { className: "page-stack" });

  // 1. Dictation Provider
  const providerPanel = el("div", { className: "panel" }, [
    el("div", { className: "panel-row" }, [
      el("div", { className: "row-copy" }, [
        el("div", { className: "row-title", text: t("general.providerTitle") }),
        el("div", { className: "row-sub", text: t("general.providerDesc") }),
      ]),
      el("div", { className: "row-ctrl" }, [
        el("select", {
          className: "select",
          id: "setting-mode",
          "aria-label": t("general.providerTitle"),
          onChange: (e) => { setDraftField("mode", e.target.value); render(); },
        }, PROVIDER_IDS.map((id) => el("option", { value: id, text: t(`provider.${id}`), selected: d.mode === id }))),
      ]),
    ]),
  ]);

  if (d.mode === "local") {
    const offlineModels = (state.models?.models || []).filter((m) => m.engine !== STREAMING_PREVIEW_ENGINE);
    providerPanel.append(
      el("div", { className: "panel-row" }, [
        el("div", { className: "row-copy" }, [
          el("div", { className: "row-title", text: t("general.localModelTitle") }),
          el("div", { className: "row-sub", text: t("general.localModelDesc") }),
        ]),
        el("div", { className: "row-ctrl" }, [
          el("select", {
            className: "select",
            "aria-label": t("general.localModelTitle"),
            onChange: (e) => setDraftField("local_model", e.target.value),
          }, (offlineModels.length > 0 ? offlineModels : [{ id: d.local_model || "large-v3-turbo", display_name: d.local_model || "large-v3-turbo" }]).map((m) =>
            el("option", { value: m.id, text: `${m.display_name || m.id}${m.installed ? t("general.installedSuffix") : ""}`, selected: d.local_model === m.id }),
          )),
          el("button", { type: "button", className: "btn btn-sm", text: t("common.manage"), onClick: () => navigate("models") }),
        ]),
      ]),
    );
  } else if (d.mode === "openai_compatible") {
    providerPanel.append(
      el("div", { className: "panel-row" }, [
        el("div", { className: "row-copy" }, [
          el("div", { className: "row-title", text: t("general.endpointUrlTitle") }),
          el("div", { className: "row-sub", text: t("general.endpointUrlDesc") }),
        ]),
        el("div", { className: "row-ctrl" }, [
          el("input", {
            className: "input",
            type: "url",
            "aria-label": t("general.endpointUrlTitle"),
            value: d.openai_compatible_base_url,
            placeholder: "http://localhost:8000/v1",
            onInput: (e) => setDraftField("openai_compatible_base_url", e.target.value),
          }),
        ]),
      ]),
      el("div", { className: "panel-row" }, [
        el("div", { className: "row-copy" }, [
          el("div", { className: "row-title", text: t("general.customModelTitle") }),
        ]),
        el("div", { className: "row-ctrl" }, [
          el("input", {
            className: "input",
            type: "text",
            "aria-label": t("general.customModelTitle"),
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
          el("div", { className: "row-title", text: t("general.cloudModelTitle") }),
          el("div", { className: "row-sub", text: t("general.cloudModelDesc") }),
        ]),
        el("div", { className: "row-ctrl" }, [
          el("input", {
            className: "input",
            type: "text",
            "aria-label": t("general.cloudModelTitle"),
            value: d.model,
            placeholder: d.mode === "deepgram" ? "nova-3" : t("general.cloudModelPlaceholder"),
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
          el("div", { className: "row-title", text: t("general.liveStreamingTitle") }),
          el("div", { className: "row-sub", text: t("general.liveStreamingDesc") }),
        ]),
        el("div", { className: "row-ctrl" }, [
          makeSwitch(d.streaming, t("general.liveStreamingTitle"), (v) => setDraftField("streaming", v)),
        ]),
      ]),
      el("div", { className: "panel-row" }, [
        el("div", { className: "row-copy" }, [
          el("div", { className: "row-title", text: t("general.warmSocketTitle") }),
          el("div", { className: "row-sub", text: t("general.warmSocketDesc") }),
        ]),
        el("div", { className: "row-ctrl" }, [
          makeSwitch(d.deepgram_warm_websocket, t("general.warmSocketTitle"), (v) => setDraftField("deepgram_warm_websocket", v)),
        ]),
      ]),
    );
  }

  providerPanel.append(
    el("div", { className: "panel-row" }, [
      el("div", { className: "row-copy" }, [
        el("div", { className: "row-title", text: t("general.dictationLangTitle") }),
        el("div", { className: "row-sub", text: t("general.dictationLangDesc") }),
      ]),
      el("div", { className: "row-ctrl" }, [
        el("select", {
          className: "select",
          "aria-label": t("general.dictationLangTitle"),
          onChange: (e) => setDraftField("language", e.target.value),
        }, LANGUAGE_CODES.map((code) => el("option", { value: code, text: t(`lang.${code}`), selected: d.language === code }))),
      ]),
    ]),
  );

  stack.append(
    el("section", { className: "section" }, [
      el("h2", { className: "section-title", text: t("general.dictationTitle") }),
      el("p", { className: "section-desc", text: t("general.dictationDesc") }),
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
    el("option", { value: "", text: t("general.systemDefault"), selected: !d.input_device }),
    ...state.devices.map((dev) => el("option", {
      value: dev.name,
      text: `${dev.name}${dev.is_default ? t("general.defaultSuffix") : ""}`,
      selected: d.input_device === dev.name,
    })),
  ];

  stack.append(
    el("section", { className: "section" }, [
      el("h2", { className: "section-title", text: t("general.hotkeysTitle") }),
      el("p", { className: "section-desc", text: t("general.hotkeysDesc") }),
      el("div", { className: "panel" }, [
        el("div", { className: "panel-row" }, [
          el("div", { className: "row-copy" }, [
            el("div", { className: "row-title", text: t("general.toggleHotkeyTitle") }),
            el("div", { className: "row-sub", text: t("general.toggleHotkeyDesc") }),
            makeHotkeyNoteHost("toggle"),
          ]),
          el("div", { className: "row-ctrl" }, [
            el("input", {
              className: "input input-sm mono",
              id: "input-toggle-hotkey",
              type: "text",
              "aria-label": t("general.toggleHotkeyTitle"),
              value: d.toggle_hotkey,
              placeholder: state.app?.platform === "windows" ? "ctrl+alt+r" : "f19",
              onInput: (e) => setDraftField("toggle_hotkey", e.target.value),
              onBlur: (e) => validateHotkeyField("toggle", e.target.value),
            }),
          ]),
        ]),
        el("div", { className: "panel-row" }, [
          el("div", { className: "row-copy" }, [
            el("div", { className: "row-title", text: t("general.holdHotkeyTitle") }),
            el("div", { className: "row-sub", text: t("general.holdHotkeyDesc") }),
            makeHotkeyNoteHost("hold"),
          ]),
          el("div", { className: "row-ctrl" }, [
            el("input", {
              className: "input input-sm mono",
              id: "input-hold-hotkey",
              type: "text",
              "aria-label": t("general.holdHotkeyTitle"),
              value: d.hold_hotkey,
              placeholder: state.app?.platform === "windows" ? "ctrl+win" : "fn",
              onInput: (e) => setDraftField("hold_hotkey", e.target.value),
              onBlur: (e) => validateHotkeyField("hold", e.target.value),
            }),
          ]),
        ]),
        el("div", { className: "panel-row" }, [
          el("div", { className: "row-copy" }, [
            el("div", { className: "row-title", text: t("general.micTitle") }),
            el("div", { className: "row-sub", text: t("general.micDesc") }),
          ]),
          el("div", { className: "row-ctrl" }, [
            el("select", {
              className: "select",
              "aria-label": t("general.micTitle"),
              onChange: (e) => setDraftField("input_device", e.target.value),
            }, deviceOptions),
          ]),
        ]),
        el("div", { className: "panel-row" }, [
          el("div", { className: "row-copy" }, [
            el("div", { className: "row-title", text: t("general.overlayTitle") }),
            el("div", { className: "row-sub", text: t("general.overlayDesc") }),
          ]),
          el("div", { className: "row-ctrl" }, [
            makeSwitch(d.overlay_enabled, t("general.overlayTitle"), (v) => setDraftField("overlay_enabled", v)),
          ]),
        ]),
        el("div", { className: "panel-row" }, [
          el("div", { className: "row-copy" }, [
            el("div", { className: "row-title", text: t("general.soundTitle") }),
            el("div", { className: "row-sub", text: t("general.soundDesc") }),
          ]),
          el("div", { className: "row-ctrl" }, [
            makeSwitch(d.sound_enabled, t("general.soundTitle"), (v) => setDraftField("sound_enabled", v)),
          ]),
        ]),
        el("div", { className: "panel-row" }, [
          el("div", { className: "row-copy" }, [
            el("div", { className: "row-title", text: t("general.autoPasteTitle") }),
            el("div", { className: "row-sub", text: t("general.autoPasteDesc", { shortcut: state.app?.paste_shortcut || "⌘V" }) }),
          ]),
          el("div", { className: "row-ctrl" }, [
            makeSwitch(d.auto_paste, t("general.autoPasteTitle"), (v) => setDraftField("auto_paste", v)),
          ]),
        ]),
        el("div", { className: "panel-row" }, [
          el("div", { className: "row-copy" }, [
            el("div", { className: "row-title", text: t("general.showRtfTitle") }),
            el("div", { className: "row-sub", text: t("general.showRtfDesc") }),
          ]),
          el("div", { className: "row-ctrl" }, [
            makeSwitch(d.show_rtf, t("general.showRtfTitle"), (v) => setDraftField("show_rtf", v)),
          ]),
        ]),
        state.autostart?.supported
          ? el("div", { className: "panel-row" }, [
              el("div", { className: "row-copy" }, [
                el("div", { className: "row-title", text: t("general.autostartTitle") }),
                el("div", {
                  className: "row-sub",
                  text: t("general.autostartDesc"),
                }),
              ]),
              el("div", { className: "row-ctrl" }, [
                (() => {
                  const isPending = state.autostartPending !== null;
                  const checked = isPending ? state.autostartPending : Boolean(state.autostart.enabled);
                  const sw = makeSwitch(checked, t("general.autostartTitle"), async (next) => {
                    if (state.autostartPending !== null) return;
                    state.autostartPending = next;
                    sw.disabled = true;
                    clearError();
                    try {
                      state.autostart = await invoke("set_autostart", { enabled: next });
                      showToast(state.autostart.enabled ? t("general.autostartEnabled") : t("general.autostartDisabled"));
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

  // 3. Interface Language (applies immediately without dirty save bar)
  const uiPref = getUiLanguagePref();
  const sysLangName = detectSystemLanguage() === "de" ? t("general.uiLangNameDe") : t("general.uiLangNameEn");
  stack.append(
    el("section", { className: "section" }, [
      el("h2", { className: "section-title", text: t("general.interfaceTitle") }),
      el("p", { className: "section-desc", text: t("general.interfaceDesc") }),
      el("div", { className: "panel" }, [
        el("div", { className: "panel-row" }, [
          el("div", { className: "row-copy" }, [
            el("div", { className: "row-title", text: t("general.uiLangTitle") }),
            el("div", { className: "row-sub", text: t("general.uiLangDesc") }),
          ]),
          el("div", { className: "row-ctrl" }, [
            el("select", {
              className: "select",
              id: "setting-ui-language",
              "aria-label": t("general.uiLangTitle"),
              onChange: (e) => applyUiLanguageChange(e.target.value),
            }, [
              el("option", { value: "system", text: t("general.uiLangSystem", { lang: sysLangName }), selected: uiPref === "system" }),
              el("option", { value: "en", text: t("general.uiLangNameEn"), selected: uiPref === "en" }),
              el("option", { value: "de", text: t("general.uiLangNameDe"), selected: uiPref === "de" }),
            ]),
          ]),
        ]),
      ]),
    ]),
  );

  // 4. LLM Refinement (toggles dependent block in place without destroying switch focus)
  const refineDetails = el("div", { id: "refine-details", hidden: !d.refine }, [
    el("div", { className: "panel-row" }, [
      el("div", { className: "row-copy" }, [
        el("div", { className: "row-title", text: t("general.refineProviderTitle") }),
      ]),
      el("div", { className: "row-ctrl" }, [
        el("select", {
          className: "select",
          "aria-label": t("general.refineProviderTitle"),
          onChange: (e) => setDraftField("refine_provider", e.target.value),
        }, [
          ["", t("general.refineProviderDefault")],
          ["groq", "Groq"],
          ["openrouter", "OpenRouter"],
          ["openai", "OpenAI"],
          ["gemini", "Google Gemini"],
        ].map(([v, l]) => el("option", { value: v, text: l, selected: d.refine_provider === v }))),
      ]),
    ]),
    el("div", { className: "panel-row" }, [
      el("div", { className: "row-copy" }, [
        el("div", { className: "row-title", text: t("general.refineModelTitle") }),
        el("div", { className: "row-sub", text: t("general.refineModelDesc") }),
      ]),
      el("div", { className: "row-ctrl" }, [
        el("input", {
          className: "input",
          type: "text",
          "aria-label": t("general.refineModelTitle"),
          value: d.refine_model,
          placeholder: t("general.refineModelPlaceholder"),
          onInput: (e) => setDraftField("refine_model", e.target.value),
        }),
      ]),
    ]),
    el("div", { className: "panel-row" }, [
      el("div", { className: "row-copy" }, [
        el("div", { className: "row-title", text: t("general.voiceCommandsTitle") }),
        el("div", { className: "row-sub", text: t("general.voiceCommandsDesc") }),
      ]),
      el("div", { className: "row-ctrl" }, [
        makeSwitch(d.voice_commands, t("general.voiceCommandsTitle"), (v) => setDraftField("voice_commands", v)),
      ]),
    ]),
  ]);

  const refinePanel = el("div", { className: "panel" }, [
    el("div", { className: "panel-row" }, [
      el("div", { className: "row-copy" }, [
        el("div", { className: "row-title", text: t("general.refineToggleTitle") }),
        el("div", { className: "row-sub", text: t("general.refineToggleDesc") }),
      ]),
      el("div", { className: "row-ctrl" }, [
        makeSwitch(d.refine, t("general.refineToggleTitle"), (v) => {
          setDraftField("refine", v);
          refineDetails.hidden = !v;
        }),
      ]),
    ]),
    refineDetails,
  ]);

  stack.append(
    el("section", { className: "section" }, [
      el("h2", { className: "section-title", text: t("general.refineSectionTitle") }),
      el("p", { className: "section-desc", text: t("general.refineSectionDesc") }),
      refinePanel,
    ]),
  );

  // 5. API Keys (typing & clearing back to empty never deletes an existing key — only explicit Clear does!)
  const keyRows = API_KEY_ENV_KEYS.map((envKey) => {
    const label = t(`apiKey.${envKey}`);
    const configured = Boolean(d.api_keys_configured?.[envKey]);
    const masked = d.api_keys_masked?.[envKey];
    const markedForClear = state.apiKeyClears.has(envKey);
    const badgeNode = el("span", {
      className: `badge ${markedForClear ? "badge-warning" : configured ? "badge-success" : "badge-neutral"}`,
      text: markedForClear
        ? t("apiKey.pendingRemoval")
        : configured
        ? t("apiKey.saved", { masked: masked || "••••" })
        : t("apiKey.notConfigured"),
    });

    const inputNode = el("input", {
      className: "input input-sm mono",
      type: "password",
      autocomplete: "off",
      "aria-label": label,
      value: state.apiKeyDrafts[envKey] || "",
      placeholder: configured ? t("apiKey.placeholderNew") : t("apiKey.placeholderPaste"),
      onInput: (e) => {
        const val = e.target.value;
        state.settingsRev += 1;
        if (val.trim()) {
          state.apiKeyDrafts[envKey] = val;
          state.apiKeyClears.delete(envKey);
          badgeNode.className = "badge badge-warning";
          badgeNode.textContent = t("apiKey.pendingUpdate");
          state.dirtySettings = true;
        } else {
          delete state.apiKeyDrafts[envKey];
          badgeNode.className = `badge ${state.apiKeyClears.has(envKey) ? "badge-warning" : configured ? "badge-success" : "badge-neutral"}`;
          badgeNode.textContent = state.apiKeyClears.has(envKey)
            ? t("apiKey.pendingRemoval")
            : configured
            ? t("apiKey.saved", { masked: masked || "••••" })
            : t("apiKey.notConfigured");
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
          text: t("common.clear"),
          onClick: () => {
            state.settingsRev += 1;
            delete state.apiKeyDrafts[envKey];
            state.apiKeyClears.add(envKey);
            inputNode.value = "";
            badgeNode.className = "badge badge-warning";
            badgeNode.textContent = t("apiKey.pendingRemoval");
            state.dirtySettings = true;
            updateDirtyBar();
            showToast(t("apiKey.markedForRemoval", { label }));
          },
        }) : null,
      ]),
    ]);
  });

  stack.append(
    el("section", { className: "section" }, [
      el("h2", { className: "section-title", text: t("general.apiKeysTitle") }),
      el("p", { className: "section-desc", text: t("general.apiKeysDesc") }),
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
  metaNode.textContent = t("models.downloading", {
    file: progress.file,
    index: progress.file_index,
    total: progress.total_files,
    pct,
  });
  barNode.value = pct;
  return true;
}

function renderModelsView() {
  const data = state.models;
  if (!data) return el("p", { className: "muted", text: t("models.scanning") });

  const stack = el("div", { className: "page-stack" });
  const caps = data.capabilities || {};

  stack.append(
    el("section", { className: "section" }, [
      el("div", { className: "panel" }, [
        el("div", { className: "metrics-grid" }, [
          el("div", { className: "metric-cell" }, [
            el("span", { className: "metric-label", text: "whisper-rs" }),
            el("span", { className: "metric-value", text: caps.whisper_compiled ? t("common.ready") : t("models.slimBuild") }),
          ]),
          el("div", { className: "metric-cell" }, [
            el("span", { className: "metric-label", text: "sherpa-onnx" }),
            el("span", { className: "metric-value", text: caps.sherpa_compiled ? t("common.ready") : t("models.slimBuild") }),
          ]),
          el("div", { className: "metric-cell" }, [
            el("span", { className: "metric-label", text: t("models.installed") }),
            el("span", {
              className: "metric-value tabular",
              text: `${data.models.filter((m) => m.installed).length} / ${data.models.length}`,
            }),
          ]),
          el("div", { className: "metric-cell" }, [
            el("span", { className: "metric-label", text: t("models.loadedInRam") }),
            el("span", {
              className: "metric-value metric-text",
              title: data.loaded_model_id || t("common.none"),
              text: data.loaded_model_id || t("common.none"),
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
      ? t("models.sourceHf")
      : m.source === "pulse_scribe_cache"
      ? t("models.sourceLocal")
      : null;

    const copy = el("div", { className: "row-copy" }, [
      el("div", { className: "row-title" }, [
        el("span", { text: m.display_name }),
        " ",
        isActive ? el("span", { className: "badge badge-success", text: t("common.active") }) : null,
        isStreamingPreview ? el("span", { className: "badge badge-neutral", text: t("models.livePreview") }) : null,
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
            ? t("models.downloading", {
                file: prog.file,
                index: prog.file_index,
                total: prog.total_files,
                pct,
              })
            : t("models.startingDownload"),
        }),
        el("progress", {
          className: "progress-bar",
          id: `dl-bar-${m.id}`,
          max: 100,
          value: pct,
          "aria-label": t("models.progressAria", { name: m.display_name }),
        }),
      );
    }

    const controls = el("div", { className: "row-ctrl" });
    if (downloading) {
      controls.append(
        el("button", {
          type: "button",
          className: "btn btn-sm btn-danger-ghost",
          text: t("common.cancel"),
          onClick: () => runSafe(async () => {
            const cancelled = await invoke("cancel_model_download", { modelId: m.id });
            delete state.downloadProgress[m.id];
            if (cancelled) {
              showToast(t("models.cancelling", { id: m.id }));
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
            text: t("models.useModel"),
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
              showToast(t("models.switchedToast", { id: m.id }));
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
            "aria-label": t("models.deleteAria", { name: m.display_name }),
            onClick: () => runSafe(async () => {
              const ok = await confirmAction({
                title: t("models.deleteTitle", { name: m.display_name }),
                message: t("models.deleteMessage"),
                confirmLabel: t("models.deleteConfirm"),
                destructive: true,
              });
              if (!ok) return;
              state.models = await invoke("delete_local_model", { modelId: m.id });
              showToast(t("models.deletedToast", { id: m.id }));
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
              file: t("models.connecting"),
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
              showToast(t("models.downloadedToast", { name: m.display_name }));
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
        }, [icon("ic-download", "ic ic-sm"), el("span", { text: t("models.download") })]),
      );
    }

    return el("div", { className: "panel-row" }, [copy, controls]);
  });

  stack.append(
    el("section", { className: "section" }, [
      el("h2", { className: "section-title", text: t("models.catalogTitle") }),
      el("p", { className: "section-desc", text: t("models.catalogDesc") }),
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
      showToast(t("prompts.savedToast"));
      render();
    } else {
      state.dirtyPrompts = true;
      showToast(t("prompts.savedPartialToast"));
    }
  });
}

function renderPromptsView() {
  const data = state.prompts;
  if (!data) return el("p", { className: "muted", text: t("prompts.loading") });

  const currentCtx = data.contexts.find((c) => c.key === state.activePromptTab) || data.contexts[0];
  const currentLoc = localizePromptContext(currentCtx);
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
        "aria-label": t("prompts.contextsAria"),
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
        const loc = localizePromptContext(ctx);
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
          el("span", { text: loc.label }),
          custom ? el("span", { className: "badge badge-warning", text: t("common.custom") }) : null,
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
            el("div", { className: "row-title", text: currentLoc.label }),
            isModified ? el("button", {
              type: "button",
              className: "btn btn-sm btn-ghost",
              text: t("prompts.revertSection"),
              onClick: () => {
                state.promptDrafts[currentCtx.key] = defaultVal;
                state.promptsRev += 1;
                state.dirtyPrompts = true;
                render();
              },
            }) : el("span", { className: "badge badge-neutral", text: t("common.default") }),
          ]),
          el("p", { className: "section-desc", text: currentLoc.description }),
          el("textarea", {
            className: "textarea",
            id: "prompt-editor",
            "aria-label": t("prompts.editorAria", { label: currentLoc.label }),
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
      showToast(t("vocab.savedToast", { count: state.vocabKeywords.length }));
      render();
    } else {
      state.dirtyVocab = true;
      showToast(t("vocab.savedPartialToast"));
    }
  });
}

function renderVocabularyView() {
  const data = state.vocabulary;
  if (!data) return el("p", { className: "muted", text: t("vocab.loading") });

  const countBadge = el("span", { className: "badge tabular" });
  const chipsHost = el("div", { className: "panel-row stack" });

  const refreshVocabDom = () => {
    const count = state.vocabKeywords.length;
    const overLocal = count > data.local_limit;
    const overDeepgram = count > data.deepgram_limit;
    countBadge.className = `badge tabular ${overDeepgram ? "badge-error" : overLocal ? "badge-warning" : "badge-neutral"}`;
    countBadge.textContent = t("vocab.count", {
      count,
      local: data.local_limit,
      deepgram: data.deepgram_limit,
    });

    if (count === 0) {
      chipsHost.replaceChildren(
        el("div", { className: "empty-state" }, [
          icon("ic-book"),
          el("h3", { text: t("vocab.emptyTitle") }),
          el("p", { text: t("vocab.emptyDesc") }),
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
              "aria-label": t("vocab.removeAria", { term: kw }),
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
    "aria-label": t("vocab.inputAria"),
    placeholder: t("vocab.inputPlaceholder"),
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
        el("h2", { className: "section-title", text: t("vocab.sectionTitle") }),
        countBadge,
      ]),
      el("p", { className: "section-desc", text: t("vocab.sectionDesc") }),
      el("div", { className: "panel" }, [
        el("div", { className: "panel-row" }, [
          addInput,
          el("button", {
            type: "button",
            className: "btn",
            onClick: addTermsFromInput,
          }, [icon("ic-plus", "ic ic-sm"), el("span", { text: t("common.add") })]),
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
          el("h3", { text: q ? t("history.emptyMatchTitle") : t("history.emptyTitle") }),
          el("p", {
            text: q ? t("history.emptyMatchDesc") : t("history.emptyDesc"),
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
            entry.refined ? el("span", { className: "badge badge-success", text: t("history.refinedBadge") }) : null,
          ]),
        ]),
        el("div", { className: "row-ctrl" }, [
          el("button", {
            type: "button",
            className: "icon-btn",
            "aria-label": t("history.copyAria"),
            title: t("common.copy"),
            onClick: () => runSafe(async () => {
              await invoke("copy_text_to_clipboard", { text: entry.text });
              showToast(t("history.copiedToast"));
            }),
          }, [icon("ic-copy", "ic ic-sm")]),
          el("button", {
            type: "button",
            className: "icon-btn",
            "aria-label": t("history.deleteAria"),
            title: t("common.delete"),
            onClick: () => runSafe(async () => {
              await invoke("delete_history_entry", { timestamp: entry.timestamp, text: entry.text });
              state.history = await invoke("get_history", { limit: 200 });
              showToast(t("history.deletedToast"));
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
          el("span", {
            className: "meta tabular",
            text: items.length === 1 ? t("history.entryOne") : t("history.entryMany", { count: items.length }),
          }),
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
    placeholder: t("history.searchPlaceholder"),
    "aria-label": t("history.searchAria"),
    onInput: (e) => {
      state.historyQuery = e.target.value;
      renderHistoryList(listContainer);
    },
  });

  const btnAll = el("button", {
    type: "button",
    className: `seg-btn${state.historyFilter === "all" ? " active" : ""}`,
    "aria-pressed": state.historyFilter === "all" ? "true" : "false",
    text: t("history.filterAll"),
    onClick: () => setFilter("all"),
  });
  const btnRefined = el("button", {
    type: "button",
    className: `seg-btn${state.historyFilter === "refined" ? " active" : ""}`,
    "aria-pressed": state.historyFilter === "refined" ? "true" : "false",
    text: t("history.filterRefined"),
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
      el("div", { className: "segmented", role: "group", "aria-label": t("history.filterAria") }, [btnAll, btnRefined]),
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
  const currentIdx = Math.max(0, ONBOARDING_STEP_IDS.indexOf(state.onboardingStep));

  stack.append(
    el("div", { className: "stepper", role: "list" }, ONBOARDING_STEP_IDS.map((stepId, i) =>
      el("span", {
        role: "listitem",
        className: `step-pill${i === currentIdx ? " active" : i < currentIdx ? " done" : ""}`,
        "aria-current": i === currentIdx ? "step" : null,
        text: t(`onboarding.step.${stepId}`),
      }),
    )),
  );

  const panel = el("div", { className: "panel" });
  const step = state.onboardingStep;

  if (step === "choose_goal") {
    const choices = ["fast", "private", "advanced"].map((id) => ({
      id,
      title: t(`onboarding.choice.${id}.title`),
      desc: t(`onboarding.choice.${id}.desc`),
    }));
    panel.append(
      el("div", { className: "panel-row stack" }, [
        el("h2", { className: "section-title", text: t("onboarding.goalTitle") }),
        el("p", { className: "section-desc", text: t("onboarding.goalDesc") }),
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
        el("span", {
          className: `badge ${granted ? "badge-success" : "badge-warning"}`,
          text: granted ? t("onboarding.perm.granted") : t("onboarding.perm.required"),
        }),
        !granted ? el("button", {
          type: "button",
          className: "btn btn-sm",
          text: t("onboarding.perm.request"),
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
          text: t("onboarding.perm.openSettings"),
          onClick: () => runSafe(() => invoke("open_privacy_settings", { kind })),
        }) : null,
      ]),
    ]);
    panel.append(
      permRow("microphone", t("onboarding.perm.mic.title"), t("onboarding.perm.mic.desc"), perms.microphone === "authorized"),
      permRow("accessibility", t("onboarding.perm.acc.title"), t("onboarding.perm.acc.desc"), perms.accessibility),
      permRow("input_monitoring", t("onboarding.perm.input.title"), t("onboarding.perm.input.desc"), perms.input_monitoring),
    );
  } else if (step === "hotkey") {
    const wizBadge = (res) => {
      if (!res || !res.message) return null;
      const cls = res.level === "error" ? "badge-error" : res.level === "warning" ? "badge-warning" : "badge-success";
      return el("span", { className: `badge ${cls}`, text: res.message });
    };
    panel.append(
      el("div", { className: "panel-row stack" }, [
        el("h2", { className: "section-title", text: t("onboarding.hotkeyTitle") }),
        el("div", { className: "field-grid" }, [
          el("div", { className: "field" }, [
            el("label", { for: "wiz-toggle", text: t("general.toggleHotkeyTitle") }),
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
            el("label", { for: "wiz-hold", text: t("general.holdHotkeyTitle") }),
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
        el("h2", { className: "section-title", text: t("onboarding.testTitle") }),
        el("p", {
          className: "section-desc",
          text: t("onboarding.testDesc"),
        }),
        el("div", { className: "row-ctrl" }, [
          isRecording
            ? el("button", {
                type: "button",
                className: "btn btn-danger",
                onClick: () => runSafe(() => invoke("toggle_dictation")),
              }, [icon("ic-square", "ic ic-sm"), el("span", { text: t("onboarding.testStop") })])
            : isProcessing
            ? el("button", {
                type: "button",
                className: "btn btn-danger",
                onClick: () => runSafe(() => invoke("cancel_dictation")),
              }, [icon("ic-x", "ic ic-sm"), el("span", { text: t("onboarding.testCancel") })])
            : el("button", {
                type: "button",
                className: "btn btn-primary",
                onClick: () => runSafe(() => invoke("start_test_dictation")),
              }, [icon("ic-mic", "ic ic-sm"), el("span", { text: t("onboarding.testStart") })]),
        ]),
        transcript ? el("pre", { className: "code-pre", text: transcript }) : null,
        testErr ? el("span", { className: "badge badge-error", text: testErr }) : null,
      ]),
    );
  } else {
    const toggleText = state.settings?.toggle_hotkey || t("common.notSet");
    const holdText = state.settings?.hold_hotkey || t("common.notSet");
    panel.append(
      el("div", { className: "panel-row stack" }, [
        el("h2", { className: "section-title", text: t("onboarding.readyTitle") }),
        el("p", {
          className: "section-desc",
          text: t("onboarding.readyDesc"),
        }),
        el("div", { className: "row-sub" }, [
          t("onboarding.toggleLabel"),
          el("span", { className: "kbd", text: toggleText }),
          t("onboarding.holdLabel"),
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
          text: t("common.back"),
          onClick: () => setOnboardingStepAction(ONBOARDING_STEP_IDS[currentIdx - 1]),
        })
      : el("span"),
    currentIdx < ONBOARDING_STEP_IDS.length - 1
      ? el("button", {
          type: "button",
          className: "btn btn-primary",
          text: t("common.continue"),
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
                showError(invalid.message || t("onboarding.hotkeyInvalid"));
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
            await setOnboardingStepAction(ONBOARDING_STEP_IDS[currentIdx + 1]);
          }),
        })
      : el("button", {
          type: "button",
          className: "btn btn-primary",
          text: t("onboarding.finish"),
          onClick: () => runSafe(async () => {
            await invoke("set_onboarding_state", { step: "done", choice: state.onboardingChoice });
            state.onboardingStep = "choose_goal";
            state.app = await invoke("get_app_state");
            showToast(t("onboarding.completeToast"));
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
  const status = localizeDaemonStatus(app);
  const perms = state.permissions || { microphone: "unknown", accessibility: false, input_monitoring: false };
  const isError = app.daemon_state === "error";
  const missingPerms = perms.microphone !== "authorized" || !perms.accessibility || !perms.input_monitoring;

  const verdictCls = isError ? "error" : missingPerms ? "warn" : "";
  const verdictText = isError
    ? t("diag.verdictError")
    : missingPerms
    ? t("diag.verdictPerms")
    : t("diag.verdictOk");

  stack.append(
    el("div", { className: "verdict" }, [
      el("span", { className: `verdict-icon ${verdictCls}` }, [
        icon(isError ? "ic-alert" : missingPerms ? "ic-shield" : "ic-check"),
      ]),
      el("div", {}, [
        el("h2", { className: "verdict-title", text: verdictText }),
        el("div", { className: "meta", text: status.hint }),
      ]),
    ]),
  );

  stack.append(
    el("div", { className: "panel" }, [
      el("div", { className: "metrics-grid" }, [
        el("div", { className: "metric-cell" }, [
          el("span", { className: "metric-label", text: t("diag.metricVersion") }),
          el("span", { className: "metric-value tabular", text: `v${app.version || "2.1.0"}` }),
        ]),
        el("div", { className: "metric-cell" }, [
          el("span", { className: "metric-label", text: t("diag.metricPlatform") }),
          el("span", { className: "metric-value", text: (app.platform || "desktop").toUpperCase() }),
        ]),
        el("div", { className: "metric-cell" }, [
          el("span", { className: "metric-label", text: t("diag.metricProvider") }),
          el("span", { className: "metric-value metric-text", text: app.mode || "deepgram" }),
        ]),
        el("div", { className: "metric-cell" }, [
          el("span", { className: "metric-label", text: t("diag.metricModels") }),
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
      el("h2", { className: "section-title", text: t("diag.updatesSection") }),
      el("div", { className: "panel" }, [
        el("div", { className: "panel-row" }, [
          el("div", { className: "row-copy" }, [
            el("div", { className: "row-title", text: t("diag.softwareUpdates") }),
            el("div", {
              className: "row-sub",
              text: upd
                ? upd.error || (upd.update_available
                    ? t("diag.updateAvailable", { latest: upd.latest_version, current: upd.current_version })
                    : t("diag.upToDate", { current: upd.current_version }))
                : t("diag.checkPrompt"),
            }),
          ]),
          el("div", { className: "row-ctrl" }, [
            upd?.update_available && upd.release_url
              ? el("button", {
                  type: "button",
                  className: "btn btn-sm btn-primary",
                  text: t("diag.downloadRelease"),
                  onClick: () => runSafe(() => invoke("open_release_url", { url: upd.release_url })),
                })
              : null,
            el("button", {
              type: "button",
              className: "btn btn-sm",
              text: t("diag.checkForUpdates"),
              onClick: () => runSafe(async () => {
                state.updateResult = await invoke("check_for_updates");
                if (state.updateResult.error) {
                  showError(state.updateResult.error);
                } else {
                  showToast(state.updateResult.update_available ? t("diag.toastUpdateAvailable") : t("diag.toastUpToDate"));
                }
                render();
              }),
            }),
          ]),
        ]),
        el("div", { className: "panel-row" }, [
          el("div", { className: "row-copy" }, [
            el("div", { className: "row-title", text: t("diag.configDirTitle") }),
            el("div", { className: "row-sub mono", text: app.config_dir || "~/.pulsescribe" }),
          ]),
          el("div", { className: "row-ctrl" }, [
            el("button", {
              type: "button",
              className: "btn btn-sm",
              text: t("common.open"),
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
              el("div", { className: "row-title", text: t("diag.exportedBundle") }),
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
    const { pref, resolved } = setUiLanguagePref(app.ui_language || "system");
    state.committedUiLanguagePref = pref;
    applyStaticTranslations();
    if (pref === "system" && app.resolved_ui_language && app.resolved_ui_language !== resolved) {
      enqueueUiLanguageSave({ pref: "system", resolved, silent: true });
    }
    applyThemePref(themePref);
    updateDaemonBadge();
    await navigate(app.initial_route || "general");
  });
}

bootstrap();
