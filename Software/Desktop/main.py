"""
main.py — E-Paper Dashboard entry point.

Loads config, connects to the ESP32, keeps a per-page rendered-image cache,
and pushes updates on schedule or on button press from the ESP32.
"""

import logging
import os
import queue
import shutil
import subprocess
import sys
import time

import config_loader
import serial_sender as ss
import tray_icon as ti
from pages import system, clock, weather, fact, proxmox, sports, plex

_log_dir  = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "EPaperDashboard")
os.makedirs(_log_dir, exist_ok=True)
_log_file = os.path.join(_log_dir, "dashboard.log")

from logging.handlers import RotatingFileHandler as _RFH
logging.basicConfig(
    level    = logging.INFO,
    format   = "%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    datefmt  = "%H:%M:%S",
    handlers = [
        _RFH(_log_file, maxBytes=512*1024, backupCount=1, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
# Silence noisy third-party loggers
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("PIL").setLevel(logging.WARNING)

log = logging.getLogger("main")
log.info("=== E-Paper Dashboard starting (PID %d) ===", os.getpid())


def _resource(rel: str) -> str:
    """Resolve a path relative to the exe/script; works inside PyInstaller bundles."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def _ensure_config() -> str:
    """
    Return the path to the user's config.yml in %APPDATA%\\EPaperDashboard\\.
    On first run, copies the template there and opens it in Notepad so the
    user can fill in their settings before the app continues.
    """
    app_dir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                           "EPaperDashboard")
    os.makedirs(app_dir, exist_ok=True)
    config_path = os.path.join(app_dir, "config.yml")

    if not os.path.exists(config_path):
        template = _resource("config.template.yml")
        shutil.copy(template, config_path)
        log.info("First run — copied config template to %s", config_path)

        if sys.platform.startswith("win"):
            import ctypes
            import ctypes.wintypes

            ctypes.windll.user32.MessageBoxW(
                0,
                f"Welcome to E-Paper Dashboard!\n\nA config file has been created at:\n{config_path}\n\nClick OK to open it in Notepad.\nEdit your settings, save, then close Notepad to continue.",
                "E-Paper Dashboard — First Run Setup",
                0x40,  # MB_ICONINFORMATION
            )

            # ShellExecuteEx with SEE_MASK_NOCLOSEPROCESS so we get a handle
            # to wait on — no CMD window involved at all.
            class SHELLEXECUTEINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize",       ctypes.wintypes.DWORD),
                    ("fMask",        ctypes.wintypes.ULONG),
                    ("hwnd",         ctypes.wintypes.HWND),
                    ("lpVerb",       ctypes.wintypes.LPCWSTR),
                    ("lpFile",       ctypes.wintypes.LPCWSTR),
                    ("lpParameters", ctypes.wintypes.LPCWSTR),
                    ("lpDirectory",  ctypes.wintypes.LPCWSTR),
                    ("nShow",        ctypes.c_int),
                    ("hInstApp",     ctypes.wintypes.HINSTANCE),
                    ("lpIDList",     ctypes.c_void_p),
                    ("lpClass",      ctypes.wintypes.LPCWSTR),
                    ("hkeyClass",    ctypes.wintypes.HKEY),
                    ("dwHotKey",     ctypes.wintypes.DWORD),
                    ("hIconOrMonitor", ctypes.wintypes.HANDLE),
                    ("hProcess",     ctypes.wintypes.HANDLE),
                ]

            SEE_MASK_NOCLOSEPROCESS = 0x00000040
            sei = SHELLEXECUTEINFO()
            sei.cbSize       = ctypes.sizeof(sei)
            sei.fMask        = SEE_MASK_NOCLOSEPROCESS
            sei.lpVerb       = "open"
            sei.lpFile       = "notepad.exe"
            sei.lpParameters = config_path
            sei.nShow        = 1  # SW_SHOWNORMAL

            ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))
            if sei.hProcess:
                ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, 0xFFFFFFFF)
                ctypes.windll.kernel32.CloseHandle(sei.hProcess)
        else:
            subprocess.run(["xdg-open", config_path])

    return config_path


CONFIG_PATH = _ensure_config()


# ── Page registry ─────────────────────────────────────────────────────────────

def _build_pages(cfg: dict) -> dict:
    """Return ordered dict of enabled page_name → zero-arg render callable."""
    pages_out = {}

    def reg(name: str, fn):
        if config_loader.is_enabled(cfg, name):
            pages_out[name] = fn

    pc = lambda n: config_loader.page_cfg(cfg, n)

    reg("system",          lambda: system.render(pc("system")))
    reg("clock",           lambda: clock.render(pc("clock"), weather.fetch(pc("weather"))))
    reg("weather",         lambda: weather.render(weather.fetch(pc("weather"))))
    reg("plex",            lambda: plex.render(plex.fetch(pc("plex"))))
    reg("fact",            lambda: fact.render(fact.fetch(pc("fact"))))
    reg("proxmox",         lambda: proxmox.render(proxmox.fetch(pc("proxmox"))))
    reg("sports_calendar", lambda: sports.render(sports.fetch(pc("sports_calendar"))))

    return pages_out


def _interval(cfg: dict, name: str) -> float:
    p    = config_loader.page_cfg(cfg, name)
    base = max(5, cfg["update_interval_seconds"])
    if p.get("critical_poll") and p.get("critical_poll_interval_seconds"):
        return float(p["critical_poll_interval_seconds"])
    slow = p.get("slow_poll_interval_seconds", 0)
    return float(slow) if slow else float(base)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    try:
        cfg = config_loader.load_config(CONFIG_PATH)
    except FileNotFoundError:
        log.error("config.yml not found at %s", CONFIG_PATH)
        sys.exit(1)

    enabled = config_loader.enabled_pages(cfg)
    pages   = _build_pages(cfg)
    names   = [n for n in enabled if n in pages]

    if not names:
        log.error("No pages are enabled in config.yml.")
        sys.exit(1)

    log.info("Enabled pages: %s", ", ".join(names))

    system.init_history(config_loader.page_cfg(cfg, "system"))

    # ── Serial ────────────────────────────────────────────────────────────────
    _serial_override = cfg.get("serial_port", "")
    sender           = None
    _last_port_scan  = 0.0
    _PORT_RETRY_S    = 5   # how often to scan for a newly plugged-in ESP32

    def _try_connect() -> "ss.SerialSender | None":
        port = ss.find_port(_serial_override)
        if not port:
            return None
        try:
            s = ss.SerialSender(port)
            log.info("ESP32 connected on %s", port)
            return s
        except Exception as exc:
            log.error("Serial port %s: %s", port, exc)
            return None

    sender = _try_connect()
    if sender:
        log.info("Sending startup clear.")
        sender.send(bytes([0xFF] * (648 * 480 // 8)))
    else:
        log.warning("ESP32 not detected — will keep scanning every %ds.", _PORT_RETRY_S)

    # ── Tray ──────────────────────────────────────────────────────────────────
    tray = ti.TrayIcon(config_path=CONFIG_PATH)
    tray.start()

    # ── State ─────────────────────────────────────────────────────────────────
    page_idx    = 0
    cache:  dict[str, bytes | None] = {n: None for n in names}
    updated:dict[str, float]        = {n: 0.0   for n in names}
    paused      = False

    def render_page(name: str) -> bytes | None:
        try:
            data = pages[name]()
            if data:
                cache[name] = data
            return data
        except Exception as exc:
            log.error("render %s: %s", name, exc)
            return cache.get(name)

    def push(name: str):
        nonlocal sender
        data = cache.get(name) or render_page(name)
        if data and sender:
            disconnected = False
            try:
                if not sender.send(data):
                    log.warning("push %s failed — marking ESP32 disconnected", name)
                    disconnected = True
            except Exception as exc:
                log.warning("push %s error — marking ESP32 disconnected: %s", name, exc)
                disconnected = True
            if disconnected:
                try:
                    sender.close()
                except Exception:
                    pass
                sender = None
        elif data:
            log.debug("render-only: %s OK", name)

    # Initial render + push of first page
    render_page(names[page_idx])
    updated[names[page_idx]] = time.time()
    push(names[page_idx])

    log.info("Running. Ctrl-C or tray Quit to stop.")
    while True:
        # ── Tray commands ─────────────────────────────────────────────────────
        try:
            cmd = tray.cmd_queue.get_nowait()
        except queue.Empty:
            cmd = None

        if   cmd == ti.CMD_CLEAR:
            if sender:
                sender.send(bytes([0xFF] * (648 * 480 // 8)))
        elif cmd == ti.CMD_QUIT:
            log.info("Quit — sending clear and waiting for panel refresh.")
            if sender:
                sender.send(bytes([0xFF] * (648 * 480 // 8)))
                time.sleep(4)   # wait for e-paper full refresh to complete
            break
        elif cmd == ti.CMD_PAUSE:
            paused = True
            tray.set_tooltip("E-Paper Dashboard (paused)")
        elif cmd == ti.CMD_RESUME:
            paused = False
            tray.set_tooltip("E-Paper Dashboard")
        elif cmd == ti.CMD_FORCE_REFRESH:
            render_page(names[page_idx])
            push(names[page_idx])
            updated[names[page_idx]] = time.time()

        if paused:
            time.sleep(1)
            continue

        # ── ESP32 hot-plug / reconnect ────────────────────────────────────────
        if sender is None:
            now_mono = time.monotonic()
            if now_mono - _last_port_scan >= _PORT_RETRY_S:
                _last_port_scan = now_mono
                sender = _try_connect()
                if sender:
                    # Push the current page immediately so the display isn't blank
                    push(names[page_idx])

        # ── Button press ──────────────────────────────────────────────────────
        if sender and sender.poll_button():
            page_idx = (page_idx + 1) % len(names)
            name     = names[page_idx]
            log.info("BTN → page %d (%s)", page_idx, name)
            if cache[name] is None:
                render_page(name)
                updated[name] = time.time()
            push(name)

        # ── Scheduled refresh ─────────────────────────────────────────────────
        now = time.time()
        for name in names:
            if now - updated[name] >= _interval(cfg, name):
                render_page(name)
                updated[name] = now
                is_current  = (name == names[page_idx])
                is_critical = config_loader.page_cfg(cfg, name).get("critical_poll", False)
                if is_current or is_critical:
                    push(name)

        time.sleep(1)

    tray.stop()
    if sender:
        sender.close()


if __name__ == "__main__":
    main()
