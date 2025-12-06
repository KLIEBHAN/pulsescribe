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
  Clipboard,
  getPreferenceValues,
  closeMainWindow,
  environment,
} from "@raycast/api";
import { spawn, spawnSync } from "child_process";
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

function findFirst(candidates: string[]): string | null {
  return candidates.find(existsSync) ?? null;
}

// --- Auto-Detection ---

function findScriptPath(): string | null {
  if (!environment.assetsPath) return null;
  const path = join(environment.assetsPath, "transcribe.py");
  return existsSync(path) ? path : null;
}

function resolvePreferences(raw: Preferences): Preferences {
  return {
    pythonPath: raw.pythonPath || findFirst(PYTHON_CANDIDATES) || "",
    scriptPath: raw.scriptPath || findScriptPath() || "",
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

  const check = spawnSync(prefs.pythonPath, ["--version"], { timeout: 5000 });
  if (check.error || check.status !== 0) {
    return `Python nicht gefunden: ${prefs.pythonPath}`;
  }

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
      await showHUD("🎤 Aufnahme läuft...");
      return;
    }
    if (existsSync(IPC.error)) break;
    await sleep(TIMEOUT.poll);
  }

  const error = readAndDelete(IPC.error);
  await showHUD(`❌ ${error || "Aufnahme konnte nicht gestartet werden"}`);
}

// --- Aufnahme stoppen ---

async function stopRecording(): Promise<void> {
  await closeMainWindow();

  // PID lesen und validieren
  let pidStr: string;
  try {
    pidStr = readFileSync(IPC.pid, "utf-8").trim();
  } catch {
    await showHUD("⚠️ Keine aktive Aufnahme gefunden");
    return;
  }

  const pid = parseInt(pidStr, 10);
  if (!Number.isInteger(pid) || pid <= 0 || !isProcessAlive(pid)) {
    deleteIfExists(IPC.pid);
    await showHUD("⚠️ Keine aktive Aufnahme gefunden");
    return;
  }

  // Stoppen und auf Ergebnis warten
  await showHUD("⏳ Transkribiere...");
  process.kill(pid, "SIGUSR1");

  const deadline = Date.now() + TIMEOUT.transcription;
  while (Date.now() < deadline) {
    const error = readAndDelete(IPC.error);
    if (error) {
      await showHUD(`❌ ${error}`);
      return;
    }

    const text = readAndDelete(IPC.transcript);
    if (text) {
      await Clipboard.paste(text);
      await showHUD("✅ Eingefügt!");
      return;
    }

    await sleep(TIMEOUT.poll);
  }

  await showHUD("❌ Transkription fehlgeschlagen (Timeout)");
}

// --- Entry Point ---

export default async function Command(): Promise<void> {
  const prefs = resolvePreferences(getPreferenceValues<Preferences>());

  const error = validateConfig(prefs);
  if (error) {
    await showHUD(`⚠️ ${error}`);
    return;
  }

  if (existsSync(IPC.pid)) {
    await stopRecording();
  } else {
    await startRecording(prefs);
  }
}
