"""Clock & Status page."""

import socket
import time

import psutil

from ._base import PANEL_WIDTH, PANEL_HEIGHT, BLACK, load_font, load_bold, to_1bit, new_page
from .weather import draw_icon, _WI, _wmo_icon

_ft = _fd = _fi = _fw_icon = _fw_temp = _fw_sub = None


def _fonts():
    global _ft, _fd, _fi, _fw_icon, _fw_temp, _fw_sub
    if _ft is None:
        _ft      = load_font(120)
        _fd      = load_font(36)
        _fi      = load_font(22)
        _fw_temp = load_bold(68)
        _fw_sub  = load_font(22)


def _uptime() -> str:
    delta = int(time.time() - psutil.boot_time())
    h, r  = divmod(delta, 3600)
    return f"up {h // 24}d {h % 24}h" if h >= 24 else f"up {h}h {r // 60}m"


def _cx(draw, text, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return (PANEL_WIDTH - (bb[2] - bb[0])) // 2


def render(cfg: dict | None = None, weather: dict | None = None) -> bytes:
    _fonts()
    use_24h  = (cfg or {}).get("time_format_24h", False)
    now      = time.localtime()
    time_str = time.strftime("%H:%M" if use_24h else "%I:%M", now).lstrip("0") or "0:00"
    date_str = time.strftime("%A, %B ", now) + str(int(time.strftime("%d", now)))

    ampm_str = time.strftime("%p", now) if not use_24h else ""

    img, draw = new_page()

    # Fixed-width digit rendering so "1" and "4" are equally spaced from the colon
    ref_bb   = draw.textbbox((0, 0), "0", font=_ft)
    digit_w  = ref_bb[2] - ref_bb[0]   # reference width for all digits
    colon_bb = draw.textbbox((0, 0), ":", font=_ft)
    colon_w  = colon_bb[2] - colon_bb[0]
    time_h   = ref_bb[3] - ref_bb[1]
    time_y   = 60

    # Measure total width: each digit gets digit_w, colon gets colon_w
    def _char_w(ch):
        return digit_w if ch.isdigit() else colon_w

    total_time_w = sum(_char_w(ch) for ch in time_str)

    if ampm_str:
        ampm_bb = draw.textbbox((0, 0), ampm_str, font=_fd)
        ampm_w  = ampm_bb[2] - ampm_bb[0]
        ampm_h  = ampm_bb[3] - ampm_bb[1]
        block_w = total_time_w + 8 + ampm_w
    else:
        ampm_bb = ampm_w = ampm_h = None
        block_w = total_time_w

    start_x = (PANEL_WIDTH - block_w) // 2
    cx = start_x
    for ch in time_str:
        cbb = draw.textbbox((0, 0), ch, font=_ft)
        cw  = cbb[2] - cbb[0]
        ch_h = cbb[3] - cbb[1]
        cell = _char_w(ch)
        # Vertically center each glyph within the digit height, bearing-corrected
        y = time_y + (time_h - ch_h) // 2 - cbb[1]
        draw.text((cx + (cell - cw) // 2 - cbb[0], y), ch, font=_ft, fill=BLACK)
        cx += cell

    if ampm_str:
        draw.text((cx + 8, time_y + time_h - ampm_h - ampm_bb[1]), ampm_str, font=_fd, fill=BLACK)

    date_bb = draw.textbbox((0, 0), date_str, font=_fd)
    date_w  = date_bb[2] - date_bb[0]
    date_x  = (PANEL_WIDTH - date_w) // 2 - date_bb[0]
    date_y  = time_y + (ref_bb[3] - ref_bb[1]) + 16
    draw.text((date_x, date_y - date_bb[1]), date_str, font=_fd, fill=BLACK)

    div_y = date_y + (date_bb[3] - date_bb[1]) + 20
    draw.line([(40, div_y), (PANEL_WIDTH - 40, div_y)], fill=BLACK, width=2)

    host = socket.gethostname()
    up   = _uptime()
    hbb  = draw.textbbox((0, 0), host, font=_fi)
    ubb  = draw.textbbox((0, 0), up,   font=_fi)
    info_h = max(hbb[3] - hbb[1], ubb[3] - ubb[1])
    div2_y = div_y + info_h + 30   # band height

    # Vertically center text between the two divider lines
    row_y = div_y + (div2_y - div_y) // 2 - info_h // 2
    draw.text((48, row_y - hbb[1]),                                    host, font=_fi, fill=BLACK)
    draw.text((PANEL_WIDTH - 48 - (ubb[2] - ubb[0]), row_y - ubb[1]), up,   font=_fi, fill=BLACK)

    # ── Weather strip ─────────────────────────────────────────────────────────
    if weather:
        draw.line([(40, div2_y), (PANEL_WIDTH - 40, div2_y)], fill=BLACK, width=2)
        strip_y0 = div2_y + 12
        strip_h  = PANEL_HEIGHT - strip_y0 - 10
        icon_sz  = min(strip_h, 70)
        cx       = PANEL_WIDTH // 2   # center of whole strip

        # Measure all text first
        temp_str = f"{weather['temp_f']:.0f}"
        sfx_str  = "°F"
        cond_str = weather.get("condition", "")
        fl_str   = f"Feels Like {weather['feels_like_f']:.0f}°"

        tbb  = draw.textbbox((0, 0), temp_str, font=_fw_temp)
        sbb  = draw.textbbox((0, 0), sfx_str,  font=load_bold(20))
        cbb  = draw.textbbox((0, 0), cond_str, font=_fw_sub)
        fbb  = draw.textbbox((0, 0), fl_str,   font=_fw_sub)

        temp_h = tbb[3] - tbb[1]
        temp_w = (tbb[2] - tbb[0]) + (sbb[2] - sbb[0])
        cond_h = cbb[3] - cbb[1]
        fl_h   = fbb[3] - fbb[1]

        temp_row_w = (tbb[2] - tbb[0]) + (sbb[2] - sbb[0]) + 2
        text_col_w = max(temp_row_w, cbb[2] - cbb[0], fbb[2] - fbb[0])

        gap     = 20
        block_w = icon_sz + gap + text_col_w
        block_x = (PANEL_WIDTH - block_w) // 2

        icon_cx = block_x + icon_sz // 2
        icon_cy = strip_y0 + strip_h // 2
        draw_icon(draw, weather["icon"], icon_cx, icon_cy, icon_sz)

        # Center axis for the text column
        text_cx = block_x + icon_sz + gap + text_col_w // 2

        block_h = temp_h + 6 + cond_h + 4 + fl_h
        ty      = icon_cy - block_h // 2

        # Temp number centered on text_cx; °F suffix to the right of it
        temp_num_w = tbb[2] - tbb[0]
        tx = text_cx - temp_num_w // 2 - tbb[0]
        draw.text((tx, ty - tbb[1]), temp_str, font=_fw_temp, fill=BLACK)
        draw.text((tx + temp_num_w + 2, ty - tbb[1] + sbb[1] - 1), sfx_str, font=load_bold(20), fill=BLACK)

        # Clear and Feels Like centered on same text_cx as the temp number
        cy2 = ty + temp_h + 14
        draw.text((text_cx - (cbb[2] - cbb[0]) // 2 - cbb[0], cy2 - cbb[1]), cond_str, font=_fw_sub, fill=BLACK)

        cy3 = cy2 + cond_h + 10
        draw.text((text_cx - (fbb[2] - fbb[0]) // 2 - fbb[0], cy3 - fbb[1]), fl_str, font=_fw_sub, fill=BLACK)

    return to_1bit(img).tobytes()
