# Gemma Translator

> **Raspberry Pi 5 + Whisplay Plus + PiSugar 3 Plus** · [Hardware and software installation guide](WHISPLAY-PLUS-SETUP.md)

<img width="960" height="540" src="https://storage.googleapis.com/experiments-uploads/gemma-translator/gemma-translator.gif" />

This repo was built with the assistance of [Google Antigravity](https://antigravity.google/) and includes code to run an on-device, fully offline voice translator powered by [Gemma 4](https://ai.google.dev/gemma/docs/core) and [LiteRT-LM](https://github.com/google-ai-edge/LiteRT-lm). This project features a web frontend optimized for small handheld displays (e.g., 480x320) and a Python API server (`http.server`) that communicates with Gemma. Text-to-speech is powered by [Moonshine](https://github.com/moonshine-ai/moonshine).

https://github.com/user-attachments/assets/343072ce-dc78-44a7-a783-99312845cabe

## Features

- **On-Device Inference**: Uses LiteRT-LM to run the `gemma4-e2b` model entirely locally. No internet required after setup.
- **Voice Interface**: Captures microphone audio, processes it, and sends it to the local model.
- **Optimized UI**: Retro-terminal styling custom-built for small hardware screens (like Raspberry Pi displays).
- **Unified Startup**: One script to launch the LLM server, the Python API, and the React frontend.

## Prerequisites

- Python 3.12+ (Raspberry Pi OS Trixie provides Python 3.13)
- Node.js 18+ (20 LTS recommended) & npm — installed automatically by `deploy-pi.sh` on Raspberry Pi OS / Debian
- Linux or macOS

## Required Hardware

- **Compute**: Raspberry Pi 5 with 8GB RAM
- **Audio Input**: Microphone or USB audio capture interface
- **Audio Output**: Speaker or headphone output device
- **Display**: Display monitor or touchscreen (e.g., 480x320 kiosk display)

<img width="3024" height="1672" src="https://storage.googleapis.com/experiments-uploads/gemma-translator/gemma-translator-cad.gif" />

## Setup Instructions

1. **Make Scripts Executable**
   Ensure the setup, download, start, and deployment scripts have execute permissions:
   ```bash
   chmod +x setup.sh download_model.sh start.sh deploy-pi.sh
   ```

2. **Install Dependencies**
   Run the setup script to create a Python virtual environment (`venv`) and install all required packages:
   ```bash
   ./setup.sh
   ```

3. **Download the Model**
   Run the model downloader script to fetch the `gemma4-e2b` model from Hugging Face and import it into LiteRT-LM:
   ```bash
   ./download_model.sh
   ```

## Running the Application

Start all services (LiteRT-LM, the Python API server, and the Vite Web UI) in development mode:
```bash
./start.sh
```

To run in production mode (skipping Vite dev server and serving compiled UI assets from `frontend/dist/` via `backend/server.py` on port 3000):
```bash
./start.sh --prod
```

The application will be accessible at:
- **Web UI (Dev)**: `http://localhost:5173`
- **Web UI (Prod) / API server**: `http://localhost:3000`
- **LiteRT-LM**: `http://localhost:9379`

### Warm-model and streaming behavior

At startup, `backend/litert_session_server.py` loads Gemma and creates one
persistent translator conversation with its system prompt already in the KV
cache. The browser receives token SSE events through the streaming `/proxy`,
updates the translation on every delta, and sends completed clauses to the TTS
queue while the rest of the response is still being generated. The Python API
also loads the default Chinese and English STT/TTS models before it begins
accepting requests.

The main Raspberry Pi tuning variables are:

```bash
TRANSLATOR_PRELOAD_LANGUAGES=zh,en
TRANSLATOR_SPEECH_MODEL_CACHE_SIZE=2
LITERT_MAX_NUM_TOKENS=4096
LITERT_RESET_AT_TOKENS=3200
```

The persistent conversation is recreated automatically at the reset threshold
to prevent an unbounded KV cache.

### Accessing the UI from another device

The Web UI listens on all network interfaces by default. Connect a phone,
tablet, or computer to the same local network as the translator, find the
translator's IP address, and open:

- **Production / appliance deployment:** `http://<translator-ip>:3000`
- **Development mode:** `http://<translator-ip>:5173`

For example, if the translator's address is `192.168.1.42`, open
`http://192.168.1.42:3000`. On Raspberry Pi OS, the address can be found with
`hostname -I`.

The bind address and ports can be overridden when needed:

```bash
TRANSLATOR_HOST=0.0.0.0 TRANSLATOR_PORT=3000 ./start.sh --prod
TRANSLATOR_WEB_PORT=5173 ./start.sh
```

Set `TRANSLATOR_HOST=127.0.0.1` to disable LAN access. This feature is intended
for trusted local networks; it does not expose the service through a router or
the public internet. Remote browsers can view and control the UI, but microphone
capture may require HTTPS because of browser security rules.

## Raspberry Pi Appliance Deployment

To deploy as a permanent systemd kiosk service on a Raspberry Pi 5 (8GB):
```bash
./deploy-pi.sh
```
This automated script installs Debian audio/venv packages, sets up the Python environment, builds production UI assets, downloads the LiteRT model, registers the systemd unit from `deploy/gemma-translator.service`, and configures LXDE GUI autostart (`~/.config/lxsession/rpd-x/autostart`) to launch Chromium in kiosk mode pointing to `http://localhost:3000`.

For Whisplay Plus hardware, follow the [Raspberry Pi 5 + Whisplay Plus + PiSugar 3 Plus installation guide](WHISPLAY-PLUS-SETUP.md). It covers assembly, the Whisplay Plus driver, battery management, and deployment with `./deploy-whisplay-plus.sh`.

## Project Structure

- `frontend/` - React (Vite) web frontend (`index.html`, `src/`, styles, and Vite configuration).
- `backend/` - Python API server for Moonshine STT/TTS and the persistent LiteRT-LM translator service.
- `deploy/` - Parameterizable systemd service unit template (`gemma-translator.service`).
- `stl/` - STL files for 3D printing the hardware case.
- `setup.sh` - Automates Python virtual environment creation and dependency installation.
- `download_model.sh` - Fetches the required LiteRT model.
- `start.sh` - Multi-process launcher supporting `--prod` and development modes.
- `deploy-pi.sh` - One-command Raspberry Pi automated deployment script.

## Keyboard Shortcuts

The Gemma Translator supports **two keyboard modes**. Switch between them anytime from the **Settings panel → "Keyboard Mode"** dropdown. The choice is remembered across restarts (stored in the browser's `localStorage` under the key `keyboardMode`).

The app has two lanes (two people facing each other on the kiosk):
- **Lane 1 / Person 1** — the left/top lane.
- **Lane 2 / Person 2** — the right/bottom lane.

Each lane has a rotating language "revolver" and records speech, which is transcribed (Moonshine STT), translated (Gemma), and spoken back in the other lane's language (moonshine-voice TTS).

### Landscape Mode (default) — "active person"
One lane is the **active person** at a time. The active lane is framed with **corner brackets on all four corners**. You drive everything from a single set of keys and switch focus with Space.

| Key | Action | Description |
| :--- | :--- | :--- |
| **Spacebar** | Switch active person | Toggles the active lane (Person 1 ⇄ Person 2). Disabled while recording. |
| **Z** | Record (push-to-talk) | Hold to record the **active** person; release to transcribe & translate. |
| **← Left Arrow** | Previous language | Rotates the **active** person's language backward. |
| **→ Right Arrow** | Next language | Rotates the **active** person's language forward. |

Notes:
- The active lane shows four-corner brackets; while it is recording, the brackets invert to black along with the lane's color reversal.
- Best for one-handed / single-operator use.

### Vertical Mode — "two-hand" (original mapping)
Each lane has its **own dedicated keys** — there is no active-person concept and **no bracket highlight**. Both people can be controlled independently.

| Key | Action | Description |
| :--- | :--- | :--- |
| **Z** | Record — Person 1 (push-to-talk) | Hold to record Lane 1; release to transcribe & translate. |
| **X** | Record — Person 2 (push-to-talk) | Hold to record Lane 2; release to transcribe & translate. |
| **← Left Arrow** | Previous language — Person 1 | Rotates Lane 1's language backward. |
| **→ Right Arrow** | Next language — Person 1 | Rotates Lane 1's language forward. |
| **− Minus** (`_`) | Previous language — Person 2 | Rotates Lane 2's language backward. |
| **+ Plus** (`=`) | Next language — Person 2 | Rotates Lane 2's language forward. |

Notes:
- No corner-bracket selection highlight in this mode.
- Best for two operators, each handling their own side.

### Common behavior (both modes)
- **Input focus guard:** all shortcuts are ignored while focus is on a configuration field (`<input>`, `<textarea>`, or `<select>`) — e.g. when editing the API endpoint or settings.
- **Recording lock:** language rotation is blocked while a recording is in progress.
- **Keyboard-driven:** recording and language rotation are keyboard-only in the current build; on-screen touch controls are not enabled.

### Switching modes
Open **Settings (⚙)** → **Keyboard Mode** → choose **Landscape** or **Vertical**. The change takes effect immediately and persists on the device.

| Setting value | Mode |
| :--- | :--- |
| `landscape` | Active-person scheme (Space / Z / ← →) — default |
| `vertical` | Two-hand scheme (Z / X / ← → / − +) |

## Credits
Made by a small team at [Google Creative Lab](https://github.com/googlecreativelab):
- [Alan Yam](https://github.com/alanvww)
- [Shashwath Santosh](https://x.com/shashwth)
- [Dan Motzenbecker](https://github.com/dmotz)

## Disclaimer

This is not an officially supported Google product. This project is not
eligible for the [Google Open Source Software Vulnerability Rewards
Program](https://bughunters.google.com/open-source-security).
