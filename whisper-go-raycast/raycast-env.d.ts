/// <reference types="@raycast/api">

/* 🚧 🚧 🚧
 * This file is auto-generated from the extension's manifest.
 * Do not modify manually. Instead, update the `package.json` file.
 * 🚧 🚧 🚧 */

/* eslint-disable @typescript-eslint/ban-types */

type ExtensionPreferences = {
  /** Python Pfad - Pfad zur Python-Installation (z.B. /usr/bin/python3) */
  "pythonPath": string,
  /** Script Pfad - Pfad zu transcribe.py */
  "scriptPath": string,
  /** Sprache - Sprachcode für Transkription */
  "language": "de" | "en" | ""
}

/** Preferences accessible in all the extension's commands */
declare type Preferences = ExtensionPreferences

declare namespace Preferences {
  /** Preferences accessible in the `toggle-recording` command */
  export type ToggleRecording = ExtensionPreferences & {}
}

declare namespace Arguments {
  /** Arguments passed to the `toggle-recording` command */
  export type ToggleRecording = {}
}

