#!/bin/bash
# whisplay-plus Raspberry Pi appliance bootstrap.

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_USER="$(whoami)"
CURRENT_UID="$(id -u)"
BOOT_CONFIG="/boot/firmware/config.txt"
if [ ! -f "$BOOT_CONFIG" ]; then
    BOOT_CONFIG="/boot/config.txt"
fi

echo "==========================================================="
echo "Gemma Translator — whisplay-plus Deployment"
echo "Project Dir: ${PROJECT_DIR}"
echo "User: ${CURRENT_USER} (UID: ${CURRENT_UID})"
echo "==========================================================="

echo "[1/9] Installing whisplay-plus OS dependencies..."
if command -v apt-get &> /dev/null; then
    sudo apt-get update
    sudo apt-get install -y \
        python3-venv python3-pip python3-gpiozero python3-lgpio \
        curl ffmpeg libasound2-dev pulseaudio-utils alsa-utils \
        chromium-browser
    sudo apt-get install -y pipewire-utils 2>/dev/null || true
else
    echo "[INFO] apt-get not detected. Skipping Debian package installation."
fi

if ! command -v npm &> /dev/null; then
    if command -v apt-get &> /dev/null; then
        echo "[INFO] Node.js/npm not found. Installing via apt..."
        sudo apt-get install -y nodejs npm
    else
        echo "[ERROR] npm is required but not installed, and apt-get is unavailable."
        exit 1
    fi
fi

echo "[2/9] Enabling DSI/touch-friendly Raspberry Pi display settings..."
if [ -f "$BOOT_CONFIG" ]; then
    if ! grep -q '^display_auto_detect=1' "$BOOT_CONFIG"; then
        echo 'display_auto_detect=1' | sudo tee -a "$BOOT_CONFIG" > /dev/null
    fi
    if ! grep -q '^dtoverlay=vc4-kms-v3d' "$BOOT_CONFIG"; then
        echo 'dtoverlay=vc4-kms-v3d' | sudo tee -a "$BOOT_CONFIG" > /dev/null
    fi
else
    echo "[WARNING] Boot config not found; skipped DSI display config."
fi

echo "[3/9] Installing backend environment..."
"${PROJECT_DIR}/setup.sh"

echo "[4/9] Installing frontend dependencies and building production UI..."
npm --prefix "${PROJECT_DIR}/frontend" install
npm --prefix "${PROJECT_DIR}/frontend" run build

echo "[5/9] Downloading LiteRT model if needed..."
"${PROJECT_DIR}/download_model.sh"

echo "[6/9] Configuring whisplay-sound as default ALSA device when detected..."
SOUND_CARD_NAME="${WHISPLAY_SOUND_CARD_NAME:-whisplay}"
CARD_ID="$(aplay -l 2>/dev/null | awk -v needle="$(echo "$SOUND_CARD_NAME" | tr '[:upper:]' '[:lower:]')" '
    /^card [0-9]+:/ {
        line=tolower($0)
        if (index(line, needle) > 0) {
            gsub(":", "", $2)
            print $2
            exit
        }
    }
')"
if [ -n "$CARD_ID" ]; then
    sudo tee /etc/asound.conf > /dev/null <<EOF
pcm.!default {
    type asym
    playback.pcm "hw:${CARD_ID},0"
    capture.pcm "hw:${CARD_ID},0"
}

ctl.!default {
    type hw
    card ${CARD_ID}
}
EOF
    echo "[INFO] Default ALSA card set to card ${CARD_ID} (${SOUND_CARD_NAME})."
else
    echo "[WARNING] No ALSA card matching '${SOUND_CARD_NAME}' found. Leaving ALSA default unchanged."
    aplay -l 2>/dev/null || true
fi

echo "[7/9] Registering Gemma Translator systemd service..."
SERVICE_FILE="/etc/systemd/system/gemma-translator.service"
sed -e "s|{{USER}}|${CURRENT_USER}|g" \
    -e "s|{{PROJECT_DIR}}|${PROJECT_DIR}|g" \
    -e "s|{{UID}}|${CURRENT_UID}|g" \
    "${PROJECT_DIR}/deploy/gemma-translator.service" | sudo tee "$SERVICE_FILE" > /dev/null
