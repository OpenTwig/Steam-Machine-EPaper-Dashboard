"""
Sports page — Major Events only.

Uses ESPN's public undocumented API (no key required).

Automatically detects which major sporting event is currently active and
renders a bracket / standings grid for it.

Currently supported events (checked in priority order):
  • FIFA Club World Cup 2025
  • FIFA World Cup 2026 (when active)

The bracket grid adapts to the current round automatically:
  Group stage     → skipped (too many games)
  Round of 16     → 8  match cells (2 col × 4 row)
  Quarter-finals  → 4  cells       (2 col × 2 row)
  Semi-finals     → 2  cells       (1 col × 2 row)
  Final           → 1  cell        (full width)
"""

import logging
import time
from datetime import datetime, timedelta

import requests

from ._base import (
    PANEL_WIDTH, PANEL_HEIGHT, BLACK,
    load_font, load_bold, to_1bit, new_page,
)

log = logging.getLogger(__name__)

_ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"

# ── Font cache ────────────────────────────────────────────────────────────────
_fh = _fb = _ft = _fs = _fx = None

_load_bold = load_bold


def _fonts():
    global _fh, _fb, _ft, _fs, _fx
    if _fh is None:
        _fh = _load_bold(38)   # page title
        _fb = _load_bold(22)   # match team names
        _ft = load_font(22)    # date/score in cells
        _fs = load_font(16)    # time / small labels
        _fx = load_font(13)    # tiny


# ── Major event definitions — loaded from config.yml ─────────────────────────
# Fallback used only if config has no events list.
_DEFAULT_EVENTS = [
    {"slug": "fifa.world", "dates": "20260611-20260719", "label": "FIFA World Cup 2026"},
    {"slug": "fifa.cwc",   "dates": "20250614-20250714", "label": "Club World Cup 2025"},
]

# Slug → display label, in the order we prefer to show them
_STAGE_ORDER = [
    "group-stage",
    "round-of-32",
    "round-of-16",
    "quarterfinals",
    "semifinals",
    "third-place",
    "final",
]

_STAGE_LABEL = {
    "group-stage":  "Group Stage",
    "round-of-32":  "Round of 32",
    "round-of-16":  "Round of 16",
    "quarterfinals": "Quarter-Finals",
    "semifinals":   "Semi-Finals",
    "third-place":  "3rd Place",
    "final":        "Final",
}



# ── API helpers ───────────────────────────────────────────────────────────────

