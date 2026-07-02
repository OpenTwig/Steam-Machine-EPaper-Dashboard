"""System page — combined Sensors + Vitals in compact rounded cards."""

import glob
import io
import os
import platform
import subprocess
import threading
import time
from collections import deque

import psutil
import requests
from PIL import Image

from ._base import (
    PANEL_WIDTH, PANEL_HEIGHT, BLACK,
    load_font, load_bold, to_1bit, new_page, draw_sparkline,
)

def _load_sans(size: int, bold: bool = False):
    return load_bold(size) if bold else load_font(size)

_POLL_INTERVAL = 3    # seconds between background sensor samples
_HIST_POINTS   = 120  # ~6 minutes of history at 3s intervals
_STEAM_INTERVAL = 300 # re-fetch Steam every 5 minutes

# ── History buffers ────────────────────────────────────────────────────────────
_cpu_hist  = deque(maxlen=_HIST_POINTS)
_gpu_hist  = deque(maxlen=_HIST_POINTS)
_gpuu_hist = deque(maxlen=_HIST_POINTS)
_cpuu_hist = deque(maxlen=_HIST_POINTS)
_rx_hist   = deque(maxlen=_HIST_POINTS)
_tx_hist   = deque(maxlen=_HIST_POINTS)
_ram_hist  = deque(maxlen=_HIST_POINTS)
_disk_hist = deque(maxlen=_HIST_POINTS)

_last_net  = None
_last_disk = None

# ── Cached state ──────────────────────────────────────────────────────────────
_poll_thread      = None
_poll_stop        = threading.Event()

_steam_profile    = {}    # {username, avatar_url, status}
_steam_avatar_img = None  # grayscale PIL Image, resized+dithered at render time
_last_steam_fetch = 0.0

_cache_os       = ""
_cache_bios     = ""
_cache_steam_id = ""

# ── Startup ───────────────────────────────────────────────────────────────────

def init_history(cfg: dict):
    """Called once at startup with the full page config. Blocks until Steam data
    is fetched, then starts the background sensor poll thread."""
    global _cpu_hist, _gpu_hist, _gpuu_hist, _cpuu_hist, _rx_hist, _tx_hist, _ram_hist, _disk_hist
    global _cache_os, _cache_bios, _cache_steam_id
    global _steam_profile, _steam_avatar_img, _last_steam_fetch

    n = cfg.get("history_points", _HIST_POINTS)
    _cpu_hist  = deque(maxlen=n)
    _gpu_hist  = deque(maxlen=n)
    _gpuu_hist = deque(maxlen=n)
    _cpuu_hist = deque(maxlen=n)
    _rx_hist   = deque(maxlen=n)
    _tx_hist   = deque(maxlen=n)
    _ram_hist  = deque(maxlen=n)
    _disk_hist = deque(maxlen=n)

    # 1. Fetch Steam profile + avatar synchronously (blocks, but user sees real data first)
    api_key = cfg.get("steam_api_key", "")
    if api_key:
        profile = _fetch_steam_profile(api_key)
        _steam_profile = profile
        if profile.get("avatar_url"):
            raw = _fetch_steam_avatar(profile["avatar_url"])
            if raw:
                _steam_avatar_img = raw
        _last_steam_fetch = time.monotonic()

    # 2. Fetch static system info (fast, local)
    _cache_os       = _os_label()
    _cache_bios     = _bios_label()
    _cache_steam_id = _steam_id()

    # 3. Prime delta sensors then start background poll
    threading.Thread(target=_prime_sensors, daemon=True, name="sys-prime").start()


def _prime_sensors():
    """Prime delta-based sensors then kick off the poll loop."""
    _net_rates()
    _disk_activity()
    psutil.cpu_percent(interval=None)  # discard first 0.0
    time.sleep(0.5)
    _poll_sensors()
    _start_poll_loop()


def _poll_sensors():
    """Take one sensor sample and append to all history buffers."""
    try:
        cpu, _ = _cpu_metric()
        _cpu_hist.append(cpu)
        _gpu_hist.append(_gpu_temp())
        _gpuu_hist.append(_gpu_usage())
        _cpuu_hist.append(_cpu_usage())
        _ram_hist.append(psutil.virtual_memory().percent)
        _disk_hist.append(_disk_activity())
        rx, tx = _net_rates()
        _rx_hist.append(rx)
        _tx_hist.append(tx)
    except Exception:
        pass


