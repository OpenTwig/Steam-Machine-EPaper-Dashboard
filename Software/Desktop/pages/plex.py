"""Plex Now Playing page."""

import io
import logging

import requests
import urllib3
from PIL import Image, ImageFilter

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from ._base import PANEL_WIDTH, PANEL_HEIGHT, BLACK, load_font, load_bold, to_1bit, new_page, draw_bar

log = logging.getLogger(__name__)
_fh = _ft = _fs = _fi = _fx = _fb = None

_load_bold = load_bold

PAD     = 14
TITLE_H = 48


def _fonts():
    global _fh, _ft, _fs, _fi, _fx, _fb
    if _fh is None:
        _fh = _load_bold(42)   # page title
        _ft = load_font(34)    # media title (episode)
        _fb = _load_bold(38)   # show title — bold
        _fs = load_font(26)    # subtitle
        _fi = load_font(22)    # meta (year · player)
        _fx = load_font(20)    # progress %


# ── Data ──────────────────────────────────────────────────────────────────────

def _parse_session(s: dict, url: str, token: str) -> dict:
    duration = s.get("duration", 1)
    thumb    = s.get("grandparentThumb") or s.get("parentThumb") or s.get("thumb")
    return {
        "title":             s.get("title", "Unknown"),
        "grandparent_title": s.get("grandparentTitle", ""),
        "parent_index":      s.get("parentIndex"),    # season number
        "index":             s.get("index"),          # episode number
        "year":              str(s.get("year", "")),
        "player":            s.get("Player", {}).get("title", ""),
        "progress_pct":      round(s.get("viewOffset", 0) / max(duration, 1) * 100),
        "thumb_url":         f"{url}{thumb}?X-Plex-Token={token}" if thumb else None,
    }


def fetch(cfg: dict) -> dict | None:
    url, token = cfg.get("server_url", "").rstrip("/"), cfg.get("api_token", "")
    if not url or not token:
        return None
    try:
        # Get server name
        r_id = requests.get(f"{url}/",
                            params={"X-Plex-Token": token},
                            headers={"Accept": "application/json"}, timeout=5,
                            verify=False)
        r_id.raise_for_status()
        server_name = r_id.json().get("MediaContainer", {}).get("friendlyName", "Plex")

        r = requests.get(f"{url}/status/sessions",
                         params={"X-Plex-Token": token},
                         headers={"Accept": "application/json"}, timeout=5,
                         verify=False)
        r.raise_for_status()
        sessions = r.json().get("MediaContainer", {}).get("Metadata", [])
    except Exception as exc:
        log.warning("Plex: %s", exc)
        return None
    return {
        "server_name": server_name,
        "sessions":    [_parse_session(s, url, token) for s in sessions],
    }


# ── Render helpers ─────────────────────────────────────────────────────────────

def _fetch_thumb(url: str) -> Image.Image | None:
    try:
        r = requests.get(url, timeout=5, verify=False)
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("L")
    except Exception:
        return None


def _truncate(draw, text, font, max_w):
    if draw.textbbox((0, 0), text, font=font)[2] <= max_w:
        return text
    while text and draw.textbbox((0, 0), text + "…", font=font)[2] > max_w:
        text = text[:-1]
    return text + "…"


def _draw_session(img, draw, session: dict, y: int, h: int) -> None:
    THUMB_W = 180
    BAR_H   = 18
    BAR_GAP = 12

    # ── Thumbnail ─────────────────────────────────────────────────────────────
    tx = PAD
    if session.get("thumb_url"):
        thumb = _fetch_thumb(session["thumb_url"])
        if thumb:
            thumb = thumb.filter(ImageFilter.SHARPEN)
            thumb.thumbnail((THUMB_W, h - PAD * 2), Image.LANCZOS)
            thumb_1bit = thumb.point(lambda p: 255 if p > 140 else 0, "L")
            img.paste(thumb_1bit, (PAD, y + PAD))
            tx = PAD + thumb.width + PAD

    max_w = PANEL_WIDTH - PAD - tx
    cy    = y + PAD

    # ── Show title (grandparent) first, then episode title ────────────────────
    if session.get("grandparent_title"):
        sub = _truncate(draw, session["grandparent_title"], _fb, max_w)
        draw.text((tx, cy), sub, font=_fb, fill=BLACK)
        cy += draw.textbbox((0, 0), sub, font=_fb)[3] + 6

    # Episode: "S2 - E13  Redline"
    ep_parts = []
    if session.get("parent_index") and session.get("index"):
        ep_parts.append(f"S{session['parent_index']} - E{session['index']}")
    title = _truncate(draw, "  ".join(ep_parts + [session["title"]]) if ep_parts else session["title"], _fs, max_w)
    draw.text((tx, cy), title, font=_fs, fill=BLACK)
    cy += draw.textbbox((0, 0), title, font=_fs)[3] + 6

    # ── Meta: year · player ───────────────────────────────────────────────────
    meta = "  ·  ".join(p for p in [session.get("year", ""), session.get("player", "")] if p)
    if meta:
        draw.text((tx, cy), _truncate(draw, meta, _fi, max_w), font=_fi, fill=BLACK)
        cy += draw.textbbox((0, 0), meta, font=_fi)[3] + BAR_GAP

    # ── Progress bar with % to the right ──────────────────────────────────────
    pct       = session["progress_pct"]
    pct_label = f"{pct}%"
    pct_bb    = draw.textbbox((0, 0), pct_label, font=_fx)
    pct_w     = pct_bb[2] - pct_bb[0] + 8
    bar_x1    = PANEL_WIDTH - PAD - pct_w
    draw_bar(draw, (tx, cy, bar_x1, cy + BAR_H), pct / 100)
    draw.text((bar_x1 + 6, cy + (BAR_H - (pct_bb[3] - pct_bb[1])) // 2), pct_label, font=_fx, fill=BLACK)


def render(data: dict | None) -> bytes:
    _fonts()
    img, draw = new_page()

    server_name = (data or {}).get("server_name", "Plex")
    sessions    = (data or {}).get("sessions", [])

    # ── Title ─────────────────────────────────────────────────────────────────
    label = server_name
    bb    = draw.textbbox((0, 0), label, font=_fh)
    draw.text(((PANEL_WIDTH - (bb[2] - bb[0])) // 2, 2),
              label, font=_fh, fill=BLACK)
    draw.line([(0, TITLE_H), (PANEL_WIDTH, TITLE_H)], fill=BLACK, width=3)

    if not sessions:
        bb = draw.textbbox((0, 0), "Nothing playing", font=_fs)
        draw.text(((PANEL_WIDTH - (bb[2] - bb[0])) // 2,
                   TITLE_H + (PANEL_HEIGHT - TITLE_H) // 2 - 10),
                  "Nothing playing", font=_fs, fill=BLACK)
        return to_1bit(img).tobytes()

    # ── Sessions ──────────────────────────────────────────────────────────────
    sessions = sessions[:3]
    n        = len(sessions)
    slot_h   = (PANEL_HEIGHT - TITLE_H) // n

    for i, session in enumerate(sessions):
        y = TITLE_H + i * slot_h
        if i > 0:
            draw.line([(0, y), (PANEL_WIDTH, y)], fill=BLACK, width=2)
        _draw_session(img, draw, session, y, slot_h)

    return to_1bit(img).tobytes()
