# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import http.server
import socketserver
import urllib.request
import urllib.error
import urllib.parse
import os
import base64
import io
import json
import numpy as np
import wave
import traceback
import socket
import ssl
import time
import subprocess
import signal

import threading
from contextlib import contextmanager
from pathlib import Path
from collections import OrderedDict

# Multilingual STT via Moonshine.
# Language is fixed at recognizer construction, so we lazily build (and cache) one
# recognizer per language actually used.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SUPPORTED_STT_LANGS = {"en", "ar", "es", "ja", "zh", "ko"}
MAX_MODELS = max(2, int(os.environ.get("TRANSLATOR_SPEECH_MODEL_CACHE_SIZE", "2")))
_stt_recognizers = OrderedDict()  # language -> recognizer
# RLock (reentrant): handle_stt holds the lock across get_stt_recognizer() + inference,
# and get_stt_recognizer() re-acquires it on the same thread. A plain Lock() self-deadlocks.
_stt_lock = threading.RLock()

# Multilingual TTS via moonshine-voice (Kokoro / Piper backed). Language is fixed at
# TextToSpeech construction, so we lazily build (and cache) one engine per language used.
# Maps our UI language codes -> moonshine-voice language codes.
TTS_LANG_MAP = {
    "ar": "ar-msa",
    "en": "en-us",
    "es": "es-es",
    "ja": "ja-jp",
    "zh": "zh-hans",
    "ko": "ko-kr",
}
# Optional per-language voice override (moonshine-voice voice IDs). Languages not
# listed here use moonshine's default voice for that language.
TTS_VOICE_MAP = {
    "zh": "kokoro_zf_xiaoxiao",  # 晓晓 — soft, gentle female Mandarin
}
_tts_engines = OrderedDict()  # our-lang-code -> TextToSpeech
# RLock (reentrant): handle_tts holds the lock across get_tts_engine() + synthesis,
# and get_tts_engine() re-acquires it on the same thread. A plain Lock() self-deadlocks.
_tts_lock = threading.RLock()
_language_prepare_lock = threading.Lock()
_language_prepare_status = {}
_download_patch_lock = threading.Lock()

def get_tts_engine(language="en"):
    if language not in TTS_LANG_MAP:
        language = "en"
    with _tts_lock:
        if language in _tts_engines:
            _tts_engines.move_to_end(language)
            return _tts_engines[language]
        from moonshine_voice import TextToSpeech
        moon_lang = TTS_LANG_MAP[language]
        voice = TTS_VOICE_MAP.get(language)
        print(f"[TTS] Loading moonshine-voice (lang={language} -> {moon_lang}, voice={voice or 'default'})...")
        if len(_tts_engines) >= MAX_MODELS:
            oldest_lang, oldest_engine = _tts_engines.popitem(last=False)
            print(f"[TTS] Evicting model for {oldest_lang}")
            del oldest_engine
        if voice:
            _tts_engines[language] = TextToSpeech(moon_lang, voice=voice)
        else:
            _tts_engines[language] = TextToSpeech(moon_lang)
        return _tts_engines[language]

def get_stt_recognizer(language="en"):
    if language not in SUPPORTED_STT_LANGS:
        language = "en"
    with _stt_lock:
        if language in _stt_recognizers:
            _stt_recognizers.move_to_end(language)
            return _stt_recognizers[language]
        from moonshine_voice import get_model_for_language, Transcriber
        print(f"[STT] Loading Moonshine STT (lang={language})...")
        if len(_stt_recognizers) >= MAX_MODELS:
            oldest_lang, oldest_recognizer = _stt_recognizers.popitem(last=False)
            print(f"[STT] Evicting model for {oldest_lang}")
            del oldest_recognizer
        model_path, model_arch = get_model_for_language(language)
        _stt_recognizers[language] = Transcriber(model_path=model_path, model_arch=model_arch)
        return _stt_recognizers[language]


