"""Daily Fact page — local folder of .txt files with optional paired images."""

import logging
import os
import time

from PIL import Image

from ._base import PANEL_WIDTH, PANEL_HEIGHT, BLACK, load_font, to_1bit, new_page, wrap_text

log = logging.getLogger(__name__)
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp")

_fb = _fs = None


def _fonts():
    global _fb, _fs
    if _fb is None:
        _fb = load_font(22)
        _fs = load_font(18)


# ── Data ──────────────────────────────────────────────────────────────────────

def fetch(cfg: dict) -> dict | None:
    folder = cfg.get("facts_folder", "")
    if not folder or not os.path.isdir(folder):
        return None
    facts = []
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".txt"):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                text = fh.read().strip()
        except OSError:
            continue
        base     = os.path.splitext(name)[0]
        img_path = next(
            (os.path.join(folder, base + ext) for ext in _IMAGE_EXTS
             if os.path.exists(os.path.join(folder, base + ext))), None)
        facts.append({"text": text, "image_path": img_path})
    if not facts:
        return None
    return facts[time.localtime().tm_yday % len(facts)]


# ── Render ────────────────────────────────────────────────────────────────────

def render(data: dict | None) -> bytes:
    _fonts()
    img, draw = new_page()

    if data is None:
        draw.text((36, PANEL_HEIGHT // 2 - 20),
                  "No facts configured. Add .txt files to facts_folder in config.yml.",
                  font=_fs, fill=BLACK)
        return to_1bit(img).tobytes()

    text       = data.get("text", "")
    image_path = data.get("image_path")

    if image_path:
        half_w, half_h = PANEL_WIDTH // 2 - 16, PANEL_HEIGHT - 32
        try:
            src = Image.open(image_path).convert("L")
            src.thumbnail((half_w, half_h), Image.LANCZOS)
            img.paste(src, ((half_w - src.width) // 2 + 8,
                            (half_h - src.height) // 2 + 16))
        except OSError:
            pass
        draw.line([(PANEL_WIDTH // 2, 16), (PANEL_WIDTH // 2, PANEL_HEIGHT - 16)],
                  fill=BLACK, width=2)
        wrap_text(draw, text, _fb, PANEL_WIDTH // 2 - 24, PANEL_WIDTH // 2 + 12, 24)
    else:
        wrap_text(draw, text, _fb, PANEL_WIDTH - 72, 36, 36)

    return to_1bit(img).tobytes()
