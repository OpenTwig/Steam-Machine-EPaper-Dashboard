"""
config_loader.py — reads config.yml and enforces hard-coded minimum floors.

All callers use load_config().  The returned dict is the raw YAML structure
with any out-of-range values already clamped in place, so the rest of the
codebase can trust the values without re-checking them.
"""

import os
import yaml
import logging

log = logging.getLogger(__name__)

# Hard-coded floors enforced in code regardless of what the file says.
_MIN_UPDATE_INTERVAL   = 5    # seconds — baseline refresh
_MIN_CRITICAL_INTERVAL = 8    # seconds — critical-poll minimum
_MIN_SLOW_INTERVAL     = 60   # seconds — slow-poll minimum when non-zero

_PAGE_NAMES = [
    "system", "clock", "weather",
    "plex", "fact", "proxmox", "sports_calendar",
]

_DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "config.yml")


def load_config(path: str = _DEFAULT_PATH) -> dict:
    """
    Parse config.yml and return the validated config dict.
    Raises FileNotFoundError / yaml.YAMLError on bad input.
    """
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    if cfg is None:
        cfg = {}

    # ── Baseline update interval ──────────────────────────────────────────────
    raw = cfg.get("update_interval_seconds", _MIN_UPDATE_INTERVAL)
    if raw < _MIN_UPDATE_INTERVAL:
        log.warning(
            "update_interval_seconds=%s below minimum %s; clamping",
            raw, _MIN_UPDATE_INTERVAL,
        )
    cfg["update_interval_seconds"] = max(_MIN_UPDATE_INTERVAL, int(raw))

    # ── Per-page interval floors ──────────────────────────────────────────────
    for name in _PAGE_NAMES:
        block = cfg.get(name)
        if not isinstance(block, dict):
            continue

        # critical_poll_interval_seconds
        raw_c = block.get("critical_poll_interval_seconds", _MIN_CRITICAL_INTERVAL)
        if raw_c is not None:
            if raw_c < _MIN_CRITICAL_INTERVAL:
                log.warning(
                    "%s.critical_poll_interval_seconds=%s below %s; clamping",
                    name, raw_c, _MIN_CRITICAL_INTERVAL,
                )
            block["critical_poll_interval_seconds"] = max(_MIN_CRITICAL_INTERVAL, int(raw_c))

        # slow_poll_interval_seconds — 0 means disabled; if non-zero, clamp
        raw_s = block.get("slow_poll_interval_seconds", 0)
        if raw_s and raw_s < _MIN_SLOW_INTERVAL:
            log.warning(
                "%s.slow_poll_interval_seconds=%s below %s; clamping",
                name, raw_s, _MIN_SLOW_INTERVAL,
            )
            block["slow_poll_interval_seconds"] = _MIN_SLOW_INTERVAL

    return cfg


def page_cfg(cfg: dict, name: str) -> dict:
    """Convenience: return a page's config block (empty dict if missing)."""
    return cfg.get(name) or {}


def is_enabled(cfg: dict, name: str) -> bool:
    """True when the page block exists AND enabled: true."""
    return bool(page_cfg(cfg, name).get("enabled", False))


def enabled_pages(cfg: dict) -> list[str]:
    """
    Return the ordered list of page names from enabled_pages: that are also
    individually enabled in their block.  Falls back to scanning all known
    pages in definition order.
    """
    ordered = cfg.get("enabled_pages") or _PAGE_NAMES
    return [p for p in ordered if is_enabled(cfg, p)]