LANGUAGE_LABELS = {
    "zh": "Chinese",
    "en": "English",
    "ar": "Arabic",
    "es": "Spanish",
    "ja": "Japanese",
    "ko": "Korean",
}
LANGUAGE_ORDER = ["zh", "en", "ar", "es", "ja", "ko"]


def prepare_language(language):
    if language not in SUPPORTED_STT_LANGS or language not in TTS_LANG_MAP:
        raise ValueError(f"Unsupported language: {language}")
    get_stt_recognizer(language)
    get_tts_engine(language)


def set_language_prepare_status(language, status, progress, stage, error=None):
    with _language_prepare_lock:
        _language_prepare_status[language] = {
            "status": status,
            "progress": progress,
            "stage": stage,
            "error": error,
            "updatedAt": time.time(),
        }


def tracked_download_file(language, range_start, range_end):
    def _download_file(
        url,
        dest,
        expected_sha256=None,
        resume=True,
        show_progress=True,
        timeout=30,
    ):
        import platform
        import requests
        from filelock import FileLock
        from moonshine_voice.download_file import hash_file

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        temp_file = dest.with_suffix(dest.suffix + ".partial")
        lock_file = dest.with_suffix(dest.suffix + ".lock")
        filename = dest.name
        last_emit = 0.0
        last_percent = -1

        def emit(downloaded, total):
            nonlocal last_emit, last_percent
            if total > 0:
                file_percent = min(100, max(0, int(downloaded * 100 / total)))
                progress = range_start + int((range_end - range_start) * file_percent / 100)
                stage = f"Downloading {filename} {file_percent}%"
            else:
                progress = range_start
                stage = f"Downloading {filename}"

            now = time.time()
            if progress != last_percent or now - last_emit >= 0.4:
                last_percent = progress
                last_emit = now
                set_language_prepare_status(language, "preparing", progress, stage)

        with FileLock(lock_file):
            if dest.exists():
                if expected_sha256 is None or hash_file(dest) == expected_sha256:
                    set_language_prepare_status(
                        language,
                        "preparing",
                        range_end,
                        f"{filename} already downloaded",
                    )
                    return dest
                dest.unlink()

            initial_size = 0
            headers = {}
            if resume and temp_file.exists():
                initial_size = temp_file.stat().st_size
                headers["Range"] = f"bytes={initial_size}-"

            response = requests.get(url, headers=headers, stream=True, timeout=timeout)
            if response.status_code == 416:
                temp_file.unlink(missing_ok=True)
                initial_size = 0
                response = requests.get(url, stream=True, timeout=timeout)
            response.raise_for_status()

            if response.status_code == 206:
                content_range = response.headers.get("Content-Range", "")
                if "/" in content_range:
                    total_size = int(content_range.split("/")[-1])
                else:
                    total_size = initial_size + int(response.headers.get("Content-Length", 0))
            else:
                total_size = int(response.headers.get("Content-Length", 0))
                initial_size = 0
                temp_file.unlink(missing_ok=True)

            emit(initial_size, total_size)
            downloaded = initial_size
            mode = "ab" if initial_size > 0 else "wb"
            with open(temp_file, mode) as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    emit(downloaded, total_size)

            if expected_sha256:
                actual_hash = hash_file(temp_file)
                if actual_hash != expected_sha256:
                    temp_file.unlink()
                    raise ValueError(
                        f"SHA256 mismatch for {dest.name}: "
                        f"expected {expected_sha256}, got {actual_hash}"
                    )

            temp_file.rename(dest)
            if platform.system() != "Windows":
                lock_file.unlink(missing_ok=True)
        set_language_prepare_status(language, "preparing", range_end, f"{filename} downloaded")
        return dest

    return _download_file


@contextmanager
def moonshine_download_progress(language, range_start, range_end):
    with _download_patch_lock:
        import moonshine_voice.download as moon_download
        import moonshine_voice.download_file as moon_download_file

        tracked = tracked_download_file(language, range_start, range_end)
        original_model_download_file = moon_download.download_file
        original_module_download_file = moon_download_file.download_file
        moon_download.download_file = tracked
        moon_download_file.download_file = tracked
        try:
            yield
        finally:
            moon_download.download_file = original_model_download_file
            moon_download_file.download_file = original_module_download_file


