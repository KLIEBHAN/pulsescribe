Kurzfassung:
Wir bauen zwei kleine Skripte:

1. **`transcribe_api.py`** → testet Whisper (und Co.) über die OpenAI‑API
2. **`transcribe_local.py`** → nutzt das **open‑source Whisper**‑Repo lokal auf deiner Maschine ([GitHub][1])

---

## 1. Whisper über die OpenAI‑API (Python)

### 1.1. Setup

**Python‑Lib installieren**

```bash
pip install --upgrade openai
```

**API‑Key setzen**

Unter Linux/macOS z.B.:

```bash
export OPENAI_API_KEY="dein_api_key"
```

Unter Windows (Powershell):

```powershell
setx OPENAI_API_KEY "dein_api_key"
```

### 1.2. Minimal-Skript: `transcribe_api.py`

Dieses Skript nimmt eine lokale Audiodatei (`audio.mp3`, `audio.wav`, …) und schickt sie an die Speech‑to‑Text‑Endpoint der OpenAI API. Aktuell sind z.B. die Modelle `gpt-4o-transcribe`, `gpt-4o-mini-transcribe` und `whisper-1` verfügbar. ([OpenAI Platform][2])

```python
from openai import OpenAI
import sys
from pathlib import Path

client = OpenAI()

def transcribe_file(path: str) -> None:
    audio_path = Path(path)
    if not audio_path.exists():
        print(f"Datei nicht gefunden: {audio_path}")
        return

    with audio_path.open("rb") as audio_file:
        # Für einfache Tests: gpt-4o-transcribe
        transcript = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=audio_file,
            # optional:
            # language="de",               # hilft bei deutscher Sprache
            # response_format="json",      # "text", "srt", "vtt" ...
            # temperature=0.0,
        )

    # transcript.text enthält das pure Transkript
    print("=== TRANSKRIPT ===")
    print(transcript.text)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python transcribe_api.py <audio_datei>")
        sys.exit(1)

    transcribe_file(sys.argv[1])
```

**Testen:**

```bash
python transcribe_api.py sample.wav
```

### 1.3. Bonus: Translation nach Englisch (`whisper-1`)

Wenn du z.B. deutsche Sprache automatisch nach Englisch übersetzen willst:

```python
from openai import OpenAI

client = OpenAI()

with open("german.m4a", "rb") as audio_file:
    translation = client.audio.translations.create(
        model="whisper-1",
        file=audio_file,
        # response_format="text"  # für reinen Text
    )

print(translation.text)
```

Die `translations`‑Route macht **immer Englisch** draus. ([OpenAI Platform][2])

---

## 2. Whisper lokal in Python nutzen (open‑source Modell)

Hier nutzen wir das offizielle **openai/whisper**‑Repo (PyTorch‑basiert). ([GitHub][1])

### 2.1. Installation

```bash
pip install -U openai-whisper
```

Zusätzlich brauchst du **ffmpeg** (für Audio‑Handling): ([GitHub][1])

- Debian/Ubuntu:

  ```bash
  sudo apt update && sudo apt install ffmpeg
  ```

- macOS mit Homebrew:

  ```bash
  brew install ffmpeg
  ```

- Windows (Beispiel Chocolatey):

  ```powershell
  choco install ffmpeg
  ```

Wenn du eine GPU hast (CUDA), nutzt whisper die automatisch, falls PyTorch richtig installiert ist.

### 2.2. Minimal-Skript: `transcribe_local.py`

```python
import sys
import whisper
from pathlib import Path

def transcribe_local(path: str, model_name: str = "turbo") -> None:
    audio_path = Path(path)
    if not audio_path.exists():
        print(f"Datei nicht gefunden: {audio_path}")
        return

    print(f"Lade Modell '{model_name}' ...")
    model = whisper.load_model(model_name)  # z.B. "tiny", "base", "small", "medium", "large", "turbo"

    print(f"Transkribiere {audio_path} ...")
    result = model.transcribe(str(audio_path))
    # result["text"] enthält das Transkript
    print("=== TRANSKRIPT ===")
    print(result["text"])

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python transcribe_local.py <audio_datei> [modellname]")
        print("Beispiel: python transcribe_local.py sample.wav turbo")
        sys.exit(1)

    audio_file = sys.argv[1]
    model_name = sys.argv[2] if len(sys.argv) >= 3 else "turbo"
    transcribe_local(audio_file, model_name)
```

Die verwendeten Modelnamen und ihre Größen stehen im Whisper‑README (z.B. `tiny`, `base`, `small`, `medium`, `large`, `turbo`, jeweils mit Speed/VRAM‑Tradeoff). ([GitHub][1])

**Testen:**

```bash
python transcribe_local.py sample.wav
# oder langsamer aber genauer:
python transcribe_local.py sample.wav large
```

### 2.3. CLI direkt ohne Python-Skript

Whisper bringt auch ein eigenes CLI mit: ([GitHub][1])

```bash
whisper audio.wav --model turbo
```

Weitere Optionen siehst du mit:

```bash
whisper --help
```

---

## 3. Wann API, wann lokal?

Kurz:

- **API**

  - Kein Setup‑Stress mit GPU / PyTorch
  - Modelle wie `gpt-4o-transcribe(-diarize)` mit Zusatzfeatures (Diarization, Logprobs etc.) ([OpenAI Platform][2])
    – Kosten pro Minute Audio, Internet nötig

- **Lokal (openai/whisper)**

  - Voll offline, volle Kontrolle
  - Sehr gut für Batch‑Jobs / längere Aufnahmen
    – Setup dauert etwas, Performance hängt stark von deiner Hardware ab

---

Wenn du magst, können wir als nächsten Schritt aus einem der Skripte eine kleine **CLI-App mit Argumenten** oder sogar ein Mini‑Webinterface (FastAPI/Streamlit) machen und z.B. Mikrofon‑Input live transkribieren. Das Universum versteht sich schließlich nicht von allein. 🌌

[1]: https://github.com/openai/whisper "GitHub - openai/whisper: Robust Speech Recognition via Large-Scale Weak Supervision"
[2]: https://platform.openai.com/docs/api-reference/audio?utm_source=chatgpt.com "Audio | OpenAI API Reference"
