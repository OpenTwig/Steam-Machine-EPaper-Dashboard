"""Weather page — Open-Meteo (free, no key) via IP geolocation."""

import logging
import os
import time

import requests

from ._base import (
    PANEL_WIDTH, PANEL_HEIGHT, BLACK, WHITE,
    load_font, load_bold, to_1bit, new_page,
)

log = logging.getLogger(__name__)

_WMO_LABEL = {
    0: "Clear", 1: "Mainly Clear", 2: "Partly Cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy Fog",
    51: "Lt Drizzle", 53: "Drizzle", 55: "Hvy Drizzle",
    61: "Lt Rain", 63: "Rain", 65: "Hvy Rain",
    71: "Lt Snow", 73: "Snow", 75: "Hvy Snow", 77: "Snow Grains",
    80: "Lt Showers", 81: "Showers", 82: "Hvy Showers",
    85: "Snow Showers", 86: "Hvy Snow Shwrs",
    95: "Thunderstorm", 96: "Thunder+Hail", 99: "Hvy Thunder",
}

# Weather Icons font codepoints (weathericons-regular-webfont.ttf)
# CSS uses \fXXX which maps to Unicode private use area 0xF000+
_WI = {
    "sunny":       "",  # wi-day-sunny
    "partly":      "",  # wi-day-cloudy
    "cloudy":      "",  # wi-cloudy
    "fog":         "",  # wi-fog
    "rain":        "",  # wi-rain
    "showers":     "",  # wi-showers
    "snow":        "",  # wi-snow
    "thunder":     "",  # wi-thunderstorm
    "sunrise":     "",  # wi-sunrise
    "sunset":      "",  # wi-sunset
    "wind":        "",  # wi-strong-wind
    "humidity":    "",  # wi-humidity
    "uv":          "",  # wi-day-sunny (UV proxy)
    "aqi":         "",  # wi-smoke
}

def _wmo_icon(code: int) -> str:
    if code == 0:               return _WI["sunny"]
    if code in (1, 2):          return _WI["partly"]
    if code == 3:               return _WI["cloudy"]
    if code in (45, 48):        return _WI["fog"]
    if code in range(51, 68):   return _WI["rain"]
    if code in range(71, 78):   return _WI["snow"]
    if code in range(80, 83):   return _WI["showers"]
    if code in (85, 86):        return _WI["snow"]
    if code >= 95:              return _WI["thunder"]
    return _WI["sunny"]

# ── Icon drawing ──────────────────────────────────────────────────────────────

_WI_FONT_PATH = os.path.join(os.path.dirname(__file__), "..", "fonts", "weathericons.ttf")
_wi_cache: dict[int, object] = {}

def _wi_font(size: int):
    if size not in _wi_cache:
        from PIL import ImageFont
        try:
            _wi_cache[size] = ImageFont.truetype(_WI_FONT_PATH, size)
        except OSError:
            log.warning("Weather icon font not found at %s", _WI_FONT_PATH)
            _wi_cache[size] = ImageFont.load_default()
    return _wi_cache[size]


