/**
 * Whisper Go – Toggle Recording Command
 *
 * Systemweite Spracheingabe mit Toggle-Verhalten:
 * 1. Hotkey → Aufnahme startet (Python-Daemon im Hintergrund)
 * 2. Hotkey → Aufnahme stoppt, transkribiert, fügt Text ein
 *
 * IPC mit Python-Daemon über Dateien:
 * - PID_FILE: Zeigt an ob Aufnahme läuft
 * - TRANSCRIPT_FILE: Ergebnis nach Erfolg
 * - ERROR_FILE: Fehlermeldung bei Problemen
 * - SIGUSR1: Signal zum Stoppen
 */

import {
  showHUD,
  showToast,
  Toast,
  Clipboard,
  getPreferenceValues,
  closeMainWindow,
  environment,
} from "@raycast/api";
import { spawn } from "child_process";
import { existsSync, readFileSync, unlinkSync } from "fs";
import { homedir } from "os";
import { join } from "path";

// --- Konstanten ---

const IPC = {
  pid: "/tmp/whisper_go.pid",
  transcript: "/tmp/whisper_go.transcript",
  error: "/tmp/whisper_go.error",
} as const;

const TIMEOUT = {
  daemonStart: 2000,
  transcription: 60000,
  poll: 100,
} as const;

// Bekannte Python-Pfade in Prioritätsreihenfolge
const PYTHON_CANDIDATES = [
  join(homedir(), ".pyenv/shims/python3"),
  join(homedir(), ".pyenv/shims/python"),
  "/opt/homebrew/bin/python3",
  "/usr/local/bin/python3",
  "/usr/bin/python3",
];

// --- Types ---

interface Preferences {
  pythonPath: string;
  scriptPath: string;
  language: string;
  openaiApiKey: string;
}

// --- Hilfsfunktionen ---

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function readAndDelete(path: string): string | null {
  if (!existsSync(path)) return null;
  const content = readFileSync(path, "utf-8").trim();
  unlinkSync(path);
  return content;
}

function deleteIfExists(path: string): void {
  if (existsSync(path)) unlinkSync(path);
}

function isProcessAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

// --- Auto-Detection ---

function resolvePreferences(raw: Preferences): Preferences {
  // Python: User-Preference oder erste existierende Candidate
  const pythonPath = raw.pythonPath || PYTHON_CANDIDATES.find(existsSync) || "";

  // Script: User-Preference oder via assetsPath (Symlink)
  let scriptPath = raw.scriptPath;
  if (!scriptPath && environment.assetsPath) {
    const candidate = join(environment.assetsPath, "transcribe.py");
    if (existsSync(candidate)) scriptPath = candidate;
  }

  return {
    pythonPath,
    scriptPath: scriptPath || "",
    language: raw.language,
    openaiApiKey: raw.openaiApiKey || "",
  };
}

// --- Validierung ---

function validateConfig(prefs: Preferences): string | null {
  if (!prefs.scriptPath) return "Script-Pfad nicht konfiguriert";
  if (!existsSync(prefs.scriptPath))
    return `Script nicht gefunden: ${prefs.scriptPath}`;
  if (!prefs.pythonPath) return "Python-Pfad nicht konfiguriert";
  if (!existsSync(prefs.pythonPath))
    return `Python nicht gefunden: ${prefs.pythonPath}`;
  return null;
}

// --- Aufnahme starten ---