def _poll_loop():
    """Background loop: sample sensors every _POLL_INTERVAL seconds,
    and refresh Steam profile every _STEAM_INTERVAL seconds."""
    global _steam_profile, _steam_avatar_img, _last_steam_fetch
    while not _poll_stop.wait(_POLL_INTERVAL):
        _poll_sensors()
        # Refresh Steam periodically (uses cached api_key from last init_history call)
        if _last_steam_fetch and (time.monotonic() - _last_steam_fetch) >= _STEAM_INTERVAL:
            api_key = _steam_profile.get("_api_key", "")
            if api_key:
                try:
                    profile = _fetch_steam_profile(api_key)
                    _steam_profile = profile
                    if profile.get("avatar_url"):
                        raw = _fetch_steam_avatar(profile["avatar_url"])
                        if raw:
                            _steam_avatar_img = raw
                    _last_steam_fetch = time.monotonic()
                except Exception:
                    pass


def _start_poll_loop():
    global _poll_thread
    _poll_stop.clear()
    if _poll_thread and _poll_thread.is_alive():
        return
    _poll_thread = threading.Thread(target=_poll_loop, daemon=True, name="sys-poll")
    _poll_thread.start()


# ── Sensor reads ───────────────────────────────────────────────────────────────

def _cpu_temp() -> float:
    try:
        temps = psutil.sensors_temperatures()
        for key in ("coretemp", "cpu_thermal", "k10temp", "acpitz"):
            if temps.get(key):
                return temps[key][0].current
    except (AttributeError, OSError):
        pass
    return 0.0


def _cpu_base_ghz() -> float:
    if platform.system() == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            mhz = winreg.QueryValueEx(key, "~MHz")[0]
            winreg.CloseKey(key)
            return mhz / 1000.0
        except Exception:
            pass
    else:
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("cpu MHz"):
                        return float(line.split(":")[1].strip()) / 1000.0
        except Exception:
            pass
    freq = psutil.cpu_freq()
    return freq.max / 1000.0 if freq else 0.0


def _cpu_metric() -> tuple[float, str]:
    if platform.system() != "Windows":
        t = _cpu_temp()
        if t > 0:
            return t, "°C"
    return _cpu_base_ghz(), "GHz"


def _gpu_usage() -> float:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            timeout=2, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return float(out.strip().split()[0])
    except Exception:
        return 0.0


def _gpu_temp() -> float:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            timeout=2, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return float(out.strip().split()[0])
    except Exception:
        pass
    try:
        for hwmon in sorted(os.listdir("/sys/class/hwmon")):
            base = f"/sys/class/hwmon/{hwmon}"
            name_path = os.path.join(base, "name")
            if not os.path.exists(name_path):
                continue
            with open(name_path) as f:
                if f.read().strip() not in ("amdgpu", "radeon"):
                    continue
            for fname in sorted(os.listdir(base)):
                if fname.startswith("temp") and fname.endswith("_input") and "edge" in fname:
                    with open(os.path.join(base, fname)) as f:
                        return float(f.read().strip()) / 1000.0
    except (OSError, ValueError):
        pass
    return 0.0


def _cpu_usage() -> float:
    v = psutil.cpu_percent(interval=None)
    if v == 0.0:
        v = psutil.cpu_percent(interval=0.1)
    return v


def _disk_activity() -> float:
    global _last_disk
    now  = time.monotonic()
    stat = psutil.disk_io_counters()
    if stat is None:
        return 0.0
    if hasattr(stat, "busy_time"):
        marker = stat.busy_time
        if _last_disk is None:
            _last_disk = (marker, now)
            return 0.0
        old_marker, old_t = _last_disk
        dt = max(now - old_t, 0.001)
        _last_disk = (marker, now)
        return min(100.0, (marker - old_marker) / (dt * 1000) * 100)
    else:
        marker = stat.read_bytes + stat.write_bytes
        if _last_disk is None:
            _last_disk = (marker, now)
            return 0.0
        old_marker, old_t = _last_disk
        dt = max(now - old_t, 0.001)
        _last_disk = (marker, now)
        return min(100.0, (marker - old_marker) / dt / 1024 / 1024 / 500 * 100)


