#!/bin/bash
# Launch Chromium on the local whisplay-plus display. Use 127.0.0.1 rather than
# localhost because this Chromium/Wayland combination can stall on localhost
# while the backend is otherwise reachable.
# Chromium app mode lets the kiosk fit narrow portrait displays.

set -e

URL="${WHISPLAY_KIOSK_URL:-http://127.0.0.1:3000/}"
PROFILE_DIR="${WHISPLAY_CHROMIUM_PROFILE:-$HOME/.local/share/whisplay-chromium-kiosk}"
LOG_FILE="${WHISPLAY_CHROMIUM_LOG:-/tmp/whisplay-chromium.log}"

mkdir -p "$PROFILE_DIR"

# Desktop login can finish before Gemma and the speech models are ready.
deadline=$((SECONDS + 600))
until curl --fail --silent --max-time 2 --output /dev/null "$URL"; do
    if (( SECONDS >= deadline )); then
        printf 'Timed out waiting for %s\n' "$URL" >> "$LOG_FILE"
        exit 1
    fi
    sleep 2
done

if ! pgrep -f '^/usr/lib/chromium/chromium .*127[.]0[.]0[.]1:3000' >/dev/null 2>&1; then
    setsid chromium \
        --user-data-dir="$PROFILE_DIR" \
        --password-store=basic \
        --no-first-run \
        --disable-restore-session-state \
        --disable-extensions \
        --ozone-platform=wayland \
        --kiosk \
        --start-fullscreen \
        --noerrdialogs \
        --disable-infobars \
        --disable-session-crashed-bubble \
        --disable-features=TranslateUI \
        --check-for-update-interval=31536000 \
        --use-fake-ui-for-media-stream \
        --autoplay-policy=no-user-gesture-required \
        --app="$URL" \
        >"$LOG_FILE" 2>&1 < /dev/null &
fi
