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
import { join, dirname } from "path";

const PID_FILE = "/tmp/whisper_go.pid";
const TRANSCRIPT_FILE = "/tmp/whisper_go.transcript";
const ERROR_FILE = "/tmp/whisper_go.error";

interface Preferences {
  pythonPath: string;
  scriptPath: string;
  language: string;
}

/**
 * Findet Python-Pfad automatisch (pyenv, homebrew, system).
 */
function findPythonPath(): string | null {
  const candidates = [
    join(homedir(), ".pyenv/shims/python3"),
    join(homedir(), ".pyenv/shims/python"),
    "/opt/homebrew/bin/python3",
    "/usr/local/bin/python3",
    "/usr/bin/python3",
  ];

  for (const candidate of candidates) {
    if (existsSync(candidate)) {
      return candidate;
    }
  }
  return null;
}

/**
 * Findet transcribe.py relativ zum Extension-Ordner.
 */
function findScriptPath(): string | null {
  // Mehrere Strategien, da assetsPath je nach Dev/Prod unterschiedlich ist
  const candidates: string[] = [];

  // 1. Via assetsPath (Prod: /path/to/extension/assets)
  const assetsDir = environment.assetsPath;
  if (assetsDir) {
    const extensionDir = assetsDir.replace(/\/assets\/?$/, "");
    candidates.push(join(dirname(extensionDir), "transcribe.py"));
  }

  // 2. Typisches Dev-Setup: Extension in whisper-go-raycast/, Script in parent
  // environment.extensionPath könnte direkt verfügbar sein
  const extensionPath = (environment as { extensionPath?: string })
    .extensionPath;
  if (extensionPath) {
    candidates.push(join(dirname(extensionPath), "transcribe.py"));
  }

  for (const candidate of candidates) {
    if (existsSync(candidate)) {
      return candidate;
    }
  }
  return null;
}

/**
 * Resolved Preferences mit Auto-Detection für leere Werte.
 */
function resolvePreferences(prefs: Preferences): Preferences {
  return {
    pythonPath: prefs.pythonPath || findPythonPath() || "",
    scriptPath: prefs.scriptPath || findScriptPath() || "",
    language: prefs.language,
  };
}

/**
 * Liest und löscht die Error-Datei falls vorhanden.
 */
function readAndClearError(): string | null {
  if (existsSync(ERROR_FILE)) {
    const content = readFileSync(ERROR_FILE, "utf-8").trim();
    unlinkSync(ERROR_FILE);
    return content;
  }
  return null;
}

/**
 * Validiert die Konfiguration vor dem Start.
 */
function validateConfig(prefs: Preferences): string | null {
  if (!prefs.scriptPath) {
    return "Script-Pfad nicht konfiguriert";
  }
  if (!existsSync(prefs.scriptPath)) {
    return `Script nicht gefunden: ${prefs.scriptPath}`;
  }
  if (!prefs.pythonPath) {
    return "Python-Pfad nicht konfiguriert";
  }

  // Prüfe ob Python existiert und funktioniert
  const result = spawnSync(prefs.pythonPath, ["--version"], { timeout: 5000 });
  if (result.error || result.status !== 0) {
    return `Python nicht gefunden: ${prefs.pythonPath}`;
  }

  return null;
}

/**
 * Prüft ob ein Prozess mit der gegebenen PID existiert.
 */
function isProcessRunning(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/**
 * Wartet auf die Transcript-Datei (Polling).
 * Prüft auch ERROR_FILE für schnelleres Fehler-Feedback.
 */
async function waitForTranscript(
  maxWaitMs = 60000,
): Promise<{ transcript: string } | { error: string } | null> {
  const startTime = Date.now();

  while (Date.now() - startTime < maxWaitMs) {
    // Fehlerfall prüfen (schnelles Feedback)
    if (existsSync(ERROR_FILE)) {
      const error = readFileSync(ERROR_FILE, "utf-8").trim();
      unlinkSync(ERROR_FILE);
      return { error };
    }
    // Erfolgsfall
    if (existsSync(TRANSCRIPT_FILE)) {
      const transcript = readFileSync(TRANSCRIPT_FILE, "utf-8").trim();
      unlinkSync(TRANSCRIPT_FILE);
      return { transcript };
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }

  return null;
}

/**
 * Startet die Aufnahme im Hintergrund.
 */
async function startRecording(prefs: Preferences): Promise<void> {
  await closeMainWindow();

  // Alte Dateien aufräumen
  if (existsSync(ERROR_FILE)) unlinkSync(ERROR_FILE);
  if (existsSync(TRANSCRIPT_FILE)) unlinkSync(TRANSCRIPT_FILE);

  const args = [prefs.scriptPath, "--record-daemon"];
  if (prefs.language) {
    args.push("--language", prefs.language);
  }

  const child = spawn(prefs.pythonPath, args, {
    detached: true,
    stdio: "ignore",
    env: { ...process.env },
  });

  child.unref();

  // Warten bis PID-File geschrieben wurde (mit Timeout)
  const maxWait = 2000;
  const startTime = Date.now();

  while (Date.now() - startTime < maxWait) {
    if (existsSync(PID_FILE)) {
      await showHUD("🎤 Aufnahme läuft...");
      return;
    }
    // Prüfe ob Error-File geschrieben wurde
    const errorMsg = readAndClearError();
    if (errorMsg) {
      await showHUD(`❌ ${errorMsg}`);
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }

  await showHUD("❌ Aufnahme konnte nicht gestartet werden");
}

/**
 * Stoppt die Aufnahme, wartet auf Transkript und fügt ein.
 */
async function stopRecording(): Promise<void> {
  await closeMainWindow();

  // Race Condition: PID-File könnte zwischen Check und Read verschwinden
  let pidStr: string;
  try {
    pidStr = readFileSync(PID_FILE, "utf-8").trim();
  } catch {
    await showHUD("⚠️ Keine aktive Aufnahme gefunden");
    return;
  }

  const pid = parseInt(pidStr, 10);

  // PID validieren
  if (!Number.isInteger(pid) || pid <= 0) {
    unlinkSync(PID_FILE);
    await showHUD("⚠️ Ungültige Aufnahme-Information");
    return;
  }

  if (!isProcessRunning(pid)) {
    // Stale PID file – aufräumen
    unlinkSync(PID_FILE);
    await showHUD("⚠️ Keine aktive Aufnahme gefunden");
    return;
  }

  await showHUD("⏳ Transkribiere...");

  // SIGUSR1 senden um Aufnahme zu stoppen
  process.kill(pid, "SIGUSR1");

  // Auf Transcript oder Error warten
  const result = await waitForTranscript();

  if (result && "transcript" in result) {
    await Clipboard.paste(result.transcript);
    await showHUD("✅ Eingefügt!");
  } else if (result && "error" in result) {
    await showHUD(`❌ ${result.error}`);
  } else {
    await showHUD("❌ Transkription fehlgeschlagen (Timeout)");
  }
}

/**
 * Toggle: Startet oder stoppt die Aufnahme je nach Status.
 */
export default async function Command(): Promise<void> {
  const rawPrefs = getPreferenceValues<Preferences>();
  const prefs = resolvePreferences(rawPrefs);

  // Konfiguration validieren
  const configError = validateConfig(prefs);
  if (configError) {
    await showHUD(`⚠️ ${configError}`);
    return;
  }

  const isRecording = existsSync(PID_FILE);

  if (!isRecording) {
    await startRecording(prefs);
  } else {
    await stopRecording();
  }
}
