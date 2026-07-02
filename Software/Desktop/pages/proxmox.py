"""Proxmox VM/Container Status page."""

import logging

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
log = logging.getLogger(__name__)


# ── Data ──────────────────────────────────────────────────────────────────────

def _get(url, token, path):
    try:
        r = requests.get(f"{url}{path}",
                         headers={"Authorization": f"PVEAPIToken={token}"},
                         verify=False, timeout=5)
        r.raise_for_status()
        return r.json().get("data")
    except Exception as exc:
        log.warning("Proxmox %s: %s", path, exc)
        return None


def fetch(cfg: dict) -> list[dict] | None:
    url, token = cfg.get("url", "").rstrip("/"), cfg.get("api_token", "")
    if not url or not token:
        return None
    nodes = _get(url, token, "/api2/json/nodes")
    if not nodes:
        return None

    results = []
    for node in nodes:
        n = node["node"]
        for typ in ("qemu", "lxc"):
            items = _get(url, token, f"/api2/json/nodes/{n}/{typ}") or []
            for item in items:
                vmid   = item.get("vmid", "?")
                status = item.get("status", "unknown")

                # Per-VM detailed stats
                detail = _get(url, token, f"/api2/json/nodes/{n}/{typ}/{vmid}/status/current") or {}

                # CPU %
                cpu_pct = round((detail.get("cpu") or item.get("cpu") or 0) * 100, 1)

                # RAM %
                mem     = detail.get("mem")  or item.get("mem",  0)
                maxmem  = detail.get("maxmem") or item.get("maxmem", 1)
                mem_pct = round(mem / maxmem * 100) if maxmem else 0

                # Boot disk %
                disk    = detail.get("disk")    or item.get("disk",    0)
                maxdisk = detail.get("maxdisk")  or item.get("maxdisk", 1)
                disk_pct = round(disk / maxdisk * 100) if maxdisk else 0

                # IPv4 — available for LXC; for QEMU need agent
                ip = "—"
                if typ == "lxc":
                    ifaces = _get(url, token, f"/api2/json/nodes/{n}/{typ}/{vmid}/interfaces") or []
                    for iface in ifaces:
                        addr = iface.get("inet", "")
                        if addr and not addr.startswith("127."):
                            ip = addr.split("/")[0]
                            break
                elif typ == "qemu" and status == "running":
                    agent = _get(url, token, f"/api2/json/nodes/{n}/{typ}/{vmid}/agent/network-get-interfaces")
                    if agent:
                        for iface in (agent.get("result") or []):
                            for a in (iface.get("ip-addresses") or []):
                                if a.get("ip-address-type") == "ipv4" and not a["ip-address"].startswith("127."):
                                    ip = a["ip-address"]
                                    break
                            if ip != "—":
                                break

                tags = [t.strip().lower() for t in (item.get("tags") or "").split(";") if t.strip()]
                if "dashboard" not in tags:
                    continue

                results.append({
                    "name":     item.get("name") or str(vmid),
                    "type":     "VM" if typ == "qemu" else "CT",
                    "status":   status,
                    "cpu_pct":  cpu_pct,
                    "mem_pct":  mem_pct,
                    "disk_pct": disk_pct,
                    "ip":       ip,
                    "node":     n,
                })

    results.sort(key=lambda x: (x["status"] != "running", x["name"].lower()))
    return results or None


# ── Render ────────────────────────────────────────────────────────────────────