def _net_rates() -> tuple[float, float]:
    global _last_net
    now  = time.monotonic()
    stat = psutil.net_io_counters()
    rx, tx = stat.bytes_recv, stat.bytes_sent
    if _last_net is None:
        _last_net = (rx, tx, now)
        return 0.0, 0.0
    old_rx, old_tx, old_t = _last_net
    dt = max(now - old_t, 0.001)
    _last_net = (rx, tx, now)
    return max(0.0, (rx - old_rx) / dt / 1024), max(0.0, (tx - old_tx) / dt / 1024)


def _fmt_net(kbs: float) -> str:
    mbps = kbs * 8 / 1000
    if mbps >= 1000:
        return f"{mbps/1000:.2f}Gbps"
    if mbps >= 1:
        return f"{mbps:.1f}Mbps"
    return f"{kbs*8:.0f}Kbps"


def _net_type() -> str:
    """Return a short label for the active network interface type."""
    try:
        import socket
        # Find interface carrying the default route by connecting a UDP socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            iface_ip = s.getsockname()[0]
        finally:
            s.close()

        # Match IP to interface name
        iface_name = None
        for name, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.address == iface_ip:
                    iface_name = name
                    break
            if iface_name:
                break

        if not iface_name:
            return "NET"

        low = iface_name.lower()

        # Ethernet check first
        if any(k in low for k in ("eth", "en", "local area connection", "enet")):
            # Check link speed to distinguish Ethernet from Wi-Fi on Windows
            stats = psutil.net_if_stats().get(iface_name)
            if stats and stats.speed > 0:
                # Wi-Fi on Windows often reports speed in Mbps >= 54
                # Ethernet is usually 100/1000/2500/10000
                if any(k in low for k in ("wi", "wlan", "wifi", "wireless", "802.11")):
                    return _wifi_gen(iface_name, stats.speed)
            if not any(k in low for k in ("wi", "wlan", "wifi", "wireless")):
                stats = psutil.net_if_stats().get(iface_name)
                speed = stats.speed if stats else 0
                if speed >= 10000:
                    return "10GbE"
                if speed >= 2500:
                    return "2.5GbE"
                if speed >= 1000:
                    return "GbE"
                if speed > 0:
                    return f"{speed}M ETH"
                return "ETH"

        if any(k in low for k in ("wi", "wlan", "wifi", "wireless", "wlp", "wlo")):
            stats = psutil.net_if_stats().get(iface_name)
            speed = stats.speed if stats else 0
            return _wifi_gen(iface_name, speed)

        # Fallback: use link speed heuristic
        stats = psutil.net_if_stats().get(iface_name)
        if stats and stats.speed > 0:
            if stats.speed >= 1000:
                return "GbE"
            return "ETH"
        return iface_name[:6]
    except Exception:
        return "NET"


def _wifi_gen(iface_name: str, speed_mbps: int) -> str:
    """Map link speed to Wi-Fi generation label."""
    if platform.system() == "Windows":
        try:
            import subprocess as _sp
            out = _sp.check_output(
                ["netsh", "wlan", "show", "interfaces"],
                timeout=3, stderr=_sp.DEVNULL, text=True,
                creationflags=_sp.CREATE_NO_WINDOW,
            )
            for line in out.splitlines():
                if "Radio type" in line or "radio type" in line:
                    val = line.split(":", 1)[-1].strip()
                    if "802.11be" in val or "11be" in val:
                        return "WiFi-7"
                    if "802.11ax" in val or "11ax" in val:
                        return "WiFi-6"
                    if "802.11ac" in val or "11ac" in val:
                        return "WiFi-5"
                    if "802.11n"  in val or "11n"  in val:
                        return "WiFi-4"
                    if "802.11g"  in val:
                        return "WiFi-3"
        except Exception:
            pass
    # Fallback: infer from link speed
    if speed_mbps >= 2400:
        return "WiFi-6"
    if speed_mbps >= 433:
        return "WiFi-5"
    if speed_mbps >= 150:
        return "WiFi-4"
    if speed_mbps > 0:
        return "WiFi"
    return "WiFi"


# ── System info ────────────────────────────────────────────────────────────────

def _os_label() -> str:
    try:
        edition = platform.win32_edition()
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
        display_ver = winreg.QueryValueEx(key, "DisplayVersion")[0]
        winreg.CloseKey(key)
        short_ed = "Pro" if "Pro" in edition else edition[:4]
        return f"Win11 {short_ed} {display_ver}"
    except Exception:
        return f"Windows {platform.release()}"