def draw_icon(draw, symbol: str, cx: int, cy: int, size: int):
    """Draw a weather icon glyph centered at (cx, cy)."""
    f  = _wi_font(size)
    bb = draw.textbbox((0, 0), symbol, font=f)
    w  = bb[2] - bb[0]
    h  = bb[3] - bb[1]
    draw.text((cx - w // 2 - bb[0], cy - h // 2 - bb[1]), symbol, font=f, fill=BLACK)


# ── Data fetch ────────────────────────────────────────────────────────────────

_cache: dict | None = None
_cache_time: float  = 0.0
_CACHE_TTL  = 600   # seconds — matches slow_poll_interval_seconds in config

def _c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def _location(override: str) -> tuple[float, float, str]:
    if override:
        lat, lon = override.split(",")
        return float(lat), float(lon), override
    d = requests.get("https://ipinfo.io/json", timeout=5).json()
    lat, lon = d["loc"].split(",")
    name = ", ".join(p for p in [d.get("city"), d.get("region")] if p)
    return float(lat), float(lon), name


_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def fetch(cfg: dict, force: bool = False) -> dict | None:
    global _cache, _cache_time
    if not force and _cache is not None and (time.time() - _cache_time) < _CACHE_TTL:
        return _cache
    try:
        lat, lon, loc = _location(cfg.get("location_override", ""))
    except Exception as exc:
        log.warning("Location lookup failed: %s", exc)
        return None
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=10, params={
            "latitude":  lat, "longitude": lon, "timezone": "auto",
            "current":   "temperature_2m,apparent_temperature,weathercode,windspeed_10m,relativehumidity_2m,uv_index",
            "hourly":    "temperature_2m,weathercode",
            "daily":     "weathercode,temperature_2m_max,temperature_2m_min,sunrise,sunset",
            "forecast_days": 7,
            "temperature_unit": "celsius",
        })
        r.raise_for_status()
        d = r.json()
    except Exception as exc:
        log.warning("Weather fetch failed: %s", exc)
        return None

    # AQI from Open-Meteo air quality API
    aqi_val = None
    try:
        ra = requests.get("https://air-quality-api.open-meteo.com/v1/air-quality", timeout=5, params={
            "latitude": lat, "longitude": lon, "current": "us_aqi",
        })
        ra.raise_for_status()
        aqi_val = ra.json()["current"].get("us_aqi")
    except Exception:
        pass

    def _aqi_label(v):
        if v is None: return "N/A"
        if v <= 50:   return f"{v:.0f} Good"
        if v <= 100:  return f"{v:.0f} Mod"
        if v <= 150:  return f"{v:.0f} Unhealthy"
        return f"{v:.0f} Poor"

    cur  = d["current"]
    hrly = d["hourly"]
    dly  = d["daily"]

    # Hourly: current hour + next 6 hours (7 points)
    import datetime
    today    = time.strftime("%Y-%m-%d")
    now_h    = time.localtime().tm_hour
    hourly   = []
    for i, t in enumerate(hrly["time"]):
        date_str, hour_str = t.split("T")
        h = int(hour_str[:2])
        if date_str == today and h == now_h:
            # Found current hour — collect next 7 points
            for j in range(5):
                if i + j < len(hrly["time"]):
                    dt, hs = hrly["time"][i + j].split("T")
                    hourly.append({
                        "hour":     int(hs[:2]),
                        "date_str": dt,
                        "temp_f":   _c_to_f(hrly["temperature_2m"][i + j]),
                        "icon":     _wmo_icon(hrly["weathercode"][i + j]),
                    })
            break

    # Daily: 5 days
    daily = []
    for i, date in enumerate(dly["time"]):
        dt = datetime.date.fromisoformat(date)
        label = _DAY_NAMES[dt.weekday()]
        daily.append({
            "label":  label,
            "icon":   _wmo_icon(dly["weathercode"][i]),
            "hi_f":   _c_to_f(dly["temperature_2m_max"][i]),
            "lo_f":   _c_to_f(dly["temperature_2m_min"][i]),
        })

    # Parse today's sunrise/sunset (first daily entry)
    def _fmt_time(iso: str) -> str:
        h, m = int(iso[11:13]), int(iso[14:16])
        sfx = "am" if h < 12 else "pm"
        return f"{h % 12 or 12}:{m:02d}{sfx}"

    sunrise_str = _fmt_time(dly["sunrise"][0]) if dly.get("sunrise") else "—"
    sunset_str  = _fmt_time(dly["sunset"][0])  if dly.get("sunset")  else "—"

    wind_mph = cur.get("windspeed_10m", 0) * 0.621371  # km/h → mph
    humidity = cur.get("relativehumidity_2m", 0)
    uv_index = cur.get("uv_index", 0)

    _cache = {
        "location":    loc,
        "temp_f":      _c_to_f(cur["temperature_2m"]),
        "feels_like_f": _c_to_f(cur["apparent_temperature"]),
        "condition":   _WMO_LABEL.get(cur["weathercode"], "Unknown"),
        "icon":        _wmo_icon(cur["weathercode"]),
        "hourly":      hourly,
        "daily":       daily,
        "sunrise":     sunrise_str,
        "sunset":      sunset_str,
        "wind_mph":    wind_mph,
        "humidity":    humidity,
        "uv_index":    uv_index,
        "aqi":         _aqi_label(aqi_val),
    }
    _cache_time = time.time()
    return _cache


def _split_ampm(t: str) -> tuple[str, str]:
    """Split '5:21am' → ('5:21', 'am')."""
    for sfx in ("am", "pm"):
        if t.endswith(sfx):
            return t[:-2], sfx
    return t, ""


# ── Render ────────────────────────────────────────────────────────────────────

_MARGIN   = 10
_DIV_X    = PANEL_WIDTH * 4 // 12   # vertical divider between current and forecast

def render(data: dict | None) -> bytes:
    img, draw = new_page()

    if data is None:
        f = load_font(22)
        draw.text((20, PANEL_HEIGHT // 2 - 20), "Weather unavailable", font=f, fill=BLACK)
        return to_1bit(img).tobytes()

    # ── Layout: 2×2 grid ─────────────────────────────────────────────────────
    # DIV_X: vertical divider (left panel = current, right panel = forecast/graph)
    # DIV_Y: horizontal divider (top row = current+forecast, bottom row = details+graph)
    DIV_Y    = PANEL_HEIGHT * 9 // 20    # ~45% down
    DIV_X    = _DIV_X                    # already defined as PANEL_WIDTH * 4 // 12

    # quadrant bounds (inner — margin applied per-cell)
    q_tl = (_MARGIN,       _MARGIN,       DIV_X - 6,         DIV_Y - 6)   # top-left
    q_tr = (DIV_X + 6,     _MARGIN,       PANEL_WIDTH - _MARGIN, DIV_Y - 6)  # top-right
    q_bl = (_MARGIN,       DIV_Y + 6,     DIV_X - 6,         PANEL_HEIGHT - _MARGIN)  # bottom-left
    q_br = (DIV_X + 6,     DIV_Y + 6,     PANEL_WIDTH - _MARGIN, PANEL_HEIGHT - _MARGIN)  # bottom-right


    # ── Q1 Top-left: current conditions ──────────────────────────────────────
    tl_x0, tl_y0, tl_x1, tl_y1 = q_tl
    tl_cx = (tl_x0 + tl_x1) // 2

    temp_num = f"{data['temp_f']:.0f}"
    temp_sfx = "°F"
    tf_large = load_bold(64)
    tf_sfx   = load_bold(28)
    tf_cond  = load_font(22)
    tf_sub   = load_font(19)
    fl_str   = f"Feels Like {data['feels_like_f']:.0f}°"

    tbb = draw.textbbox((0, 0), temp_num, font=tf_large)
    sbb = draw.textbbox((0, 0), temp_sfx, font=tf_sfx)
    cbb = draw.textbbox((0, 0), data["condition"], font=tf_cond)
    fbb = draw.textbbox((0, 0), fl_str, font=tf_sub)

    temp_h = tbb[3] - tbb[1]
    temp_w = (tbb[2] - tbb[0]) + (sbb[2] - sbb[0])
    cond_h = cbb[3] - cbb[1]
    fl_h   = fbb[3] - fbb[1]

    # Measure total block height to vertically center in quadrant
    icon_size  = 70
    total_h    = icon_size + 8 + temp_h + 10 + cond_h + 6 + fl_h
    start_y    = tl_y0 + max(0, (tl_y1 - tl_y0 - total_h) // 2)

    icon_cy = start_y + icon_size // 2
    draw_icon(draw, data["icon"], tl_cx, icon_cy, icon_size)

    temp_y = icon_cy + icon_size // 2 + 8
    temp_x = tl_cx - temp_w // 2
    draw.text((temp_x, temp_y - tbb[1]), temp_num, font=tf_large, fill=BLACK)
    sfx_x = temp_x + (tbb[2] - tbb[0]) + 2
    draw.text((sfx_x, temp_y - tbb[1] + sbb[1] - 1), temp_sfx, font=tf_sfx, fill=BLACK)

    cond_y = temp_y + temp_h + 10
    cond_x = tl_cx - (cbb[2] - cbb[0]) // 2 - cbb[0]
    draw.text((cond_x, cond_y - cbb[1]), data["condition"], font=tf_cond, fill=BLACK)

    fl_y = cond_y + cond_h + 6
    fl_x = tl_cx - (fbb[2] - fbb[0]) // 2 - fbb[0]
    draw.text((fl_x, fl_y - fbb[1]), fl_str, font=tf_sub, fill=BLACK)

    # ── Q2 Top-right: 5-day forecast ─────────────────────────────────────────
    import datetime
    tr_x0, tr_y0, tr_x1, tr_y1 = q_tr

    now       = datetime.datetime.now()
    today_str = now.strftime("%A, %B ") + str(now.day)
    tf_date   = load_font(16)
    tf_loc    = load_font(24)
    loc_str   = data.get("location", "")

    dbb  = draw.textbbox((0, 0), today_str, font=tf_date)
    lobb = draw.textbbox((0, 0), loc_str,   font=tf_loc)

    # Location right-aligned at the very top
    draw.text((tr_x1 - (lobb[2] - lobb[0]), tr_y0 - lobb[1]),
              loc_str, font=tf_loc, fill=BLACK)

    loc_h = (lobb[3] - lobb[1]) + 4

    # Date right-aligned below location, with extra top padding
    date_top = tr_y0 + loc_h + 8
    draw.text((tr_x1 - (dbb[2] - dbb[0]), date_top - dbb[1]),
              today_str, font=tf_date, fill=BLACK)

    daily   = data.get("daily", [])[:5]
    col_w   = (tr_x1 - tr_x0) // max(len(daily), 1)
    tf_day  = load_font(20)
    tf_temp = load_font(19)
    date_h  = (dbb[3] - dbb[1]) + 28
    icon_sz = 38

    fc_cy0 = date_top + date_h
    for i, day in enumerate(daily):
        cx   = tr_x0 + i * col_w + col_w // 2
        dlbb = draw.textbbox((0, 0), day["label"], font=tf_day)
        draw.text((cx - (dlbb[2] - dlbb[0]) // 2, fc_cy0 - dlbb[1]), day["label"], font=tf_day, fill=BLACK)

        icon_y = fc_cy0 + (dlbb[3] - dlbb[1]) + 12
        draw_icon(draw, day["icon"], cx, icon_y + icon_sz // 2, icon_sz)

        hi_str = f"{day['hi_f']:.0f}°"
        lo_str = f"{day['lo_f']:.0f}°"
        hi_y   = icon_y + icon_sz + 12
        hibb   = draw.textbbox((0, 0), hi_str, font=tf_temp)
        lobb   = draw.textbbox((0, 0), lo_str, font=tf_temp)
        lo_y   = hi_y + (hibb[3] - hibb[1]) + 8
        draw.text((cx - (hibb[2] - hibb[0]) // 2, hi_y - hibb[1]), hi_str, font=tf_temp, fill=BLACK)
        draw.text((cx - (lobb[2] - lobb[0]) // 2, lo_y - lobb[1]), lo_str, font=tf_temp, fill=BLACK)

    # ── Q3 Bottom-left: details grid (2 cols × 3 rows) ───────────────────────
    bl_x0, bl_y0, bl_x1, bl_y1 = q_bl
    aqi_raw = data.get("aqi", "—")
    aqi_parts = aqi_raw.split(" ", 1) if " " in aqi_raw else (aqi_raw, "")

    # (glyph, header, number, unit)
    details = [
        (_WI["sunrise"],  "Sunrise",     *_split_ampm(data.get("sunrise", "—"))),
        (_WI["sunset"],   "Sunset",      *_split_ampm(data.get("sunset",  "—"))),
        (_WI["wind"],     "Wind",        f"{data.get('wind_mph', 0):.0f}", "mph"),
        (_WI["humidity"], "Humidity",    f"{data.get('humidity', 0):.0f}", "%"),
        (_WI["uv"],       "UV Index",    f"{data.get('uv_index', 0):.0f}", ""),
        (_WI["aqi"],      "Air Quality", aqi_parts[0], aqi_parts[1] if len(aqi_parts) > 1 else ""),
    ]
    n_cols, n_rows = 2, 3
    cell_w    = (bl_x1 - bl_x0) // n_cols
    cell_h    = (bl_y1 - bl_y0) // n_rows
    tf_dlabel = load_font(14)   # small header
    tf_dval   = load_font(24)   # large number
    tf_dunit  = load_font(14)   # small unit
    icon_d    = 28

    for idx, (glyph, label, number, unit) in enumerate(details):
        col    = idx % n_cols
        row    = idx // n_cols
        cx     = bl_x0 + col * cell_w
        cy     = bl_y0 + row * cell_h
        lbb     = draw.textbbox((0, 0), label,  font=tf_dlabel)
        nbb     = draw.textbbox((0, 0), number, font=tf_dval)
        ubb     = draw.textbbox((0, 0), unit,   font=tf_dunit) if unit else None
        label_h = lbb[3] - lbb[1]
        num_h   = nbb[3] - nbb[1]
        unit_h  = (ubb[3] - ubb[1]) if ubb else 0

        # Stack: icon → label → number+unit, all centered in cell
        total_h = icon_d + 6 + label_h + 3 + num_h
        start   = cy + (cell_h - total_h) // 2 + 6
        cell_cx = cx + cell_w // 2

        # Icon centered at top
        draw_icon(draw, glyph, cell_cx, start + icon_d // 2, icon_d)

        # Label below icon
        label_y = start + icon_d + 6
        label_x = cell_cx - (lbb[2] - lbb[0]) // 2 - lbb[0]
        draw.text((label_x, label_y - lbb[1]), label, font=tf_dlabel, fill=BLACK)

        # Number + unit below label
        num_y   = label_y + label_h + 3
        num_w   = (nbb[2] - nbb[0])
        unit_w  = ((ubb[2] - ubb[0]) + 3) if ubb else 0
        row_w   = num_w + unit_w
        num_x   = cell_cx - row_w // 2
        draw.text((num_x, num_y - nbb[1]), number, font=tf_dval, fill=BLACK)
        if unit:
            unit_x = num_x + num_w + 3
            unit_y = num_y + num_h - unit_h
            draw.text((unit_x, unit_y - ubb[1]), unit, font=tf_dunit, fill=BLACK)

    # ── Q4 Bottom-right: hourly graph ─────────────────────────────────────────
    hourly = data.get("hourly", [])
    daily  = data.get("daily", [])
    if len(hourly) >= 2:
        tf_ax    = load_font(15)
        br_x0, br_y0, br_x1, br_y1 = q_br
        graph_x0 = br_x0 + 30   # leave room for Y labels
        graph_x1 = br_x1 - 10
        graph_y0 = br_y0 + 4
        graph_y1 = br_y1 - 22   # leave room for X labels

        # Y range: today + tomorrow daily hi/lo
        day_lo = min(h["temp_f"] for h in hourly)
        day_hi = max(h["temp_f"] for h in hourly)
        temp_range = day_hi - day_lo

        import math
        # Top tick: always ceil to nearest 5°
        hi_t = math.ceil(day_hi / 5) * 5

        # Pick step size (5 or 10) so we get ~6 intervals between lo and hi
        step_size = 5 if temp_range <= 25 else 10

        # Build ticks downward from hi_t until one full step below day_lo
        lo_t = hi_t
        while lo_t > day_lo:
            lo_t -= step_size
        # lo_t is now at or below day_lo — always add one more step for clearance
        lo_t -= step_size
        span = max(hi_t - lo_t, 1)

        # Y-axis: collect all tick values, then space them evenly across the graph height
        ticks = []
        v = lo_t
        while v <= hi_t:
            ticks.append(v)
            v += step_size
        n_ticks = len(ticks)

        for idx, v in enumerate(ticks):
            # Even pixel spacing — divide graph into (n_ticks-1) equal slots
            gy  = graph_y1 - int(idx / (n_ticks - 1) * (graph_y1 - graph_y0))
            lbl = f"{v:.0f}°"
            lbb = draw.textbbox((0, 0), lbl, font=tf_ax)
            lh  = lbb[3] - lbb[1]
            x = graph_x0
            while x < graph_x1:
                draw.line([(x, gy), (min(x + 2, graph_x1), gy)], fill=BLACK, width=1)
                x += 6
            # Label right-aligned to the graph edge
            lw = lbb[2] - lbb[0]
            draw.text((graph_x0 - lw - 4, gy - lh // 2 - lbb[1]), lbl, font=tf_ax, fill=BLACK)

        # Plot curve
        n   = len(hourly)
        pts = []
        for i, h in enumerate(hourly):
            gx = graph_x0 + int(i / (n - 1) * (graph_x1 - graph_x0))
            gy = graph_y1 - int((h["temp_f"] - lo_t) / span * (graph_y1 - graph_y0))
            pts.append((gx, gy))
        draw.line(pts, fill=BLACK, width=4)

        # X-axis: one label per hourly point (Now + 6 hours)
        for i in range(n):
            gx  = graph_x0 + int(i / (n - 1) * (graph_x1 - graph_x0))
            if i == 0:
                lbl = "Now"
            else:
                h   = hourly[i]["hour"] % 24
                sfx = "am" if h < 12 else "pm"
                lbl = f"{h % 12 or 12}{sfx}"
            lbb = draw.textbbox((0, 0), lbl, font=tf_ax)
            lw  = lbb[2] - lbb[0]
            draw.text((gx - lw // 2, graph_y1 + 16 - lbb[1]), lbl, font=tf_ax, fill=BLACK)

    return to_1bit(img).tobytes()
