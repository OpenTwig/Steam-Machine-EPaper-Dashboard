"""
tray_icon.py — system tray icon via pystray.

Runs in its own daemon thread.  Communicates with main.py via a
threading.Event and a queue.Queue so no shared mutable state is needed.

Menu items:
    Force Refresh   — re-render and push the current page immediately
    Pause / Resume  — suspend/resume all scheduled updates
    Open Config     — open config.yml in the system default editor
    Quit            — signal the main loop to exit cleanly
"""

import os
import queue
import subprocess
import sys
import threading

import pystray
from PIL import Image as PILImage, ImageDraw

# Command tokens pushed onto the command queue
CMD_FORCE_REFRESH = "force_refresh"
CMD_PAUSE         = "pause"
CMD_RESUME        = "resume"
CMD_CLEAR         = "clear"
CMD_QUIT          = "quit"


def _make_icon_image(size: int = 64) -> PILImage.Image:
    """Draw a minimal monochrome icon: white square with a black border + dot."""
    img  = PILImage.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    m    = size // 8
    draw.rectangle([m, m, size - m, size - m], outline=(0, 0, 0), width=m)
    # Small filled circle in the centre to hint "display"
    r = size // 5
    cx = size // 2
    draw.ellipse([cx - r, cx - r, cx + r, cx + r], fill=(0, 0, 0))
    return img


def _open_config(config_path: str):
    """Open config.yml in Notepad (Windows) or the system default editor."""
    if sys.platform.startswith("win"):
        subprocess.Popen(["notepad.exe", config_path],
                         creationflags=subprocess.CREATE_NO_WINDOW)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", config_path])
    else:
        editor = os.environ.get("EDITOR", "xdg-open")
        subprocess.Popen([editor, config_path])


class TrayIcon:
    """
    Wraps a pystray icon; exposes a thread-safe command queue to main.py.

    Usage:
        tray = TrayIcon(config_path="/path/to/config.yml")
        tray.start()                   # spawns daemon thread
        cmd = tray.cmd_queue.get()     # main loop blocks here
    """

    def __init__(self, config_path: str = "config.yml"):
        self.config_path = config_path
        self.cmd_queue   = queue.Queue()
        self._paused     = False
        self._icon       = None
        self._thread     = None

    def start(self):
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="tray-icon"
        )
        self._thread.start()

    def stop(self):
        if self._icon:
            self._icon.stop()

    def set_tooltip(self, text: str):
        if self._icon:
            self._icon.title = text

    # ── Internal ──────────────────────────────────────────────────────────────

    def _push(self, cmd: str):
        self.cmd_queue.put(cmd)

    def _toggle_pause(self):
        self._paused = not self._paused
        cmd = CMD_PAUSE if self._paused else CMD_RESUME
        self._push(cmd)
        # Rebuild the menu so the label flips
        if self._icon:
            self._icon.menu = self._build_menu()

    def _build_menu(self):
        pause_label = "Resume" if self._paused else "Pause"
        return pystray.Menu(
            pystray.MenuItem(
                "E-Paper Dashboard", None, enabled=False
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Force Refresh",
                lambda _icon, _item: self._push(CMD_FORCE_REFRESH),
            ),
            pystray.MenuItem(
                "Clear Display",
                lambda _icon, _item: self._push(CMD_CLEAR),
            ),
            pystray.MenuItem(
                pause_label,
                lambda _icon, _item: self._toggle_pause(),
            ),
            pystray.MenuItem(
                "Open Config",
                lambda _icon, _item: _open_config(self.config_path),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Quit",
                lambda _icon, _item: self._push(CMD_QUIT),
            ),
        )

    def _run(self):
        self._icon = pystray.Icon(
            name    = "epaper-dashboard",
            icon    = _make_icon_image(),
            title   = "E-Paper Dashboard",
            menu    = self._build_menu(),
        )
        self._icon.run()