def _espn_scoreboard(slug: str, dates: str) -> list[dict] | None:
    try:
        r = requests.get(
            f"{_ESPN_BASE}/{slug}/scoreboard",
            params={"dates": dates, "limit": 100},
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("events", [])
    except Exception as exc:
        log.warning("ESPN %s: %s", slug, exc)
        return None


def _parse_espn_event(event: dict, week_key: str = "") -> dict:
    """Parse one ESPN event into our internal match dict."""
    comp       = event["competitions"][0]
    status_obj = comp["status"]["type"]
    finished   = status_obj.get("completed", False)
    live       = status_obj.get("state") == "in"

    home = next((c for c in comp["competitors"] if c.get("homeAway") == "home"), comp["competitors"][0])
    away = next((c for c in comp["competitors"] if c.get("homeAway") == "away"), comp["competitors"][1])

    def _name(c):
        t = c.get("team", {})
        return t.get("abbreviation") or t.get("shortDisplayName") or t.get("displayName", "?")

    def _score(c):
        s = c.get("score")
        return int(s) if s is not None and s != "" else None

    kickoff = None
    try:
        kickoff = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
    except Exception:
        pass

    return {
        "stage":      week_key or event.get("season", {}).get("slug", ""),
        "home":       _name(home),
        "away":       _name(away),
        "home_goals": _score(home) if finished or live else None,
        "away_goals": _score(away) if finished or live else None,
        "finished":   finished,
        "live":       live,
        "kickoff":    kickoff,
    }


# ── Soccer fetch ──────────────────────────────────────────────────────────────

def _fetch_soccer(slug: str, dates: str) -> tuple[str, list[dict]] | tuple[None, None]:
    events = _espn_scoreboard(slug, dates)
    if not events:
        return None, None

    matches = [_parse_espn_event(e) for e in events]

    by_stage: dict[str, list] = {}
    for m in matches:
        by_stage.setdefault(m["stage"], []).append(m)

    def _stage_rank(s):
        try:
            return _STAGE_ORDER.index(s)
        except ValueError:
            return -1

    knockout_start = _STAGE_ORDER.index("round-of-32")

    current_stage = None
    for stage in sorted(by_stage.keys(), key=_stage_rank):
        if _stage_rank(stage) < knockout_start:
            continue
        if not all(m["finished"] for m in by_stage[stage]):
            current_stage = stage
            break

    if current_stage is None:
        knockout_stages = sorted(
            [s for s in by_stage if _stage_rank(s) >= knockout_start],
            key=_stage_rank, reverse=True
        )
        if not knockout_stages:
            return None, None
        recap = []
        for s in knockout_stages:
            recap.extend(by_stage[s])
            if len(recap) >= 3:
                break
        label = _STAGE_LABEL.get(knockout_stages[0], "Results")
        return f"{label} — Results", recap

    label = _STAGE_LABEL.get(current_stage, current_stage.replace("-", " ").title())
    return label, by_stage[current_stage]


# ── NFL fetch ─────────────────────────────────────────────────────────────────

_NFL_WEEK_LABEL = {
    1: "Wild Card",
    2: "Divisional Round",
    3: "Conference Championships",
    5: "Super Bowl",
}

# Super Bowl number by season start year (add future years as needed)
_SUPER_BOWL_NUMBER = {
    2024: "LIX",
    2025: "LX",
    2026: "LXI",
    2027: "LXII",
    2028: "LXIII",
}

def _fetch_nfl(dates: str) -> tuple[str, list[dict]] | tuple[None, None]:
    try:
        r = requests.get(
            f"{_ESPN_BASE.replace('/soccer', '/football')}/nfl/scoreboard",
            params={"dates": dates, "seasontype": 3, "limit": 100},
            timeout=10,
        )
        r.raise_for_status()
        data   = r.json()
        events = data.get("events", [])
    except Exception as exc:
        log.warning("ESPN NFL: %s", exc)
        return None, None

    if not events:
        return None, None

    # Detect season year from first event
    season_year = events[0].get("season", {}).get("year", 0) - 1  # season year is end year
    sb_num      = _SUPER_BOWL_NUMBER.get(season_year, "")

    # Group by week number, skip Pro Bowl (week 4)
    by_week: dict[int, list] = {}
    for e in events:
        week = e.get("week", {}).get("number", 0)
        if week == 4:
            continue
        by_week.setdefault(week, []).append(
            _parse_espn_event(e, week_key=str(week))
        )

    if not by_week:
        return None, None

    # Show earliest week with incomplete games
    current_week = None
    for week in sorted(by_week.keys()):
        if not all(m["finished"] for m in by_week[week]):
            current_week = week
            break

    if current_week is None:
        # All done — show Super Bowl result only
        current_week = max(by_week.keys())
        base = _NFL_WEEK_LABEL.get(current_week, "Super Bowl")
        if sb_num and current_week == 5:
            label = f"Super Bowl {sb_num} — Results"
        else:
            label = f"{base} — Results"
        return label, by_week[current_week]

    base = _NFL_WEEK_LABEL.get(current_week, f"Playoffs Week {current_week}")
    if sb_num and current_week == 5:
        label = f"Super Bowl {sb_num}"
    else:
        label = base
    return label, by_week[current_week]


# ── Cache ─────────────────────────────────────────────────────────────────────

_cache: dict | None = None          # full tournament data
_cache_full_time: float = 0.0       # last full fetch
_cache_today_time: float = 0.0      # last today-only fetch
_FULL_S  = 24 * 60 * 60            # full tournament fetch: once per day
_TODAY_S = 15 * 60                  # today-only fetch: every 15 min during game window


def _active_window(fixtures: list[dict]) -> bool:
    """Return True if now is between the first kickoff and last kickoff + 2h today."""
    today = datetime.now().astimezone().date()
    now   = datetime.now().astimezone()
    times = [
        m["kickoff"].astimezone() for m in fixtures
        if not m["finished"] and m["kickoff"] is not None
        and m["kickoff"].astimezone().date() == today
    ]
    if not times:
        return False
    return min(times) <= now <= max(times) + timedelta(hours=2)


def _today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


# ── Main fetch ────────────────────────────────────────────────────────────────

def _event_active(event: dict) -> bool:
    """Return True if today falls within the event's date range (YYYYMMDD-YYYYMMDD)."""
    today = datetime.now().strftime("%Y%m%d")
    parts = event.get("dates", "").split("-")
    if len(parts) != 2:
        return False
    return parts[0] <= today <= parts[1]


def fetch(cfg: dict) -> dict | None:
    global _cache, _cache_full_time, _cache_today_time

    now_ts    = time.time()
    in_window = _cache is not None and _active_window(_cache.get("fixtures", []))

    # Full tournament fetch: once per day, or on first run
    if _cache is None or now_ts - _cache_full_time >= _FULL_S:
        events = cfg.get("events", _DEFAULT_EVENTS)
        for event in events:
            if not _event_active(event):
                continue
            if event.get("sport") == "nfl":
                stage_label, fixtures = _fetch_nfl(event["dates"])
            else:
                stage_label, fixtures = _fetch_soccer(event["slug"], event["dates"])
            if fixtures:
                _cache           = {"label": event["label"], "round": stage_label, "fixtures": fixtures}
                _cache_full_time = now_ts
                _cache_today_time = now_ts
                return _cache
        return _cache

    # Today-only fetch: every 15 min during active game window to catch scores
    if in_window and now_ts - _cache_today_time >= _TODAY_S:
        today = _today_str()
        events = cfg.get("events", _DEFAULT_EVENTS)
        for event in events:
            if not _event_active(event):
                continue
            if event.get("sport") == "nfl":
                stage_label, fixtures = _fetch_nfl(today)
            else:
                stage_label, fixtures = _fetch_soccer(event["slug"], today)
            if fixtures:
                # Merge today's results into the cached full fixture list
                today_by_ko = {m["kickoff"]: m for m in fixtures if m["kickoff"]}
                merged = [today_by_ko.get(m["kickoff"], m) for m in _cache["fixtures"]]
                _cache["fixtures"]  = merged
                _cache_today_time   = now_ts
                return _cache
        _cache_today_time = now_ts  # avoid hammering on failure

    return _cache


# ── Render helpers ────────────────────────────────────────────────────────────

PAD     = 10
TITLE_H = 62   # height reserved for header + round label + divider line

def _fmt_kickoff(dt: datetime | None) -> str:
    if dt is None:
        return "TBD"
    local = dt.astimezone()
    date = local.strftime("%m/%d").lstrip("0")
    time = local.strftime("%I:%M%p").lstrip("0").lower()
    return f"{date} - {time}"


def _draw_match_cell(draw, box: tuple, match: dict) -> None:
    """Draw one match cell using a 3-column grid: [HOME][MID][AWAY]."""
    x0, y0, x1, y1 = box
    draw.rectangle([x0, y0, x1, y1], outline=BLACK, width=1)

    cell_h = y1 - y0
    cell_w = x1 - x0

    # Three columns: outer cols share 40% each, middle 20% — gives visible gap
    mid_w  = int(cell_w * 0.22)
    side_w = (cell_w - mid_w) // 2
    cols   = [
        (x0,                     x0 + side_w),           # home
        (x0 + side_w,            x0 + side_w + mid_w),   # mid
        (x0 + side_w + mid_w,    x1),                    # away
    ]

    now = datetime.now().astimezone()
    if match["finished"]:
        mid_text = f"{match['home_goals']} - {match['away_goals']}"
        mid_bold = True
        sub_text = None
    else:
        ko      = match["kickoff"].astimezone() if match["kickoff"] else None
        is_live = ko is not None and now >= ko
        if is_live:
            mid_text = "● LIVE"
            mid_bold = True
            sub_text = None
        else:
            mid_text = "vs"
            mid_bold = False
            sub_text = _fmt_kickoff(match["kickoff"])

    def _fit_in_col(text: str, cx0: int, cx1: int, bold: bool, target_h: int) -> tuple:
        """Find largest font that fits text within the column width."""
        max_w = (cx1 - cx0) - 8
        size  = target_h
        while size > 6:
            f  = _load_bold(size) if bold else load_font(size)
            bb = draw.textbbox((0, 0), text, font=f)
            if (bb[2] - bb[0]) <= max_w:
                return f, bb
            size -= 1
        f  = load_font(6)
        bb = draw.textbbox((0, 0), text, font=f)
        return f, bb

    target_h = min(int(cell_h * 0.45), 72)

    home_f, home_bb = _fit_in_col(match["home"], *cols[0], bold=True,     target_h=target_h)
    mid_f,  mid_bb  = _fit_in_col(mid_text,      *cols[1], bold=mid_bold, target_h=target_h)
    away_f, away_bb = _fit_in_col(match["away"], *cols[2], bold=True,     target_h=target_h)

    row_h = max(home_bb[3]-home_bb[1], mid_bb[3]-mid_bb[1], away_bb[3]-away_bb[1])

    if sub_text:
        sub_f, sub_bb = _fit_in_col(sub_text, x0, x1, bold=False, target_h=int(cell_h * 0.42))
        GAP     = max(4, int(cell_h * 0.05))
        block_h = row_h + GAP + (sub_bb[3] - sub_bb[1])
    else:
        sub_bb = sub_f = None
        GAP    = 0
        block_h = row_h

    top_y = y0 + (cell_h - block_h) // 2

    def _draw_centered(text, font, bb, cx0, cx1, ty):
        tw = bb[2] - bb[0]
        th = bb[3] - bb[1]
        draw.text((cx0 + ((cx1 - cx0) - tw) // 2, ty + (row_h - th) // 2 - bb[1]), text, font=font, fill=BLACK)

    _draw_centered(match["home"], home_f, home_bb, *cols[0], top_y)
    _draw_centered(mid_text,      mid_f,  mid_bb,  *cols[1], top_y)
    _draw_centered(match["away"], away_f, away_bb, *cols[2], top_y)

    if sub_text and sub_bb and sub_f:
        sw = sub_bb[2] - sub_bb[0]
        draw.text((x0 + (cell_w - sw) // 2, top_y + row_h + GAP), sub_text, font=sub_f, fill=BLACK)


def render(data: dict | None) -> bytes:
    _fonts()
    img, draw = new_page()

    # ── Header ────────────────────────────────────────────────────────────────
    if data:
        label = data["label"]
        rnd   = data["round"]
        title = label
    else:
        title = "Sports"
        rnd   = ""

    title_bb = draw.textbbox((0, 0), title, font=_fh)
    draw.text(((PANEL_WIDTH - (title_bb[2] - title_bb[0])) // 2, 4),
              title, font=_fh, fill=BLACK)

    if rnd:
        rnd_bb = draw.textbbox((0, 0), rnd, font=_fs)
        draw.text(((PANEL_WIDTH - (rnd_bb[2] - rnd_bb[0])) // 2, 4 + (title_bb[3]-title_bb[1]) + 6),
                  rnd, font=_fs, fill=BLACK)

    draw.line([(0, TITLE_H), (PANEL_WIDTH, TITLE_H)], fill=BLACK, width=2)

    # ── No data ───────────────────────────────────────────────────────────────
    if not data or not data.get("fixtures"):
        msg = "No active major sporting event." if not data else "No fixtures found."
        bb  = draw.textbbox((0, 0), msg, font=_ft)
        draw.text(((PANEL_WIDTH - (bb[2]-bb[0])) // 2,
                   TITLE_H + (PANEL_HEIGHT - TITLE_H) // 2 - 10),
                  msg, font=_ft, fill=BLACK)
        return to_1bit(img).tobytes()

    # ── Grid layout ───────────────────────────────────────────────────────────
    fixtures = data["fixtures"]
    n        = len(fixtures)

    # Determine grid columns based on match count
    if n <= 1:
        cols = 1
    elif n <= 4:
        cols = 2
    else:
        cols = 2   # always 2 columns; rows expand as needed

    rows    = (n + cols - 1) // cols
    grid_h  = PANEL_HEIGHT - TITLE_H - PAD
    grid_w  = PANEL_WIDTH - 2 * PAD
    cell_w  = grid_w // cols
    cell_h  = grid_h // rows

    for i, match in enumerate(fixtures):
        col = i % cols
        row = i // cols
        x0  = PAD + col * cell_w
        y0  = TITLE_H + PAD // 2 + row * cell_h
        _draw_match_cell(draw, (x0, y0, x0 + cell_w - 2, y0 + cell_h - 2), match)

    return to_1bit(img).tobytes()
