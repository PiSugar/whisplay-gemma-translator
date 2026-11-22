#!/usr/bin/env python3
"""Bridge whisplay-plus GPIO hardware to the kiosk UI.

The app already uses keyboard push-to-talk controls, so the hardware button is
mapped to an X11 key hold. The LED gives a tiny status heartbeat while idle and
stays solid during a button press.
"""

from __future__ import annotations

import os
import signal
import sys
import time
import json
from pathlib import Path


BUTTON_GPIO = int(os.environ.get("WHISPLAY_BUTTON_GPIO", "17"))
BUTTON_PULL_UP = os.environ.get("WHISPLAY_BUTTON_PULL_UP", "0").lower() in ("1", "true", "yes", "on")
LED_GPIOS = [
    int(pin.strip())
    for pin in os.environ.get(
        "WHISPLAY_LED_GPIOS",
        os.environ.get("WHISPLAY_LED_GPIO", "25,24,23"),
    ).split(",")
    if pin.strip()
]
BUTTON_KEY = os.environ.get("WHISPLAY_BUTTON_KEY", "z")
POLL_SECONDS = float(os.environ.get("WHISPLAY_GPIO_POLL_SECONDS", "0.02"))
STATE_FILE = Path(os.environ.get("WHISPLAY_GPIO_STATE_FILE", "/tmp/whisplay-plus-gpio-state.json"))

_running = True


def _handle_signal(signum, frame):
    global _running
    _running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def _write_state(pressed: bool, ready: bool = True) -> None:
    payload = {
        "enabled": True,
        "ready": ready,
        "buttonPressed": pressed,
        "buttonKey": BUTTON_KEY,
        "buttonGpio": BUTTON_GPIO,
        "ledGpios": LED_GPIOS,
        "updatedAt": time.time(),
    }
    tmp_file = STATE_FILE.with_suffix(".tmp")
    tmp_file.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    tmp_file.replace(STATE_FILE)


def _make_gpiozero_devices():
    if "/usr/lib/python3/dist-packages" not in sys.path:
        sys.path.append("/usr/lib/python3/dist-packages")

    from gpiozero import Button, LED

    button = Button(BUTTON_GPIO, pull_up=BUTTON_PULL_UP, bounce_time=0.03)
    leds = [LED(pin) for pin in LED_GPIOS]
    return button, leds


def _set_leds(leds, enabled: bool) -> None:
    for led in leds:
        led.on() if enabled else led.off()


def main() -> int:
    try:
        button, leds = _make_gpiozero_devices()
    except Exception as exc:
        print(f"[gpio] Failed to initialize GPIO: {exc}", file=sys.stderr, flush=True)
        return 1

    print(
        f"[gpio] whisplay-plus bridge ready "
        f"(button=GPIO{BUTTON_GPIO}, pull_up={BUTTON_PULL_UP}, "
        f"leds={','.join(f'GPIO{pin}' for pin in LED_GPIOS)}, key={BUTTON_KEY})",
        flush=True,
    )
    _write_state(False)

    pressed = False
    last_heartbeat = 0.0
    heartbeat_on = False

    try:
        while _running:
            if button.is_pressed and not pressed:
                pressed = True
                _set_leds(leds, True)
                _write_state(True)
                print("[gpio] button down", flush=True)
            elif not button.is_pressed and pressed:
                pressed = False
                _set_leds(leds, False)
                _write_state(False)
                print("[gpio] button up", flush=True)

            if not pressed:
                now = time.monotonic()
                if now - last_heartbeat >= 2.0:
                    heartbeat_on = not heartbeat_on
                    _set_leds(leds, heartbeat_on)
                    _write_state(False)
                    last_heartbeat = now

            time.sleep(POLL_SECONDS)
    finally:
        _set_leds(leds, False)
        button.close()
        for led in leds:
            led.close()
        _write_state(False, ready=False)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