def prepare_languages_background(languages):
    for language in languages:
        try:
            set_language_prepare_status(language, "preparing", 20, "Preparing speech recognition")
            with moonshine_download_progress(language, 20, 55):
                get_stt_recognizer(language)
            set_language_prepare_status(language, "preparing", 55, "Speech recognition ready")
            set_language_prepare_status(language, "preparing", 70, "Preparing speech output")
            with moonshine_download_progress(language, 70, 92):
                get_tts_engine(language)
            set_language_prepare_status(language, "preparing", 92, "Finalizing")
            set_language_prepare_status(language, "ready", 100, "Ready")
        except Exception as exc:
            traceback.print_exc()
            set_language_prepare_status(language, "error", 0, "Failed", str(exc))


def language_status_payload():
    with _language_prepare_lock:
        statuses = dict(_language_prepare_status)

    languages = []
    for code in LANGUAGE_ORDER:
        languages.append({
            "code": code,
            "name": LANGUAGE_LABELS[code],
            "stt": code in SUPPORTED_STT_LANGS,
            "tts": code in TTS_LANG_MAP,
            "status": statuses.get(code, {
                "status": "not-installed",
                "progress": 0,
                "stage": "Not installed",
                "error": None,
                "updatedAt": None,
            }),
        })
    return {"languages": languages}


HOST = os.environ.get("TRANSLATOR_HOST", "0.0.0.0")
PORT = int(os.environ.get("TRANSLATOR_PORT", "3000"))


def is_local_client(client_address):
    """Return True for loopback clients, including IPv4-mapped IPv6."""
    client_ip = client_address[0]
    return client_ip in ('127.0.0.1', '::1', 'localhost', '::ffff:127.0.0.1')


class ProxyHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    def end_headers(self):
        # Add CORS headers
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS, PUT, DELETE')
        self.send_header('Access-Control-Allow-Headers', 'X-Requested-With, Content-Type, x-target-url, authorization')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def handle_proxy(self):
        # Parse query parameter "url"
        parsed_path = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed_path.query)
        target_url = query.get('url', [None])[0]

        if not target_url:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'Error: Missing "url" query parameter.')
            return

        # Restrict target URL to local LLM endpoint (http/https localhost/127.0.0.1)
        parsed_target = urllib.parse.urlparse(target_url)
        if parsed_target.scheme not in ('http', 'https') or parsed_target.hostname not in ('localhost', '127.0.0.1') or parsed_target.port not in (9379, None):
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b'Forbidden: Proxy target must be localhost:9379')
            return

        print(f"[Proxy] Routing {self.command} request to: {target_url}")
        
        # Read request body if method is POST/PUT/PATCH
        body = None
        if self.command in ['POST', 'PUT', 'PATCH']:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)

        # Build request to target url
        req = urllib.request.Request(
            target_url,
            data=body,
            method=self.command
        )

        # Forward headers (Content-Type, Authorization, etc.)
        for key, val in self.headers.items():
            if key.lower() not in ['host', 'connection', 'content-length', 'x-target-url']:
                req.add_header(key, val)

        response_started = False
        try:
            with urllib.request.urlopen(req, timeout=300) as response:
                self.send_response(response.status)
                # Forward response headers
                for key, val in response.headers.items():
                    if key.lower() not in ['content-length', 'connection', 'transfer-encoding']:
                        self.send_header(key, val)
                self.end_headers()
                response_started = True
                # read1 returns currently available bytes instead of waiting for
                # the full response. This preserves LiteRT-LM SSE token timing.
                read_chunk = getattr(response, 'read1', response.read)
                while True:
                    res_body = read_chunk(4096)
                    if not res_body:
                        break
                    self.wfile.write(res_body)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            print("[Proxy] Streaming client disconnected")
        except urllib.error.HTTPError as e:
            print(f"[Proxy Error] HTTP Error {e.code}: {e.reason}")
            try:
                res_body = e.read()
            except Exception:
                res_body = str(e).encode('utf-8')
            self.send_response(e.code)
            self.end_headers()
            self.wfile.write(res_body)
        except Exception as e:
            print(f"[Proxy Error] Exception: {e}")
            if response_started:
                self.close_connection = True
            else:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode('utf-8'))

    def handle_tts(self):
        parsed_path = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed_path.query)
        text = query.get('text', [None])[0]
        lang = query.get('lang', ['en'])[0]

        if not text:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'Error: Missing "text" parameter.')
            return

        print(f"[TTS] Synthesizing with moonshine-voice: {text[:50]}... (lang: {lang})")

        try:
            with _tts_lock:
                engine = get_tts_engine(lang)
                audio, sample_rate = engine.synthesize(text)

            # moonshine-voice returns mono float samples in [-1, 1]; encode to 16-bit PCM WAV.
            samples = np.asarray(audio, dtype=np.float32)
            samples = np.clip(samples, -1.0, 1.0)
            pcm16 = (samples * 32767.0).astype('<i2')

            with io.BytesIO() as buf:
                with wave.open(buf, 'wb') as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(int(sample_rate))
                    wf.writeframes(pcm16.tobytes())
                wav_bytes = buf.getvalue()

            self.send_response(200)
            self.send_header('Content-Type', 'audio/wav')
            self.send_header('Content-Length', str(len(wav_bytes)))
            self.end_headers()
            self.wfile.write(wav_bytes)
        except Exception as e:
            traceback.print_exc()
            print(f"[TTS Error] Exception: {e}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode('utf-8'))

    def handle_stt(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            
            if not body:
                raise ValueError("No body data")
                
            data = json.loads(body.decode('utf-8'))
            audio_b64 = data.get('audio_base64')
            if not audio_b64:
                raise ValueError("Missing audio_base64 parameter")

            language = data.get('language', 'en')
            raw_data = base64.b64decode(audio_b64)
            
            # The browser sends a raw Float32Array buffer
            audio_np = np.frombuffer(raw_data, dtype=np.float32)

            with _stt_lock:
                recognizer = get_stt_recognizer(language)
                transcript = recognizer.transcribe_without_streaming(audio_np, 16000)
            text = " ".join([line.text for line in transcript.lines])
            print(f"[STT] Transcribed: {text}")

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"text": text}).encode('utf-8'))
        except Exception as e:
            traceback.print_exc()
            print(f"[STT Error] Exception: {e}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode('utf-8'))

    def handle_volume(self):
        if not is_local_client(self.client_address):
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b'Forbidden: Volume control is only accessible locally')
            return

        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 0:
                body = self.rfile.read(content_length)
                data = json.loads(body.decode('utf-8')) if body else {}
                action = data.get('action')
            else:
                action = "get"
            
            import subprocess
            import re
            
            # PipeWire/wpctl needs XDG_RUNTIME_DIR to find its socket.
            # The server process may not have it set (e.g. when launched by systemd).
            env = os.environ.copy()
            if 'XDG_RUNTIME_DIR' not in env:
                uid = os.getuid()
                env['XDG_RUNTIME_DIR'] = f'/run/user/{uid}'
            
            def get_vol():
                # Try wpctl (PipeWire) first - outputs "Volume: 0.75"
                try:
                    out = subprocess.check_output(
                        ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
                        text=True, timeout=2, env=env
                    )
                    m = re.search(r'Volume:\s+([0-9.]+)', out)
                    if m:
                        return round(float(m.group(1)) * 100)
                except Exception:
                    pass
                # Try pactl (PulseAudio)
                try:
                    out = subprocess.check_output(
                        ["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
                        text=True, timeout=2, env=env
                    )
                    m = re.search(r'(\d+)%', out)
                    if m:
                        return int(m.group(1))
                except Exception:
                    pass
                # Try amixer
                try:
                    out = subprocess.check_output(
                        ["amixer", "sget", "Master"],
                        text=True, timeout=2, env=env
                    )
                    m = re.search(r'\[(\d+)%\]', out)
                    if m:
                        return int(m.group(1))
                except Exception:
                    pass
                return None

            def set_vol(direction):
                # Try wpctl first
                try:
                    arg = "5%+" if direction == "up" else "5%-"
                    subprocess.run(
                        ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", arg],
                        check=True, timeout=2, env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    return True
                except Exception:
                    pass
                # Try pactl
                try:
                    arg = "+5%" if direction == "up" else "-5%"
                    subprocess.run(
                        ["pactl", "set-sink-volume", "@DEFAULT_SINK@", arg],
                        check=True, timeout=2, env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    return True
                except Exception:
                    pass
                # Try amixer
                try:
                    arg = "5%+" if direction == "up" else "5%-"
                    subprocess.run(
                        ["amixer", "sset", "Master", arg],
                        check=True, timeout=2, env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    return True
                except Exception:
                    pass
                return False

            success = False
            if action in ("up", "down"):
                success = set_vol(action)
            elif action == "get":
                success = True

            if success:
                current_vol = get_vol()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "volume": current_vol}).encode('utf-8'))
            else:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'Failed to change system volume')
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode('utf-8'))

    def handle_hardware(self):
        state_file = os.environ.get('WHISPLAY_GPIO_STATE_FILE', '/tmp/whisplay-plus-gpio-state.json')
        try:
            if not os.path.exists(state_file):
                payload = {"enabled": False, "ready": False, "buttonPressed": False}
            else:
                with open(state_file, 'r', encoding='utf-8') as f:
                    payload = json.load(f)
                updated_at = float(payload.get('updatedAt', 0))
                payload['stale'] = (time.time() - updated_at) > 5 if updated_at else True
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode('utf-8'))
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode('utf-8'))

    def handle_languages(self):
        try:
            if self.command == 'POST':
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length) if content_length > 0 else b'{}'
                data = json.loads(body.decode('utf-8')) if body else {}
                requested = data.get('languages')
                if not requested:
                    requested = [data.get('lane1Language'), data.get('lane2Language')]

                languages = []
                for language in requested:
                    if not language:
                        continue
                    if language not in LANGUAGE_LABELS:
                        self.send_response(400)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "error": f"Unsupported language: {language}",
                            "supported": LANGUAGE_ORDER,
                        }).encode('utf-8'))
                        return
                    if language not in languages:
                        languages.append(language)

                languages_to_prepare = []
                with _language_prepare_lock:
                    for language in languages:
                        current = _language_prepare_status.get(language, {}).get("status")
                        if current not in ("ready", "preparing"):
                            _language_prepare_status[language] = {
                                "status": "queued",
                                "progress": 5,
                                "stage": "Queued",
                                "error": None,
                                "updatedAt": time.time(),
                            }
                            languages_to_prepare.append(language)

                if languages_to_prepare:
                    threading.Thread(
                        target=prepare_languages_background,
                        args=(languages_to_prepare,),
                        daemon=True,
                    ).start()

            payload = language_status_payload()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode('utf-8'))
        except Exception as e:
            traceback.print_exc()
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode('utf-8'))

    def handle_kiosk_exit(self):
        if (not is_local_client(self.client_address)
                or self.headers.get('Sec-Fetch-Site') in ('same-site', 'cross-site')):
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b'Forbidden: Kiosk control is only accessible locally')
            return

        payload = b'{"status":"exiting"}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        finally:
            supervisor = os.environ.get('TRANSLATOR_SUPERVISOR_PID')
            if supervisor:
                os.kill(int(supervisor), signal.SIGTERM)
            else:
                self.server.shutdown()

    def do_POST(self):
        if self.path.startswith('/proxy'):
            self.handle_proxy()
            return
        if self.path.startswith('/api/stt'):
            self.handle_stt()
            return
        if self.path.startswith('/api/volume'):
            self.handle_volume()
            return
        if self.path.startswith('/api/languages'):
            self.handle_languages()
            return
        if self.path.startswith('/api/kiosk/exit'):
            self.handle_kiosk_exit()
            return
        
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        if self.path.startswith('/proxy'):
            self.handle_proxy()
            return

        if self.path.startswith('/api/tts'):
            self.handle_tts()
            return
            
        if self.path.startswith('/api/volume'):
            self.handle_volume()
            return
        if self.path.startswith('/api/hardware'):
            self.handle_hardware()
            return
        if self.path.startswith('/api/languages'):
            self.handle_languages()
            return

        # Clean path to serve static files (strip any ?query cache-buster)
        url_path = self.path.split('?', 1)[0]
        if url_path == '/':
            url_path = '/index.html'

        dist_dir = os.path.realpath(os.path.join(BASE_DIR, '..', 'frontend', 'dist'))
        if not os.path.exists(dist_dir):
            dist_dir = os.path.realpath(os.path.join(BASE_DIR, 'dist'))
        if not os.path.exists(dist_dir):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'dist/ directory not found')
            return

        filename = url_path.lstrip('/')
        filepath = os.path.realpath(os.path.join(dist_dir, filename))
        
        # Check if the file is within dist directory
        if not filepath.startswith(dist_dir + os.sep) and filepath != dist_dir:
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b'Forbidden')
            return

        if not os.path.exists(filepath) or os.path.isdir(filepath):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'File not found')
            return

        # Determine MIME type
        ext = os.path.splitext(filepath)[1].lower()
        mime_types = {
            '.html': 'text/html',
            '.css': 'text/css',
            '.js': 'application/javascript',
            '.json': 'application/json',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.gif': 'image/gif',
            '.svg': 'image/svg+xml',
            '.ico': 'image/x-icon',
        }
        content_type = mime_types.get(ext, 'application/octet-stream')

        # Read and serve file
        try:
            with open(filepath, 'rb') as f:
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.end_headers()
                self.wfile.write(f.read())
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode('utf-8'))