sudo chmod 644 "$SERVICE_FILE"

echo "[8/9] Installing whisplay-plus GPIO defaults..."
sudo install -m 644 "${PROJECT_DIR}/deploy/whisplay-plus.env" /etc/default/whisplay-plus-gpio
if command -v usermod &> /dev/null; then
    sudo usermod -aG gpio,audio,video,input "$CURRENT_USER" 2>/dev/null || true
fi

echo "[9/9] Configuring kiosk autostart and starting services..."
LXSESSION_DIR="/home/${CURRENT_USER}/.config/lxsession/rpd-x"
AUTOSTART_FILE="${LXSESSION_DIR}/autostart"
XDG_AUTOSTART_DIR="/home/${CURRENT_USER}/.config/autostart"
XDG_DESKTOP_FILE="${XDG_AUTOSTART_DIR}/whisplay-translator-kiosk.desktop"
DESKTOP_DIR="/home/${CURRENT_USER}/Desktop"
DESKTOP_FILE="${DESKTOP_DIR}/whisplay-translator.desktop"
mkdir -p "$LXSESSION_DIR"
mkdir -p "$XDG_AUTOSTART_DIR"
mkdir -p "$DESKTOP_DIR"
chmod +x "${PROJECT_DIR}/deploy/whisplay_kiosk.sh" 2>/dev/null || true
if [ -f "/etc/xdg/lxsession/rpd-x/autostart" ] && [ ! -f "$AUTOSTART_FILE" ]; then
    cp /etc/xdg/lxsession/rpd-x/autostart "$AUTOSTART_FILE"
fi
sed -i '/@chromium/d' "$AUTOSTART_FILE" 2>/dev/null || true
sed -i '/whisplay-gemma-translator.*start.sh --prod/d' "$AUTOSTART_FILE" 2>/dev/null || true
sed -i '/whisplay_kiosk.sh/d' "$AUTOSTART_FILE" 2>/dev/null || true
echo "@bash ${PROJECT_DIR}/deploy/whisplay_kiosk.sh" >> "$AUTOSTART_FILE"
cat > "$XDG_DESKTOP_FILE" <<'EOF'
[Desktop Entry]
Type=Application
Name=Whisplay Translator Kiosk
Comment=Launch Gemma Translator on the local whisplay-plus display
Exec=sh -lc "XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 DISPLAY=:0 XAUTHORITY=${HOME}/.Xauthority bash ${HOME}/whisplay-gemma-translator/deploy/whisplay_kiosk.sh"
X-GNOME-Autostart-enabled=true
EOF
cat > "$DESKTOP_FILE" <<'EOF'
[Desktop Entry]
Type=Application
Name=Whisplay Translator
Comment=Open Gemma Translator on the local display
Exec=sh -lc "XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 DISPLAY=:0 XAUTHORITY=${HOME}/.Xauthority bash ${HOME}/whisplay-gemma-translator/deploy/whisplay_kiosk.sh"
Terminal=false
Categories=Utility;
EOF
chmod +x "$DESKTOP_FILE"

sudo systemctl daemon-reload
sudo systemctl enable gemma-translator.service
pkill -f "${PROJECT_DIR}/start.sh --prod" 2>/dev/null || true
pkill -f "${PROJECT_DIR}/venv/bin/litert-lm serve" 2>/dev/null || true
pkill -f "${PROJECT_DIR}/backend/server.py" 2>/dev/null || true
pkill -f "${PROJECT_DIR}/deploy/whisplay_plus_gpio.py" 2>/dev/null || true
sudo systemctl restart gemma-translator.service

echo "==========================================================="
echo "whisplay-plus deployment complete."
echo "UI: http://localhost:3000"
echo "LAN UI: http://<translator-ip>:3000"
echo "A reboot is recommended if DSI/audio/GPIO groups changed."
echo "==========================================================="
systemctl status --no-pager gemma-translator.service || true
