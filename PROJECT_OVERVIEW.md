# E-Paper Desk Dashboard — Project Overview

## Hardware
- **Adafruit ESP32-S3 Feather** (4MB flash / 2MB PSRAM, 512KB internal SRAM)
- **Adafruit eInk Feather Friend** — stacks on the Feather header, provides e-paper driving circuitry + 24-pin FPC connector
- **5.83" e-paper panel, 648×480, monochrome** — Seeed part 8517180050 (panel: GDEQ0583T31, UC8179 controller)
- **Wired via USB** (the Feather's native USB) — deliberate choice over BLE
- A **physical button** wired to the ESP32, for cycling between pages, with a **5-second cooldown** between page changes to prevent refresh-spamming

## Architecture
- **No OpenDisplay** — it's BLE-only by design, and the Feather Friend isn't one of its supported hardware targets regardless
- **PC renders everything; ESP32 just displays it** — the Linux PC does all drawing (Pillow), converts to 1-bit, sends raw bytes over USB serial; the ESP32 firmware reads bytes and blits them to the panel via `Adafruit_EPD`. No graphics logic lives on the chip.
- **Two code files total**: one Python script (PC) + one Arduino/C++ firmware file (ESP32)

## Pages (cycled via the button)
1. **System Sensors** — CPU/GPU temp + fan speed, each with a 1-hour sparkline (20 points, 3-min interval) — **built and visually verified** (`render.py` + `preview.py`)
2. **System Vitals** — RAM, disk space, network throughput — planned
3. **Clock & Status** — large clock/date, updates every 1 minute (faster than the 3-min sensor cycle) — planned
4. **Weather** — location approximated from the **system timezone** (not GPS), free no-key API (Open-Meteo) — planned
5. **Now Playing (Plex)** — pulls current track/media via Plex's local API + token — planned
6. **Daily Fact (with image)** — curated local list of fact+image pairs, rotates once/day — planned
7. ~~To-Do via Google Calendar~~ — **skipped** (OAuth setup complexity); an ICS feed URL was noted as a simpler fallback if revisited later
8. **Proxmox VM/Container Status** — list of VMs/LXCs (fewer than 10) with running/stopped indicators, via Proxmox API + read-only token — planned

## Configuration
- **`config.yml`** — plain-language, hand-editable settings file (update interval, enabled pages, weather override, Plex address, Proxmox address/token, etc.)
- No GUI, no separate settings app — deliberately kept to one script + one config file, avoiding a three-part frontend/backend/firmware system

## Running it
- **System tray icon** (`pystray`) — status at a glance, quick actions (force refresh, pause, open config, quit)
- Runs continuously via a **systemd user service** — starts on boot/login, no manual command needed after setup

## Installing the program
- Distributed as a **single install script** (`bash install.sh`), not a `.deb`/`apt` package — avoids the overhead of hosting/maintaining a real APT repository
- The script:
  1. Installs Python dependencies (`pyyaml`, `pyserial`, `pystray`, `psutil`, `Pillow`)
  2. Copies script files to a permanent location
  3. **Auto-detects the ESP32's serial port** by USB vendor/product ID — no manual port entry, ever, even across reboots or different USB ports
  4. Sets up a **udev rule** so the serial port works without `sudo`
  5. Installs and enables the **systemd user service**

## Terminal-based first-time config
- Instead of sending the user straight to a text editor, `install.sh` **asks a few quick questions in the terminal** and writes the answers into `config.yml` — e.g. which pages to enable, Plex server address, Proxmox address/token, update interval
- `config.yml` remains available afterward for hand-editing later (with inline comments explaining each option) — the terminal prompts are a friendlier *first-run* path, not a replacement for the file

## End-to-end user experience
1. Get the project (download or clone)
2. Run `bash install.sh`
3. Answer a handful of terminal prompts (or accept defaults)
4. Installer finishes — dependencies installed, port auto-detected, service running, tray icon visible
5. **Set and forget** — survives reboots, updates on schedule, responds to the button; editing `config.yml` + a service restart remains available anytime for changes

## Status: what's actually been built so far
Only **page 1's rendering/preview logic** (`render.py`, `preview.py`) — verified visually with simulated data. Everything else (real sensor reads, config file, other pages, firmware, tray icon, install script) is planned but not yet built.