if __name__ == '__main__':
    # Allow port reuse
    socketserver.TCPServer.allow_reuse_address = True
    local_ip = "localhost"
    try:
        # Create a dummy socket to find local network IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    use_ssl = os.path.exists('cert.pem') and os.path.exists('key.pem')

    preload_languages = []
    for language in os.environ.get("TRANSLATOR_PRELOAD_LANGUAGES", "zh,en").split(','):
        language = language.strip().lower()
        if language in LANGUAGE_LABELS and language not in preload_languages:
            preload_languages.append(language)
    if len(preload_languages) > MAX_MODELS:
        print(
            f"[Prewarm] Only the first {MAX_MODELS} languages fit the configured cache.",
            flush=True,
        )
        preload_languages = preload_languages[:MAX_MODELS]

    # Finish constructor-level model loading before accepting requests. This
    # makes service readiness mean STT/TTS conversion will not take a cold path.
    if preload_languages:
        print(
            f"[Prewarm] Loading resident STT/TTS models: {', '.join(preload_languages)}...",
            flush=True,
        )
        prepare_languages_background(preload_languages)
        print("[Prewarm] Resident speech models ready.", flush=True)

    with socketserver.ThreadingTCPServer((HOST, PORT), ProxyHTTPRequestHandler) as httpd:
        if use_ssl:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certfile='cert.pem', keyfile='key.pem')
            httpd.socket = context.wrap_socket(httpd.socket, server_side=True)

        protocol = "https" if use_ssl else "http"
        print(f"===========================================================")
        print(f"Gemma Translator running at:")
        print(f"👉 {protocol}://localhost:{PORT}")
        if HOST not in ('127.0.0.1', 'localhost', '::1') and local_ip != "localhost":
            print(f"👉 {protocol}://{local_ip}:{PORT} (Local Network)")
        print(f"Listening on {HOST}:{PORT}")
        print(f"===========================================================")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")