async function startRecording(prefs: Preferences): Promise<void> {
  await closeMainWindow();

  // Alte IPC-Dateien aufräumen
  deleteIfExists(IPC.error);
  deleteIfExists(IPC.transcript);

  // Daemon-Argumente
  const args = [prefs.scriptPath, "--record-daemon"];
  if (prefs.language) args.push("--language", prefs.language);

  // Environment mit optionalem API-Key
  const env = { ...process.env };
  if (prefs.openaiApiKey) env.OPENAI_API_KEY = prefs.openaiApiKey;

  // Daemon starten (detached = unabhängig von Raycast)
  const daemon = spawn(prefs.pythonPath, args, {
    detached: true,
    stdio: "ignore",
    env,
  });
  daemon.unref();

  // Warten bis Daemon bereit (PID-File erscheint)
  const deadline = Date.now() + TIMEOUT.daemonStart;
  while (Date.now() < deadline) {
    if (existsSync(IPC.pid)) {
      // HUD statt Toast: bleibt nach Command-Ende sichtbar
      await showHUD("🎤 Aufnahme läuft...");
      return;
    }
    if (existsSync(IPC.error)) break;
    await sleep(TIMEOUT.poll);
  }

  const error = readAndDelete(IPC.error);
  await showToast({
    style: Toast.Style.Failure,
    title: "Aufnahme fehlgeschlagen",
    message: error || "Daemon konnte nicht gestartet werden",
  });
}

// --- Aufnahme stoppen ---

async function stopRecording(): Promise<void> {
  await closeMainWindow();

  // PID lesen und validieren
  let pidStr: string;
  try {
    pidStr = readFileSync(IPC.pid, "utf-8").trim();
  } catch {
    await showToast({
      style: Toast.Style.Failure,
      title: "Keine aktive Aufnahme",
      message: "Starte zuerst eine Aufnahme",
    });
    return;
  }

  const pid = parseInt(pidStr, 10);
  if (!Number.isInteger(pid) || pid <= 0 || !isProcessAlive(pid)) {
    deleteIfExists(IPC.pid);
    await showToast({
      style: Toast.Style.Failure,
      title: "Keine aktive Aufnahme",
      message: "Aufnahme wurde bereits beendet",
    });
    return;
  }

  // Animierter Toast während Transkription
  const toast = await showToast({
    style: Toast.Style.Animated,
    title: "Transkribiere",
    message: "Verarbeite Audio...",
  });

  process.kill(pid, "SIGUSR1");

  const deadline = Date.now() + TIMEOUT.transcription;
  while (Date.now() < deadline) {
    const error = readAndDelete(IPC.error);
    if (error) {
      toast.style = Toast.Style.Failure;
      toast.title = "Transkription fehlgeschlagen";
      toast.message = error;
      await sleep(3000);
      await toast.hide();
      return;
    }

    const text = readAndDelete(IPC.transcript);
    if (text !== null) {
      // Leeres Transkript = nichts gesprochen
      if (!text) {
        toast.style = Toast.Style.Success;
        toast.title = "⚠️ Keine Sprache erkannt";
        toast.message = "Aufnahme beendet";
        await sleep(1500);
        await toast.hide();
        return;
      }
      await Clipboard.paste(text);
      toast.style = Toast.Style.Success;
      toast.title = "Text eingefügt";
      toast.message = text.length > 50 ? `${text.slice(0, 50)}...` : text;
      await sleep(2000);
      await toast.hide();
      return;
    }

    await sleep(TIMEOUT.poll);
  }

  toast.style = Toast.Style.Failure;
  toast.title = "Timeout";
  toast.message = "Transkription dauerte zu lange";
  await sleep(3000);
  await toast.hide();
}

// --- Entry Point ---

export default async function Command(): Promise<void> {
  const prefs = resolvePreferences(getPreferenceValues<Preferences>());

  // Prüfe ob Aufnahme läuft: PID-Datei muss existieren UND Prozess muss leben
  let isRecording = false;
  if (existsSync(IPC.pid)) {
    const pidStr = readFileSync(IPC.pid, "utf-8").trim();
    const pid = parseInt(pidStr, 10);
    isRecording = Number.isInteger(pid) && pid > 0 && isProcessAlive(pid);
    if (!isRecording) deleteIfExists(IPC.pid);
  }

  if (isRecording) {
    // Beim Stoppen: Keine Validierung nötig, Daemon läuft bereits
    await stopRecording();
  } else {
    // Beim Starten: Konfiguration validieren
    const error = validateConfig(prefs);
    if (error) {
      await showToast({
        style: Toast.Style.Failure,
        title: "Konfigurationsfehler",
        message: error,
      });
      return;
    }
    await startRecording(prefs);
  }
}