def render(data: list[dict] | None) -> bytes:
    from ._base import PANEL_WIDTH, PANEL_HEIGHT, BLACK, WHITE, load_font, load_bold, to_1bit, new_page
    img, draw = new_page()

    tf_title  = load_bold(36)
    tf_node   = load_font(22)
    tf_name   = load_bold(22)
    tf_type   = load_font(19)
    tf_info   = load_font(19)
    tf_status = load_bold(26)

    MARGIN  = 14
    HDR_H   = 52

    # Header: "Proxmox" large + "- nodename" smaller, baseline-aligned
    node_name = data[0]["node"] if data else ""
    tbb  = draw.textbbox((0, 0), "Proxmox", font=tf_title)
    nbb  = draw.textbbox((0, 0), node_name, font=tf_node)
    ty   = 8
    draw.text((MARGIN, ty - tbb[1]), "Proxmox", font=tf_title, fill=BLACK)
    # Baseline-align node name with title: bottom of title = ty + (tbb[3]-tbb[1])
    title_bottom = ty + (tbb[3] - tbb[1])
    node_y = title_bottom - (nbb[3] - nbb[1])
    draw.text((PANEL_WIDTH - MARGIN - (nbb[2] - nbb[0]), node_y - nbb[1]), node_name, font=tf_node, fill=BLACK)
    draw.line([(MARGIN, HDR_H), (PANEL_WIDTH - MARGIN, HDR_H)], fill=BLACK, width=1)

    if data is None:
        draw.text((MARGIN, HDR_H + 10), "Connection failed — check url/api_token", font=tf_info, fill=BLACK)
        return to_1bit(img).tobytes()
    if not data:
        draw.text((MARGIN, HDR_H + 10), "No VMs or containers found.", font=tf_info, fill=BLACK)
        return to_1bit(img).tobytes()

    # Column layout — two columns of cards
    N_COLS   = 2
    col_w    = (PANEL_WIDTH - MARGIN * 2 - 8) // N_COLS
    status_h = 22   # height reserved for ONLINE/OFFLINE label above line
    card_h   = 68
    card_gap = 18
    rows_per_col = (PANEL_HEIGHT - HDR_H - MARGIN) // (card_h + status_h + card_gap)
    max_items    = N_COLS * rows_per_col

    for idx, vm in enumerate(data[:max_items]):
        col     = idx % N_COLS
        row     = idx // N_COLS
        x       = MARGIN + col * (col_w + 8)
        block_y = HDR_H + 16 + row * (card_h + status_h + card_gap)

        # Status label + line
        online     = vm["status"] == "running"
        status_lbl = "ONLINE" if online else "OFFLINE"
        slbb       = draw.textbbox((0, 0), status_lbl, font=tf_status)
        draw.text((x, block_y - slbb[1]), status_lbl, font=tf_status, fill=BLACK)
        line_y = block_y + status_h
        draw.line([(x, line_y), (x + col_w - 60, line_y)], fill=BLACK, width=1)

        # Card content starts below the line
        y = line_y + 8

        # Name (bold) + [VM/CT] (regular) on same line
        nbb = draw.textbbox((0, 0), vm["name"], font=tf_name)
        draw.text((x, y - nbb[1]), vm["name"], font=tf_name, fill=BLACK)
        tbb = draw.textbbox((0, 0), f"[{vm['type']}]", font=tf_type)
        draw.text((x + (nbb[2] - nbb[0]) + 6, y - tbb[1] + 2), f"[{vm['type']}]", font=tf_type, fill=BLACK)

        # Info line 1: CPU / MEM
        line1 = f"CPU {vm['cpu_pct']}%  MEM {vm['mem_pct']}%"
        l1bb  = draw.textbbox((0, 0), line1, font=tf_info)
        draw.text((x, y + 22 - l1bb[1]), line1, font=tf_info, fill=BLACK)

        # Info line 2: Disk / IP
        line2 = f"Disk {vm['disk_pct']}%  IP {vm['ip']}"
        l2bb  = draw.textbbox((0, 0), line2, font=tf_info)
        draw.text((x, y + 44 - l2bb[1]), line2, font=tf_info, fill=BLACK)

    # Overflow indicator
    excess = len(data) - max_items
    if excess > 0:
        ebb = draw.textbbox((0, 0), f"+{excess} more", font=tf_info)
        draw.text((PANEL_WIDTH - MARGIN - (ebb[2]-ebb[0]), PANEL_HEIGHT - MARGIN - (ebb[3]-ebb[1]) - ebb[1]),
                  f"+{excess} more", font=tf_info, fill=BLACK)

    return to_1bit(img).tobytes()