def _bios_label() -> str:
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"HARDWARE\DESCRIPTION\System\BIOS")
        ver = winreg.QueryValueEx(key, "BIOSVersion")[0]
        winreg.CloseKey(key)
        if isinstance(ver, list):
            ver = ver[0]
        return str(ver).strip()
    except Exception:
        pass
    try:
        with open("/sys/class/dmi/id/bios_version") as f:
            return f.read().strip()
    except Exception:
        pass
    return "N/A"


def _steam_id64() -> str | None:
    paths = glob.glob(os.path.expandvars(r"%PROGRAMFILES(X86)%\Steam\config\loginusers.vdf"))
    paths += glob.glob(os.path.expandvars(r"%PROGRAMFILES%\Steam\config\loginusers.vdf"))
    paths += glob.glob(os.path.expanduser("~/.steam/steam/config/loginusers.vdf"))
    for p in paths:
        try:
            with open(p) as f:
                for line in f:
                    line = line.strip().strip('"')
                    if line.isdigit() and len(line) == 17:
                        return line
        except Exception:
            pass
    return None


def _steam_id() -> str:
    sid = _steam_id64()
    return str(int(sid) & 0xFFFFFFFF) if sid else "N/A"


_PERSONA_STATE = {
    0: "Offline", 1: "Online", 2: "Busy", 3: "Away",
    4: "Snooze", 5: "Looking to trade", 6: "Looking to play",
}


def _fetch_steam_profile(api_key: str) -> dict:
    sid64 = _steam_id64()
    if not sid64 or not api_key:
        return {}
    try:
        r = requests.get(
            "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/",
            params={"key": api_key, "steamids": sid64},
            timeout=5,
        )
        r.raise_for_status()
        players = r.json().get("response", {}).get("players", [])
        if not players:
            return {}
        p      = players[0]
        status = _PERSONA_STATE.get(p.get("personastate", 0), "Offline")
        if p.get("gameextrainfo"):
            status = f"Playing {p['gameextrainfo']}"
        result = {
            "username":   p.get("personaname", ""),
            "avatar_url": p.get("avatarmedium", ""),
            "status":     status,
            "_api_key":   api_key,  # store for background refresh
        }
    except Exception:
        return {}

    # Fetch friend list + online count
    try:
        rf = requests.get(
            "https://api.steampowered.com/ISteamUser/GetFriendList/v1/",
            params={"key": api_key, "steamid": sid64, "relationship": "friend"},
            timeout=5,
        )
        rf.raise_for_status()
        friends = rf.json().get("friendslist", {}).get("friends", [])
        if friends:
            fids = ",".join(f["steamid"] for f in friends)
            rs = requests.get(
                "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/",
                params={"key": api_key, "steamids": fids},
                timeout=5,
            )
            rs.raise_for_status()
            fplayers = rs.json().get("response", {}).get("players", [])
            result["friends_online"] = sum(1 for fp in fplayers if fp.get("personastate", 0) != 0)
    except Exception:
        pass

    return result


def _fetch_steam_avatar(url: str):
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("L")
    except Exception:
        return None


# ── Card drawing ───────────────────────────────────────────────────────────────

PAD    = 6
RADIUS = 6
GAP    = 8
FONT_H = 24
HDR_H  = FONT_H + PAD


def _fit_font(draw, text: str, max_w: int, max_h: int, bold: bool = False) -> tuple:
    for size in range(max_h, 6, -1):
        f  = _load_sans(size, bold)
        bb = draw.textbbox((0, 0), text, font=f)
        if (bb[2] - bb[0]) <= max_w:
            return f, bb
    f  = _load_sans(6)
    bb = draw.textbbox((0, 0), text, font=f)
    return f, bb


