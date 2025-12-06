/**
 * Whisper Go – Toggle Recording Command
 *
 * Systemweite Spracheingabe mit Toggle-Verhalten:
 * 1. Hotkey → Aufnahme startet (Python-Daemon im Hintergrund)
 * 2. Hotkey → Aufnahme stoppt, transkribiert, fügt Text ein
 *
 * Kommunikation mit Python erfolgt über:
 * - PID_FILE: Zeigt an, ob Aufnahme läuft
 * - TRANSCRIPT_FILE: Enthält das Transkript nach Erfolg
 * - ERROR_FILE: Enthält Fehlermeldung bei Problemen
 * - SIGUSR1: Signal zum Stoppen der Aufnahme
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

// =============================================================================
// Konstanten
// =============================================================================

/** IPC-Dateien für Kommunikation mit Python-Daemon */
const IPC_FILES = {
  pid: "/tmp/whisper_go.pid",
  transcript: "/tmp/whisper_go.transcript",
  error: "/tmp/whisper_go.error",
} as const;

/** Timeouts in Millisekunden */
const TIMEOUTS = {
  processStart: 2000, // Max. Wartezeit bis Daemon startet
  transcription: 60000, // Max. Wartezeit auf Transkription
  pollingInterval: 100, // Intervall für Datei-Polling
} as const;

// =============================================================================
// Types
// =============================================================================

interface Preferences {
  pythonPath: string;
  scriptPath: string;
  language: string;
  openaiApiKey: string;
}

/** Discriminated Union für Transkriptionsergebnis */
type TranscriptionResult =
  | { success: true; text: string }
  | { success: false; error: string }
  | null;

// =============================================================================
// Auto-Detection Funktionen
// =============================================================================

/** Bekannte Python-Installationspfade in Prioritätsreihenfolge */
const PYTHON_PATHS = [
  () => join(homedir(), ".pyenv/shims/python3"), // pyenv (bevorzugt)
  () => join(homedir(), ".pyenv/shims/python"),
  () => "/opt/homebrew/bin/python3", // Homebrew (Apple Silicon)
  () => "/usr/local/bin/python3", // Homebrew (Intel)
  () => "/usr/bin/python3", // System-Python
];

function findPythonPath(): string | null {
  for (const getPath of PYTHON_PATHS) {
    const path = getPath();
    if (existsSync(path)) return path;
  }
  return null;
}

/**
 * Findet transcribe.py via Symlink im assets-Ordner.
 * Der Symlink wird bei `npm install` automatisch erstellt.
 */
function findScriptPath(): string | null {
  if (!environment.assetsPath) return null;

  const scriptPath = join(environment.assetsPath, "transcribe.py");
  return existsSync(scriptPath) ? scriptPath : null;
}

// =============================================================================
// Hilfsfunktionen
// =============================================================================

/** Liest Datei, löscht sie, und gibt Inhalt zurück */
function readAndDelete(filePath: string): string | null {
  if (!existsSync(filePath)) return null;

  const content = readFileSync(filePath, "utf-8").trim();
  unlinkSync(filePath);
  return content;
}

/** Löscht Datei falls vorhanden (keine Exception wenn nicht) */
function deleteIfExists(filePath: string): void {
  if (existsSync(filePath)) unlinkSync(filePath);
}

/** Prüft ob Prozess mit gegebener PID läuft */
function isProcessAlive(pid: number): boolean {
  try {
    process.kill(pid, 0); // Signal 0 = nur prüfen, nicht killen
    return true;
  } catch {
    return false;
  }
}

/** Pausiert Ausführung für gegebene Millisekunden */
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// =============================================================================
// Validierung
// =============================================================================

function resolvePreferences(prefs: Preferences): Preferences {
  return {
    pythonPath: prefs.pythonPath || findPythonPath() || "",
    scriptPath: prefs.scriptPath || findScriptPath() || "",
    language: prefs.language,
    openaiApiKey: prefs.openaiApiKey || "",
  };
}

function validateConfig(prefs: Preferences): string | null {
  // Script prüfen
  if (!prefs.scriptPath) {
    return "Script-Pfad nicht konfiguriert";
  }
  if (!existsSync(prefs.scriptPath)) {
    return `Script nicht gefunden: ${prefs.scriptPath}`;
  }

  // Python prüfen
  if (!prefs.pythonPath) {
    return "Python-Pfad nicht konfiguriert";
  }
  const pythonCheck = spawnSync(prefs.pythonPath, ["--version"], {
    timeout: 5000,
  });
  if (pythonCheck.error || pythonCheck.status !== 0) {
    return `Python nicht gefunden: ${prefs.pythonPath}`;
  }

  return null; // Alles OK
}

// =============================================================================
// Polling für Transkriptionsergebnis
// =============================================================================

