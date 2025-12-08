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

// Timing-Helper für Performance-Analyse (wird pro Command() neu gesetzt)
let commandStartTime = 0;
function logTiming(label: string): void {
  const elapsed = Date.now() - commandStartTime;
  console.log(`[whisper_go] +${elapsed}ms: ${label}`);
}

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
  logTiming("startRecording() called");

  // Fenster schließen - Overlay zeigt Status via overlay.py
  await closeMainWindow();
  logTiming("closeMainWindow() done");

  // Alte IPC-Dateien aufräumen
  deleteIfExists(IPC.error);
  deleteIfExists(IPC.transcript);

  // Daemon-Argumente
  const args = [prefs.scriptPath, "--record-daemon"];
  if (prefs.language) args.push("--language", prefs.language);

  // Environment mit optionalem API-Key
  const env = { ...process.env };
  if (prefs.openaiApiKey) env.OPENAI_API_KEY = prefs.openaiApiKey;

  logTiming("spawning daemon...");
  // Daemon starten (detached = unabhängig von Raycast)
  const daemon = spawn(prefs.pythonPath, args, {
    detached: true,
    stdio: "ignore",
    env,
  });
  daemon.unref();
  logTiming("daemon spawned");

  // Kurz prüfen ob Daemon erfolgreich gestartet (max 500ms)
  const deadline = Date.now() + 500;
  let pollCount = 0;
  while (Date.now() < deadline) {
    if (existsSync(IPC.pid)) {
      logTiming(`PID file found after ${pollCount} polls`);
      return;
    }
    if (existsSync(IPC.error)) break;
    await sleep(TIMEOUT.poll);
    pollCount++;
  }

  // Fehler nur wenn Error-File existiert
  const error = readAndDelete(IPC.error);
  if (error) {
    logTiming("daemon start error");
    await showToast({
      style: Toast.Style.Failure,
      title: "Aufnahme fehlgeschlagen",
      message: error,
    });
  }
  // Kein Fehler-Toast wenn nur Timeout - Daemon läuft wahrscheinlich
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

  // Signal zum Stoppen senden - Overlay zeigt Status
  process.kill(pid, "SIGUSR1");

  // Auf Ergebnis warten (ohne Toast - Overlay zeigt "Transcribing...")
  const deadline = Date.now() + TIMEOUT.transcription;
  while (Date.now() < deadline) {
    const error = readAndDelete(IPC.error);
    if (error) {
      // Nur bei Fehlern Toast zeigen
      await showToast({
        style: Toast.Style.Failure,
        title: "Transkription fehlgeschlagen",
        message: error,
      });
      return;
    }

    const text = readAndDelete(IPC.transcript);
    if (text !== null) {
      if (!text) {
        // Leeres Transkript = nichts gesprochen
        await showToast({
          style: Toast.Style.Failure,
          title: "Keine Sprache erkannt",
          message: "Aufnahme war zu kurz oder leise",
        });
        return;
      }
      // Text einfügen - kein Toast nötig, Ergebnis ist sichtbar
      await Clipboard.paste(text);
      return;
    }

    await sleep(TIMEOUT.poll);
  }

  // Timeout
  await showToast({
    style: Toast.Style.Failure,
    title: "Timeout",
    message: "Transkription dauerte zu lange",
  });
}

// --- Entry Point ---

export default async function Command(): Promise<void> {
  const commandStart = Date.now();
  commandStartTime = commandStart; // Timing-Baseline für logTiming() setzen
  console.log(`[whisper_go] === Command() START ===`);

  const prefs = resolvePreferences(getPreferenceValues<Preferences>());
  logTiming("preferences resolved");

  // Prüfe ob Aufnahme läuft: PID-Datei muss existieren UND Prozess muss leben
  let isRecording = false;
  if (existsSync(IPC.pid)) {
    const pidStr = readFileSync(IPC.pid, "utf-8").trim();
    const pid = parseInt(pidStr, 10);
    isRecording = Number.isInteger(pid) && pid > 0 && isProcessAlive(pid);
    if (!isRecording) deleteIfExists(IPC.pid);
  }
  logTiming(`isRecording: ${isRecording}`);

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

  console.log(
    `[whisper_go] === Command() END (${Date.now() - commandStart}ms total) ===`,
  );
}