def _draw_info_card(draw, x0: int, y0: int, card_w: int, card_h: int,
                    label: str, value: str):
    x1, y1 = x0 + card_w, y0 + card_h
    draw.rounded_rectangle([x0, y0, x1, y1], radius=RADIUS, outline=BLACK, width=2)

    hpad    = int(PAD * 1.5)
    lf, lbb = _fit_font(draw, label, card_w // 2 - hpad * 2, FONT_H)
    label_w = lbb[2] - lbb[0]
    vf, vbb = _fit_font(draw, value, card_w - label_w - hpad * 3, int(FONT_H * 0.75))

    vw    = vbb[2] - vbb[0]
    lh    = lbb[3] - lbb[1]
    vh    = vbb[3] - vbb[1]
    row_h = max(lh, vh)
    ty    = y0 + (card_h - row_h) // 2

    draw.text((x0 + hpad,      ty + (row_h - lh) // 2 - lbb[1]), label, font=lf, fill=BLACK)
    draw.text((x1 - hpad - vw, ty + (row_h - vh) // 2 - vbb[1]), value, font=vf, fill=BLACK)


def _draw_card(draw, x0: int, y0: int, card_w: int, card_h: int,
               label: str, value: str, hist: list,
               value2: str | None = None, min_val=None, max_val=None,
               value3: str | None = None):
    x1, y1 = x0 + card_w, y0 + card_h
    draw.rounded_rectangle([x0, y0, x1, y1], radius=RADIUS, outline=BLACK, width=2)

    div_y  = y0 + HDR_H
    max_lw = card_w // 2 - int(PAD * 1.5) * 2
    max_vw = card_w // 2 - int(PAD * 1.5) * 2

    lf, lbb = _fit_font(draw, label, max_lw, FONT_H)
    vf, vbb = _fit_font(draw, value, max_vw, FONT_H)

    vw     = vbb[2] - vbb[0]
    row_h  = max(lbb[3], vbb[3])
    text_y = (HDR_H - row_h) // 2

    draw.text((x0 + int(PAD * 1.5),      y0 + text_y), label, font=lf, fill=BLACK)
    draw.text((x1 - int(PAD * 1.5) - vw, y0 + text_y), value, font=vf, fill=BLACK)
    draw.line([(x0, div_y), (x1, div_y)], fill=BLACK, width=1)

    sp_x0 = x0 + PAD
    sp_x1 = x1 - PAD
    sp_y0 = div_y + PAD

    if value2 is not None:
        # value2 is ▲upload, value3 is ▼download — render both bottom-right, sparkline above
        ul_text = value2
        dl_text = value3 or ""
        label_max_w = (sp_x1 - sp_x0) // 2 - PAD
        label_max_h = HDR_H - PAD * 2
        ulf, ulbb = _fit_font(draw, ul_text, label_max_w, label_max_h)
        row_h = ulbb[3] - ulbb[1]
        if dl_text:
            dlf, dlbb = _fit_font(draw, dl_text, label_max_w, label_max_h)
            row_h = max(row_h, dlbb[3] - dlbb[1])
        ty    = y1 - PAD - row_h
        sp_y1 = ty - PAD
        draw_sparkline(draw, (sp_x0, sp_y0, sp_x1, sp_y1), hist,
                       min_val=min_val, max_val=max_val)
        ul_w = ulbb[2] - ulbb[0]
        ul_x = sp_x1 - ul_w
        draw.text((ul_x, ty - ulbb[1]), ul_text, font=ulf, fill=BLACK)
        if dl_text:
            dl_w = dlbb[2] - dlbb[0]
            dl_x = ul_x - PAD - dl_w
            draw.text((dl_x, ty - dlbb[1]), dl_text, font=dlf, fill=BLACK)
    else:
        draw_sparkline(draw, (sp_x0, sp_y0, sp_x1, y1 - PAD), hist,
                       min_val=min_val, max_val=max_val)


# ── Render ─────────────────────────────────────────────────────────────────────

def render(cfg: dict | None = None) -> bytes:
    cpu      = _cpu_hist[-1]  if _cpu_hist  else 0.0
    gpu      = _gpu_hist[-1]  if _gpu_hist  else 0.0
    gpuu     = _gpuu_hist[-1] if _gpuu_hist else 0.0
    cpuu     = _cpuu_hist[-1] if _cpuu_hist else 0.0
    ram_pct  = _ram_hist[-1]  if _ram_hist  else 0.0
    disk_act = _disk_hist[-1] if _disk_hist else 0.0
    rx_kbs   = _rx_hist[-1]   if _rx_hist   else 0.0
    tx_kbs   = _tx_hist[-1]   if _tx_hist   else 0.0
    cpu_unit = "GHz" if platform.system() == "Windows" else "°C"

    img, draw = new_page()

    COLS     = 3
    header_h = PANEL_HEIGHT // 5
    total_w  = PANEL_WIDTH - GAP * (COLS + 1)
    card_w   = total_w // COLS
    info_h   = HDR_H
    card_h   = (PANEL_HEIGHT - header_h - GAP * 4 - info_h) // 2

    # ── Steam header ──────────────────────────────────────────────────────────
    profile  = _steam_profile
    username = profile.get("username", "")
    status   = profile.get("status", "")

    avatar_size = header_h - GAP * 2
    avatar_x    = GAP
    avatar_y    = GAP

    if _steam_avatar_img:
        sized = _steam_avatar_img.resize((avatar_size, avatar_size), Image.LANCZOS)
        img.paste(sized.convert("1").convert("L"), (avatar_x, avatar_y))

    draw.rectangle([avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size],
                   outline=BLACK, width=2)

    text_x = avatar_x + avatar_size + GAP * 2
    text_w  = PANEL_WIDTH - text_x - GAP

    sub_size = int(header_h * 0.25)
    sub_f    = load_font(sub_size)
    sub_bb   = draw.textbbox((0, 0), status or " ", font=sub_f)
    sub_h    = sub_bb[3] - sub_bb[1]

    name_f, name_bb = _fit_font(draw, username or "", text_w, int(header_h * 0.50))
    name_h = name_bb[3] - name_bb[1]

    line_gap     = 6
    total_text_h = name_h + line_gap + sub_h
    block_y      = avatar_y + (avatar_size - total_text_h) // 2

    if username:
        draw.text((text_x, block_y - name_bb[1]), username, font=name_f, fill=BLACK)
    if status:
        draw.text((text_x, block_y + name_h + line_gap - sub_bb[1]), status, font=sub_f, fill=BLACK)

    # ── Top info row ──────────────────────────────────────────────────────────
    info_y = header_h + GAP
    for col, (label, value) in enumerate([
        ("OS",    _cache_os),
        ("BIOS",  _cache_bios),
        ("STEAM", _cache_steam_id),
    ]):
        _draw_info_card(draw, GAP + col * (card_w + GAP), info_y, card_w, info_h, label, value)

    # ── Metric cards (2 rows of 3) ────────────────────────────────────────────
    net_type = _net_type()
    # cards: (label, header_value, hist, value2/upload, min, max, value3/download)
    if platform.system() == "Windows":
        cards = [
            ("CPU",  f"{cpuu:.0f}%",    list(_cpuu_hist), None,                    0,    100,  None),
            ("GPU",  f"{gpu:.0f}°C",    list(_gpu_hist),  None,                    None, None, None),
            ("GPU%", f"{gpuu:.0f}%",    list(_gpuu_hist), None,                    0,    100,  None),
            ("RAM",  f"{ram_pct:.0f}%", list(_ram_hist),  None,                    0,    100,  None),
            ("DISK", f"{disk_act:.0f}%",list(_disk_hist), None,                    None, None, None),
            ("NET",  net_type,          list(_rx_hist),   f"▲{_fmt_net(tx_kbs)}", 0,    None, f"▼{_fmt_net(rx_kbs)}"),
        ]
    else:
        cards = [
            ("CPU",  f"{cpu:.1f}{cpu_unit}", list(_cpu_hist),  None,                    None, None, None),
            ("CPU%", f"{cpuu:.0f}%",         list(_cpuu_hist), None,                    0,    100,  None),
            ("GPU",  f"{gpu:.0f}°C",         list(_gpu_hist),  None,                    None, None, None),
            ("RAM",  f"{ram_pct:.0f}%",      list(_ram_hist),  None,                    0,    100,  None),
            ("DISK", f"{disk_act:.0f}%",     list(_disk_hist), None,                    None, None, None),
            ("NET",  net_type,               list(_rx_hist),   f"▲{_fmt_net(tx_kbs)}", 0,    None, f"▼{_fmt_net(rx_kbs)}"),
        ]

    for i, (label, value, hist, value2, mn, mx, value3) in enumerate(cards):
        col = i % COLS
        row = i // COLS
        x0  = GAP + col * (card_w + GAP)
        y0  = info_y + info_h + GAP + row * (card_h + GAP)
        _draw_card(draw, x0, y0, card_w, card_h, label, value, hist, value2, mn, mx, value3)

    return to_1bit(img).tobytes()
