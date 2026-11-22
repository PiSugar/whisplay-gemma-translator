# Raspberry Pi 5 + Whisplay Plus + PiSugar 3 Plus Installation Guide

This guide covers hardware assembly, Raspberry Pi OS setup, Whisplay Plus drivers, PiSugar Power Manager, and Gemma Translator deployment. You need an internet connection for setup and model downloads. Once the models are downloaded, you can translate offline in the languages you have set up.

## 1. What you need

- A Raspberry Pi 5 (8 GB RAM recommended), a microSD card (32 GB or larger recommended), and Ethernet or Wi-Fi with internet access. Downloading and importing Gemma requires at least 6 GB of free space on the filesystem that contains your home directory. Allow additional space for the OS, dependencies, and speech models.
- A Whisplay Plus (called Whisplay HAT+ in the official documentation), a DSI ribbon cable that fits the Pi 5, mounting hardware, and a speaker assembly that fits the Whisplay Plus 4-pin connector. For initial setup, use a monitor connected via micro-HDMI and a USB keyboard, or connect over SSH. [Whisplay Plus hardware specifications](https://docs.pisugar.com/docs/product-wiki/whisplay-plus/overview)
- A PiSugar 3 Plus with its battery and mounting screws, and a 5 V USB-C supply that meets its charging input specifications. The PiSugar 3 Plus is rated for up to 5 V / 3 A output. [PiSugar 3 Plus specifications](https://docs.pisugar.com/docs/product-wiki/battery/pisugar3/pisugar-3-series#electrical-specifications)

## 2. Flash the OS and assemble the hardware

1. Use Raspberry Pi Imager on your computer to write **Raspberry Pi OS Desktop (64-bit, Trixie)** to the microSD card. Create a user, configure networking, and enable SSH if needed. Trixie ships with Python 3.13, which meets the Python 3.12 minimum for this project's NumPy version. [Raspberry Pi OS installation](https://www.raspberrypi.com/documentation/computers/getting-started.html) · [Trixie release notes](https://www.raspberrypi.com/news/trixie-the-new-version-of-raspberry-pi-os/) · [NumPy 2.5.1](https://pypi.org/project/numpy/2.5.1/)
2. Unplug all external power and make sure the PiSugar 3 Plus is off. Mount the PiSugar 3 Plus on the **underside** of the Pi 5. Align the four mounting holes so the spring-loaded pogo pins contact the solder joints on the underside of the 40-pin GPIO header. Secure the boards with the supplied screws, keeping the battery cable clear and the pogo pins aligned. [PiSugar 3 Plus mounting instructions](https://docs.pisugar.com/docs/product-wiki/battery/pisugar3/pisugar-3-series#how-to-mount)
3. Mount the Whisplay Plus on the **top side** of the Pi 5. Align the 40-pin connectors, press them together evenly, and secure the HAT with suitable standoffs or screws. Connect the DSI ribbon cable between the Whisplay Plus and the Pi 5 port labeled **CAM/DISP 0**. Open both latches, orient the exposed metal contacts toward the contacts in each connector, insert both ends fully, and close the latches. Plug the matching speaker assembly into the Whisplay Plus 4-pin speaker connector. [Whisplay Plus hardware documentation](https://docs.pisugar.com/docs/product-wiki/whisplay-plus/overview)
4. Insert the microSD card. To charge the battery, plug the charging cable into the USB-C port on the **PiSugar 3 Plus**. With the default power button setting, press the PiSugar power button briefly, then immediately press and hold it until the LEDs light up in sequence. Use an HDMI display or SSH for the first boot, then test the DSI display after installing the driver. [PiSugar 3 Plus power-on instructions](https://docs.pisugar.com/docs/product-wiki/battery/pisugar3/pisugar-3-series#power-on)

## 3. Install the Whisplay Plus driver

Open a terminal on the Pi 5 as your regular user. Install the official driver and reboot so Raspberry Pi OS can detect the DSI display, touchscreen, and Whisplay sound card before you deploy the translator. [Official Whisplay Plus driver](https://github.com/PiSugar/whisplay-plus)

```bash
sudo apt update
sudo apt install -y git wget curl alsa-utils i2c-tools netcat-openbsd
cd "$HOME"
git clone https://github.com/PiSugar/whisplay-plus.git
cd whisplay-plus
sudo bash install.sh
sudo reboot
```

After rebooting, verify that the Whisplay Plus shows the desktop and responds to touch. These commands should list the Whisplay playback and capture devices and the Goodix touchscreen.

```bash
aplay -l
arecord -l
grep -i -A5 goodix /proc/bus/input/devices
```

Open **Whisplay Hat+ Demo** from the desktop menu to test touch, the microphone, the speaker, the button, and the RGB LED. Close the demo when you finish to release the audio devices and GPIO pins. [Official hardware diagnostics](https://docs.pisugar.com/docs/product-wiki/whisplay-plus/overview#desktop-demo-and-diagnostics)

## 4. Install PiSugar Power Manager

Run the official installer and select **PiSugar3** when prompted for the model. This option supports the PiSugar 3 Plus. [PiSugar Power Manager installation instructions](https://docs.pisugar.com/docs/product-wiki/battery/pisugar-power-manager)

```bash
cd "$HOME"
wget -O pisugar-power-manager.sh https://cdn.pisugar.com/release/pisugar-power-manager.sh
bash pisugar-power-manager.sh -c release
systemctl status --no-pager pisugar-server
```

Open `http://127.0.0.1:8421` on the Pi 5 or `http://<raspberry-pi-ip>:8421` from another device on the same local network. Verify that the page shows the battery level and charging status. Run `hostname -I` on the Pi 5 to find its IP address.

## 5. Deploy Gemma Translator

Run the following commands as the regular user who runs the desktop session. Clone the repository to `$HOME/whisplay-gemma-translator`, which is the path the generated desktop launchers use. The deployment script sets the detected Whisplay card as the default ALSA device, installs dependencies, builds the frontend, imports Gemma, and configures `gemma-translator.service` and the Chromium kiosk launcher. [Deployment script](deploy-whisplay-plus.sh)

```bash
cd "$HOME"
git clone https://github.com/PiSugar/whisplay-gemma-translator.git
cd whisplay-gemma-translator
./deploy-whisplay-plus.sh
```

If PyPI downloads time out, rerun deployment with a reachable PyPI mirror. For example, to use the Alibaba Cloud mirror:

```bash
TRANSLATOR_PYPI_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ ./deploy-whisplay-plus.sh
```

To open the translator automatically at boot, enable **Desktop Auto Login** in the Raspberry Pi desktop settings. The Wayland kiosk launcher waits for the translator page to respond before opening Chromium. Reboot to apply the display settings and group membership changes. [Raspberry Pi desktop settings](https://www.raspberrypi.com/documentation/computers/configuration.html)

```bash
sudo reboot
```

## 6. Verify the installation and troubleshoot

On first startup, the service loads Gemma and may download the Chinese and English speech models. Once `http://127.0.0.1:3000/` opens on the Pi, select **Chinese** and **English**. Hold the **physical button on the Whisplay Plus**, say a sentence, then release the button. The UI should show the transcript and translation, and the speaker should read the translation aloud. The RGB LED blinks while idle and stays lit while you hold the button. Other languages may require an internet connection the first time you use them.

```bash
systemctl status --no-pager gemma-translator.service
journalctl -u gemma-translator.service -b -n 100 --no-pager
curl -fsS http://127.0.0.1:3000/api/hardware
```

| Symptom | What to check |
| --- | --- |
| DSI display is blank or touch is unresponsive | Shut down and turn off the PiSugar output. Check the ribbon cable orientation, both latches, and the `CAM/DISP 0` connection. From an HDMI desktop or over SSH, run `sudo dmesg \| grep -Ei 'st7701\|goodix'`. If the hardware disappears after a kernel upgrade, run `sudo bash install.sh` from the official driver directory and reboot. |
| Microphone or speaker does not work | Use `arecord -l`, `aplay -l`, and the official demo to check capture and playback. If deployment ran before the Whisplay card was detected, get the driver working and rerun `./deploy-whisplay-plus.sh`. If several audio devices are available, select Whisplay for input and output in the desktop audio settings. |
| Battery level is unavailable | Check `pisugar-server` and run `sudo i2cdetect -y 1` to look for the PiSugar 3 Plus at `0x57` and `0x68`. A driver-controlled address may appear as `UU`. If I²C is disabled, enable it in `sudo raspi-config`. If the addresses are still missing, power off and check whether the pogo pins make contact under the Pi. |
| Chromium does not open in kiosk mode | From a terminal in the local desktop session, run `bash "$HOME/whisplay-gemma-translator/deploy/whisplay_kiosk.sh"` and check `/tmp/whisplay-chromium.log`. The autostart entry assumes UID `1000`. If `id -u` shows a different UID, update `/run/user/1000` in `~/.config/autostart/whisplay-translator-kiosk.desktop` to match. |
| Physical button does not respond | Check that `/api/hardware` reports `ready: true` and `stale: false`. `buttonPressed` should become `true` while you hold the button. The startup script for `gemma-translator.service` runs the GPIO bridge. The default button pin is physical pin 11 (BCM GPIO17), and the RGB LED uses physical pins 22, 18, and 16 (BCM GPIO25, 24, and 23). If your board revision uses different pins, confirm them in the official documentation, edit `/etc/default/whisplay-plus-gpio`, and run `sudo systemctl restart gemma-translator.service`. |

To shut down, run `sudo poweroff` and wait for the Pi to halt before turning off the battery output. If the PiSugar keeps supplying power after shutdown, check whether Power Manager can read the battery. [PiSugar shutdown troubleshooting](https://docs.pisugar.com/docs/product-wiki/battery/faq)

[Back to the project README](README.md)