async function waitForTranscription(): Promise<TranscriptionResult> {
  const deadline = Date.now() + TIMEOUTS.transcription;

  while (Date.now() < deadline) {
    // Fehler hat Priorität (schnelles Feedback)
    const errorContent = readAndDelete(IPC_FILES.error);
    if (errorContent) {
      return { success: false, error: errorContent };
    }

    // Erfolg prüfen
    const transcriptContent = readAndDelete(IPC_FILES.transcript);
    if (transcriptContent) {
      return { success: true, text: transcriptContent };
    }

    await sleep(TIMEOUTS.pollingInterval);
  }

  return null; // Timeout
}

// =============================================================================
// Aufnahme starten
// =============================================================================

async function startRecording(prefs: Preferences): Promise<void> {
  await closeMainWindow();

  // Alte IPC-Dateien aufräumen (verhindert falsche Ergebnisse)
  deleteIfExists(IPC_FILES.error);
  deleteIfExists(IPC_FILES.transcript);

  // Python-Daemon starten
  const args = buildDaemonArgs(prefs);
  const env = buildEnvironment(prefs);

  const daemon = spawn(prefs.pythonPath, args, {
    detached: true, // Unabhängig von Raycast
    stdio: "ignore", // Keine Verbindung zu stdin/stdout
    env,
  });
  daemon.unref(); // Raycast muss nicht auf Beendigung warten

  // Warten bis Daemon bereit ist (PID-File erscheint)
  const started = await waitForDaemonStart();

  if (started) {
    await showHUD("🎤 Aufnahme läuft...");
  } else {
    const errorMsg = readAndDelete(IPC_FILES.error);
    await showHUD(`❌ ${errorMsg || "Aufnahme konnte nicht gestartet werden"}`);
  }
}

function buildDaemonArgs(prefs: Preferences): string[] {
  const args = [prefs.scriptPath, "--record-daemon"];
  if (prefs.language) {
    args.push("--language", prefs.language);
  }
  return args;
}

function buildEnvironment(prefs: Preferences): NodeJS.ProcessEnv {
  const env = { ...process.env };
  // API-Key aus Preference hat Vorrang vor .env
  if (prefs.openaiApiKey) {
    env.OPENAI_API_KEY = prefs.openaiApiKey;
  }
  return env;
}

async function waitForDaemonStart(): Promise<boolean> {
  const deadline = Date.now() + TIMEOUTS.processStart;

  while (Date.now() < deadline) {
    if (existsSync(IPC_FILES.pid)) return true;
    if (existsSync(IPC_FILES.error)) return false;
    await sleep(TIMEOUTS.pollingInterval);
  }

  return false;
}

// =============================================================================
// Aufnahme stoppen
// =============================================================================

async function stopRecording(): Promise<void> {
  await closeMainWindow();

  // PID lesen (mit Fehlerbehandlung für Race Conditions)
  const pid = readRecordingPid();
  if (!pid) {
    await showHUD("⚠️ Keine aktive Aufnahme gefunden");
    return;
  }

  // Aufnahme stoppen via Signal
  await showHUD("⏳ Transkribiere...");
  process.kill(pid, "SIGUSR1");

  // Auf Ergebnis warten und anzeigen
  const result = await waitForTranscription();
  await handleTranscriptionResult(result);
}

function readRecordingPid(): number | null {
  let pidStr: string;
  try {
    pidStr = readFileSync(IPC_FILES.pid, "utf-8").trim();
  } catch {
    return null; // Datei existiert nicht oder Lesefehler
  }

  const pid = parseInt(pidStr, 10);

  // Validierung: Muss positive Ganzzahl sein
  if (!Number.isInteger(pid) || pid <= 0) {
    deleteIfExists(IPC_FILES.pid);
    return null;
  }

  // Prüfen ob Prozess noch läuft
  if (!isProcessAlive(pid)) {
    deleteIfExists(IPC_FILES.pid); // Stale PID-File aufräumen
    return null;
  }

  return pid;
}

async function handleTranscriptionResult(
  result: TranscriptionResult,
): Promise<void> {
  if (result?.success) {
    await Clipboard.paste(result.text);
    await showHUD("✅ Eingefügt!");
  } else if (result && !result.success) {
    await showHUD(`❌ ${result.error}`);
  } else {
    await showHUD("❌ Transkription fehlgeschlagen (Timeout)");
  }
}

// =============================================================================
// Hauptfunktion (Entry Point)
// =============================================================================

export default async function Command(): Promise<void> {
  const rawPrefs = getPreferenceValues<Preferences>();
  const prefs = resolvePreferences(rawPrefs);

  const configError = validateConfig(prefs);
  if (configError) {
    await showHUD(`⚠️ ${configError}`);
    return;
  }

  // Toggle-Logik: PID-File existiert = Aufnahme läuft
  const isRecording = existsSync(IPC_FILES.pid);

  if (isRecording) {
    await stopRecording();
  } else {
    await startRecording(prefs);
  }
}
