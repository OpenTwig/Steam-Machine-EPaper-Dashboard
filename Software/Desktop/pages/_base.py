"""Shared drawing constants and helpers used by every page module."""

import logging
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

PANEL_WIDTH  = 648
PANEL_HEIGHT = 480
BLACK = 0
WHITE = 255

import os as _os
_FONTS_DIR = _os.path.join(_os.path.dirname(__file__), "..", "fonts")

_FONT_CANDIDATES = [
    _os.path.join(_FONTS_DIR, "RobotoMono-Regular.ttf"),
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

_BOLD_CANDIDATES = [
    _os.path.join(_FONTS_DIR, "RobotoMono-Bold.ttf"),
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def load_bold(size: int) -> ImageFont.ImageFont:
    for path in _BOLD_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return load_font(size)


def load_font(size: int) -> ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(path, size)
            log.debug("Loaded font %s @ %d", path, size)
            return font
        except OSError:
            continue
    log.warning("No usable font found for size %d — text will be tiny. "
                "Install Consolas or add a TTF path to _FONT_CANDIDATES.", size)
    return ImageFont.load_default()


def to_1bit(img: Image.Image, threshold: int = 128) -> Image.Image:
    return img.point(lambda p: 255 if p >= threshold else 0, mode="L").convert("1")


def new_page() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img  = Image.new("L", (PANEL_WIDTH, PANEL_HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)
    return img, draw


def draw_sparkline(draw, box, values, min_val=None, max_val=None, line_width=2):
    x0, y0, x1, y1 = box
    if len(values) < 2:
        return
    lo = min(values) if min_val is None else min_val
    hi = max(values) if max_val is None else max_val
    if hi - lo < 1e-6:
        # Flat line — center it by expanding range equally above and below
        margin = max(lo * 0.05, 0.5)
        lo -= margin
        hi += margin
    n   = len(values)
    pts = [
        (x0 + (i / (n - 1)) * (x1 - x0),
         y1 - ((v - lo) / (hi - lo)) * (y1 - y0))
        for i, v in enumerate(values)
    ]
    draw.line(pts, fill=BLACK, width=line_width)


def draw_bar(draw, box, fraction, border=1):
    x0, y0, x1, y1 = box
    draw.rectangle([x0, y0, x1, y1], outline=BLACK, width=border)
    inner_w = max(0, int((x1 - x0 - 2 * border) * max(0.0, min(1.0, fraction))))
    if inner_w:
        draw.rectangle([x0 + border, y0 + border,
                        x0 + border + inner_w, y1 - border], fill=BLACK)


def text_right(draw, x_right, y, text, font, fill=BLACK):
    bb = draw.textbbox((0, 0), text, font=font)
    draw.text((x_right - (bb[2] - bb[0]), y), text, font=font, fill=fill)


def wrap_text(draw, text, font, max_width, x, y, line_spacing=6):
    words, line, cur_y = text.split(), "", y
    for word in words:
        test = (line + " " + word).strip()
        bb   = draw.textbbox((0, 0), test, font=font)
        if bb[2] - bb[0] > max_width and line:
            draw.text((x, cur_y), line, font=font, fill=BLACK)
            cur_y += (bb[3] - bb[1]) + line_spacing
            line   = word
        else:
            line = test
    if line:
        bb = draw.textbbox((0, 0), line, font=font)
        draw.text((x, cur_y), line, font=font, fill=BLACK)
        cur_y += (bb[3] - bb[1]) + line_spacing
    return cur_y
