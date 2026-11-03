#!/usr/bin/env python3
"""
TachoPulse Telemetry Engine for Noctalia v5.

Discovers hardware and storage sensors (storage, CPU, multi-GPU, RAM, thermals,
fans, network transfer rates, battery, Linux PSI) and generates anti-aliased
gauge graphics.
"""

import argparse
import copy
import fcntl
import glob
import json
import math
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    sys.stderr.write("TachoPulse warning: PIL/Pillow is not installed. Gauge graph rendering will be disabled.\n")

RUNTIME_BASE = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/dev/shm"),
    "tachopulse",
)
ASSETS_DIR = os.path.join(RUNTIME_BASE, "assets")
CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "noctalia",
    "tachopulse",
)
INSTANCES_FILE = os.path.join(CONFIG_DIR, "instances.json")
CMD_FILE = os.path.join(RUNTIME_BASE, "cmd.json")
SNAPSHOT_FILE = os.path.join(RUNTIME_BASE, "snapshot.json")
STATE_FILE = os.path.join(RUNTIME_BASE, "daemon_state.json")
DAEMON_PID_FILE = os.path.join(RUNTIME_BASE, "daemon.pid")

os.makedirs(RUNTIME_BASE, exist_ok=True)
os.makedirs(ASSETS_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)

PALETTE_COLORS: Dict[str, Tuple[int, int, int]] = {
    "cyan": (86, 214, 214),
    "blue": (120, 180, 255),
    "green": (115, 230, 155),
    "orange": (255, 185, 110),
    "purple": (195, 145, 255),
    "red": (255, 110, 125),
    "yellow": (250, 225, 115),
    "white": (240, 245, 255),
}


def resolve_color(c: str) -> Tuple[int, int, int]:
    if not c:
        return PALETTE_COLORS["cyan"]
    if c in PALETTE_COLORS:
        return PALETTE_COLORS[c]
    if c.startswith("#") and len(c) in (7, 9):
        try:
            return (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))
        except ValueError:
            pass
    return PALETTE_COLORS["cyan"]

TRACK_COLOR = (90, 100, 135, 235)

DEFAULT_INSTANCES: Dict[str, Dict[str, Any]] = {
    "default": {
        "id": "default",
        "name": "CPU Load",
        "sensor_id": "cpu/total_usage",
        "color": "cyan",
        "show_icon": True,
        "icon_type": "glyph",
        "icon_val": "cpu",
        "show_graph": True,
        "graph_type": "tachometer_quarter",
        "stroke_width": 7,
        "show_value": True,
        "decimals": 0,
        "show_label": True,
        "label": "CPU",
    },
    "cpu_temp": {
        "id": "cpu_temp",
        "name": "CPU Package Temp",
        "sensor_id": "cpu/package_temp",
        "color": "orange",
        "show_icon": True,
        "icon_type": "emoji",
        "icon_val": "🔥",
        "show_graph": True,
        "graph_type": "tachometer_full",
        "stroke_width": 7,
        "show_value": True,
        "decimals": 0,
        "show_label": True,
        "label": "Tctl",
    },
    "storage_free": {
        "id": "storage_free",
        "name": "Root Free Space",
        "sensor_id": "storage/root/free_gb",
        "color": "green",
        "show_icon": True,
        "icon_type": "glyph",
        "icon_val": "database",
        "show_graph": True,
        "graph_type": "bar_vertical",
        "stroke_width": 7,
        "show_value": True,
        "decimals": 1,
        "show_label": True,
        "label": "Free",
    },
    "wifi_net": {
        "id": "wifi_net",
        "name": "Network Signal / Rate",
        "sensor_id": "net/wlan0/rx_rate",
        "color": "blue",
        "show_icon": True,
        "icon_type": "glyph",
        "icon_val": "wifi",
        "show_graph": True,
        "graph_type": "geometric_wifi",
        "stroke_width": 7,
        "show_value": True,
        "decimals": 1,
        "show_label": True,
        "label": "DL",
    },
}

_PREV_CPU_TIMES: Dict[str, Tuple[int, int]] = {}
_PREV_NET_BYTES: Dict[str, Tuple[int, int, float]] = {}

_INSTANCE_FILES: Dict[str, Dict[str, str]] = {}
_INSTANCE_RENDER_KEYS: Dict[str, Dict[str, Tuple]] = {}

_NVTOP_AVAILABLE: Optional[bool] = None
_NVIDIA_SMI_AVAILABLE: Optional[bool] = None


def load_daemon_state() -> Tuple[Dict[str, Tuple[int, int]], Dict[str, Tuple[int, int, float]]]:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                cpu = {k: tuple(v) for k, v in d.get("cpu", {}).items()}
                net = {k: tuple(v) for k, v in d.get("net", {}).items()}
                return cpu, net
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            pass
    return {}, {}


def save_daemon_state(cpu: Dict[str, Tuple[int, int]], net: Dict[str, Tuple[int, int, float]]) -> None:
    tmp_path = STATE_FILE + f".tmp.{os.getpid()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"cpu": cpu, "net": net}, f)
        os.replace(tmp_path, STATE_FILE)
    except OSError:
        pass


def read_text(path: str, default: str = "") -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return default


def format_rate_human(bytes_per_sec: float) -> str:
    if bytes_per_sec < 1024:
        return f"{int(bytes_per_sec)} B/s"
    for unit in ["KB/s", "MB/s", "GB/s"]:
        bytes_per_sec /= 1024.0
        if bytes_per_sec < 1024:
            return f"{bytes_per_sec:.1f} {unit}" if bytes_per_sec < 10 else f"{int(bytes_per_sec)} {unit}"
    return f"{bytes_per_sec:.1f} GB/s"


# ==============================================================================
# Sensor Discovery & Scraper Engine
# ==============================================================================

def sample_filesystems() -> Dict[str, Dict[str, Any]]:
    results = {}
    ignored_fs = {
        "tmpfs", "devtmpfs", "devpts", "sysfs", "proc", "cgroup", "cgroup2",
        "securityfs", "pstore", "bpf", "autofs", "mqueue", "hugetlbfs",
        "debugfs", "tracefs", "fusectl", "configfs", "ramfs", "binfmt_misc",
        "efivarfs", "overlay", "squashfs",
    }
    seen_stats = set()

    try:
        with open("/proc/mounts", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) < 3:
                    continue
                dev, mount, fstype = parts[0], parts[1], parts[2]
                if fstype in ignored_fs or not dev.startswith("/dev/"):
                    continue

                if mount in {"/var/tmp", "/var/cache", "/var/log", "/root", "/srv"}:
                    continue

                try:
                    st = os.statvfs(mount)
                    total_bytes = st.f_blocks * st.f_frsize
                    free_bytes = st.f_bavail * st.f_frsize
                    if total_bytes == 0:
                        continue
                    used_bytes = total_bytes - free_bytes
                    used_pct = (used_bytes / total_bytes) * 100.0
                    free_pct = (free_bytes / total_bytes) * 100.0

                    sig = (dev, total_bytes, round(free_bytes / (1024 * 1024 * 100)))
                    if sig in seen_stats and mount not in {"/", "/home"}:
                        continue
                    seen_stats.add(sig)

                    if mount == "/":
                        mount_name = "Root (/)"
                        slug = "root"
                    elif mount == "/home":
                        mount_name = "Home (/home)"
                        slug = "home"
                    elif mount == "/boot":
                        mount_name = "Boot (/boot)"
                        slug = "boot"
                    else:
                        mount_name = f"{os.path.basename(mount.rstrip('/'))} ({mount})"
                        slug = re.sub(r"[^a-zA-Z0-9_]", "_", mount.strip("/")).lower()

                    total_gb = total_bytes / (1024**3)
                    free_gb = free_bytes / (1024**3)
                    used_gb = used_bytes / (1024**3)

                    id_free = f"storage/{slug}/free_gb"
                    results[id_free] = {
                        "id": id_free,
                        "category": "Storage",
                        "name": f"Storage: {mount_name} - Free Space",
                        "short_label": "FREE",
                        "value": round(free_gb, 1),
                        "val_text": f"{free_gb:.1f} GB" if free_gb < 1000 else f"{free_gb/1024:.2f} TB",
                        "unit": "GB",
                        "min": 0,
                        "max": round(total_gb, 1),
                        "pct": free_pct / 100.0,
                        "icon": "database",
                        "default_color": "green",
                    }

                    id_free_pct = f"storage/{slug}/free_pct"
                    results[id_free_pct] = {
                        "id": id_free_pct,
                        "category": "Storage",
                        "name": f"Storage: {mount_name} - Free %",
                        "short_label": "FREE%",
                        "value": round(free_pct, 1),
                        "val_text": f"{free_pct:.0f}%",
                        "unit": "%",
                        "min": 0,
                        "max": 100,
                        "pct": free_pct / 100.0,
                        "icon": "database",
                        "default_color": "green",
                    }

                    id_used = f"storage/{slug}/used_gb"
                    results[id_used] = {
                        "id": id_used,
                        "category": "Storage",
                        "name": f"Storage: {mount_name} - Used Space",
                        "short_label": "USED",
                        "value": round(used_gb, 1),
                        "val_text": f"{used_gb:.1f} GB" if used_gb < 1000 else f"{used_gb/1024:.2f} TB",
                        "unit": "GB",
                        "min": 0,
                        "max": round(total_gb, 1),
                        "pct": used_pct / 100.0,
                        "icon": "database",
                        "default_color": "orange",
                    }

                    id_used_pct = f"storage/{slug}/used_pct"
                    results[id_used_pct] = {
                        "id": id_used_pct,
                        "category": "Storage",
                        "name": f"Storage: {mount_name} - Used %",
                        "short_label": "USED%",
                        "value": round(used_pct, 1),
                        "val_text": f"{used_pct:.0f}%",
                        "unit": "%",
                        "min": 0,
                        "max": 100,
                        "pct": used_pct / 100.0,
                        "icon": "database",
                        "default_color": "orange",
                    }
                except OSError:
                    continue
    except OSError:
        pass
    return results


def sample_cpu() -> Dict[str, Dict[str, Any]]:
    global _PREV_CPU_TIMES
    results = {}

    try:
        with open("/proc/stat", "r", encoding="utf-8") as f:
            for line in f:
                if not line.startswith("cpu"):
                    continue
                parts = line.split()
                name = parts[0]
                user = int(parts[1])
                nice = int(parts[2])
                system = int(parts[3])
                idle = int(parts[4])
                iowait = int(parts[5]) if len(parts) > 5 else 0
                irq = int(parts[6]) if len(parts) > 6 else 0
                softirq = int(parts[7]) if len(parts) > 7 else 0
                steal = int(parts[8]) if len(parts) > 8 else 0

                idle_time = idle + iowait
                non_idle = user + nice + system + irq + softirq + steal
                total_time = idle_time + non_idle

                prev_total, prev_idle = _PREV_CPU_TIMES.get(name, (0, 0))
                _PREV_CPU_TIMES[name] = (total_time, idle_time)

                diff_total = total_time - prev_total
                diff_idle = idle_time - prev_idle

                if diff_total > 0:
                    pct = max(0.0, min(100.0, ((diff_total - diff_idle) / diff_total) * 100.0))
                else:
                    pct = 0.0

                if name == "cpu":
                    results["cpu/total_usage"] = {
                        "id": "cpu/total_usage",
                        "category": "CPU",
                        "name": "CPU Total Load",
                        "short_label": "CPU",
                        "value": round(pct, 1),
                        "val_text": f"{pct:.0f}%",
                        "unit": "%",
                        "min": 0,
                        "max": 100,
                        "pct": pct / 100.0,
                        "icon": "cpu",
                        "default_color": "cyan",
                    }
                else:
                    core_num = name.replace("cpu", "")
                    results[f"cpu/core_{core_num}"] = {
                        "id": f"cpu/core_{core_num}",
                        "category": "CPU Cores",
                        "name": f"CPU Core {core_num} Load",
                        "short_label": f"C{core_num}",
                        "value": round(pct, 1),
                        "val_text": f"{pct:.0f}%",
                        "unit": "%",
                        "min": 0,
                        "max": 100,
                        "pct": pct / 100.0,
                        "icon": "cpu",
                        "default_color": "cyan",
                    }
    except OSError:
        pass

    freq_files = glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq")
    if freq_files:
        freqs_mhz = []
        for path in freq_files:
            txt = read_text(path)
            if txt.isdigit():
                freqs_mhz.append(int(txt) / 1000.0)
        if freqs_mhz:
            avg_mhz = sum(freqs_mhz) / len(freqs_mhz)
            val_ghz = avg_mhz / 1000.0
            results["cpu/clock_avg"] = {
                "id": "cpu/clock_avg",
                "category": "CPU",
                "name": "CPU Average Clock",
                "short_label": "CLK",
                "value": round(val_ghz, 2),
                "val_text": f"{val_ghz:.2f} GHz",
                "unit": "GHz",
                "min": 0,
                "max": 6.0,
                "pct": max(0.0, min(1.0, val_ghz / 6.0)),
                "icon": "bolt",
                "default_color": "yellow",
            }

    try:
        load1, _, _ = os.getloadavg()
        cores = float(os.cpu_count() or 4)
        results["cpu/loadavg_1m"] = {
            "id": "cpu/loadavg_1m",
            "category": "CPU",
            "name": "System Load Average (1m)",
            "short_label": "LOAD",
            "value": round(load1, 2),
            "val_text": f"{load1:.2f}",
            "unit": "",
            "min": 0,
            "max": cores,
            "pct": min(1.0, max(0.0, load1 / cores)),
            "icon": "activity",
            "default_color": "purple",
        }
    except OSError:
        pass

    return results


def sample_memory() -> Dict[str, Dict[str, Any]]:
    results = {}
    try:
        meminfo = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    val_parts = parts[1].strip().split()
                    if val_parts and val_parts[0].isdigit():
                        meminfo[parts[0].strip()] = int(val_parts[0])

        total_kb = meminfo.get("MemTotal", 0)
        avail_kb = meminfo.get("MemAvailable", 0)
        if total_kb > 0:
            used_kb = total_kb - avail_kb
            pct = (used_kb / total_kb) * 100.0
            used_gb = used_kb / (1024 * 1024)
            total_gb = total_kb / (1024 * 1024)
            free_gb = avail_kb / (1024 * 1024)

            results["memory/ram_used_pct"] = {
                "id": "memory/ram_used_pct",
                "category": "Memory",
                "name": "Physical RAM Usage (%)",
                "short_label": "RAM",
                "value": round(pct, 1),
                "val_text": f"{pct:.0f}%",
                "unit": "%",
                "min": 0,
                "max": 100,
                "pct": pct / 100.0,
                "icon": "device-desktop-analytics",
                "default_color": "purple",
            }
            results["memory/ram_used_gb"] = {
                "id": "memory/ram_used_gb",
                "category": "Memory",
                "name": "Physical RAM Used (GB)",
                "short_label": "RAM",
                "value": round(used_gb, 1),
                "val_text": f"{used_gb:.1f} GB",
                "unit": "GB",
                "min": 0,
                "max": round(total_gb, 1),
                "pct": pct / 100.0,
                "icon": "device-desktop-analytics",
                "default_color": "purple",
            }
            results["memory/ram_free_gb"] = {
                "id": "memory/ram_free_gb",
                "category": "Memory",
                "name": "Physical RAM Available (GB)",
                "short_label": "AVAL",
                "value": round(free_gb, 1),
                "val_text": f"{free_gb:.1f} GB",
                "unit": "GB",
                "min": 0,
                "max": round(total_gb, 1),
                "pct": max(0.0, min(1.0, free_gb / total_gb)),
                "icon": "device-desktop-analytics",
                "default_color": "green",
            }

        swap_total = meminfo.get("SwapTotal", 0)
        swap_free = meminfo.get("SwapFree", 0)
        if swap_total > 0:
            swap_used = swap_total - swap_free
            swap_pct = (swap_used / swap_total) * 100.0
            results["memory/swap_used_pct"] = {
                "id": "memory/swap_used_pct",
                "category": "Memory",
                "name": "Swap Memory Usage (%)",
                "short_label": "SWAP",
                "value": round(swap_pct, 1),
                "val_text": f"{swap_pct:.0f}%",
                "unit": "%",
                "min": 0,
                "max": 100,
                "pct": swap_pct / 100.0,
                "icon": "stack-2",
                "default_color": "orange",
            }
    except OSError:
        pass

    return results


_NUM_RE = re.compile(r"[-+]?\d*\.?\d+")


def _parse_num(val: Any) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    m = _NUM_RE.search(str(val))
    return float(m.group(0)) if m else 0.0


def sample_gpus() -> Dict[str, Dict[str, Any]]:
    global _NVTOP_AVAILABLE, _NVIDIA_SMI_AVAILABLE
    results = {}
    has_gpus = False

    if _NVTOP_AVAILABLE is not False:
        try:
            proc = subprocess.run(
                ["nvtop", "-s"],
                capture_output=True,
                text=True,
                timeout=1.5,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                _NVTOP_AVAILABLE = True
                raw_data = json.loads(proc.stdout)
                devices = raw_data if isinstance(raw_data, list) else raw_data.get("devices", [])
                for idx, g in enumerate(devices):
                    has_gpus = True
                    raw_name = g.get("device_name") or f"GPU {idx}"
                    clean_name = raw_name.replace(" Laptop GPU", "").replace(" Graphics", "")
                    gpu_slug = f"gpu_{idx}"
                    cat = "GPU"

                    load = _parse_num(g.get("gpu_util"))
                    results[f"{gpu_slug}/load"] = {
                        "id": f"{gpu_slug}/load",
                        "category": cat,
                        "name": f"{clean_name} - Core Utilization",
                        "short_label": "GPU",
                        "value": round(load, 1),
                        "val_text": f"{load:.0f}%",
                        "unit": "%",
                        "min": 0,
                        "max": 100,
                        "pct": max(0.0, min(1.0, load / 100.0)),
                        "icon": "device-gamepad",
                        "default_color": "green",
                    }

                    clock = _parse_num(g.get("gpu_clock"))
                    if clock > 0:
                        results[f"{gpu_slug}/core_clock"] = {
                            "id": f"{gpu_slug}/core_clock",
                            "category": cat,
                            "name": f"{clean_name} - Core Clock",
                            "short_label": "GCLK",
                            "value": round(clock, 0),
                            "val_text": f"{clock:.0f} MHz",
                            "unit": "MHz",
                            "min": 0,
                            "max": 3000,
                            "pct": max(0.0, min(1.0, clock / 3000.0)),
                            "icon": "bolt",
                            "default_color": "yellow",
                        }

                    mem_total = _parse_num(g.get("mem_total"))
                    mem_used = _parse_num(g.get("mem_used"))
                    mem_util = _parse_num(g.get("mem_util"))
                    if mem_total > 0:
                        total_gb = mem_total / (1024**3)
                        used_gb = mem_used / (1024**3)
                        free_gb = max(0.0, total_gb - used_gb)
                        vram_pct = mem_util if mem_util > 0 else ((used_gb / total_gb) * 100.0 if total_gb > 0 else 0.0)

                        if mem_used > 0 or mem_util > 0:
                            results[f"{gpu_slug}/vram_pct"] = {
                                "id": f"{gpu_slug}/vram_pct",
                                "category": cat,
                                "name": f"{clean_name} - VRAM Usage (%)",
                                "short_label": "VRAM%",
                                "value": round(vram_pct, 1),
                                "val_text": f"{vram_pct:.0f}%",
                                "unit": "%",
                                "min": 0,
                                "max": 100,
                                "pct": max(0.0, min(1.0, vram_pct / 100.0)),
                                "icon": "database",
                                "default_color": "blue",
                            }
                            results[f"{gpu_slug}/vram_used"] = {
                                "id": f"{gpu_slug}/vram_used",
                                "category": cat,
                                "name": f"{clean_name} - VRAM Used (GB)",
                                "short_label": "VRAM",
                                "value": round(used_gb, 2),
                                "val_text": f"{used_gb:.1f} GB",
                                "unit": "GB",
                                "min": 0,
                                "max": round(total_gb, 1),
                                "pct": max(0.0, min(1.0, used_gb / total_gb)) if total_gb > 0 else 0.0,
                                "icon": "database",
                                "default_color": "blue",
                            }
                            results[f"{gpu_slug}/vram_free"] = {
                                "id": f"{gpu_slug}/vram_free",
                                "category": cat,
                                "name": f"{clean_name} - VRAM Free (GB)",
                                "short_label": "VFREE",
                                "value": round(free_gb, 2),
                                "val_text": f"{free_gb:.1f} GB",
                                "unit": "GB",
                                "min": 0,
                                "max": round(total_gb, 1),
                                "pct": max(0.0, min(1.0, free_gb / total_gb)) if total_gb > 0 else 0.0,
                                "icon": "database",
                                "default_color": "green",
                            }

                    mem_clock = _parse_num(g.get("mem_clock"))
                    if mem_clock > 0:
                        results[f"{gpu_slug}/mem_clock"] = {
                            "id": f"{gpu_slug}/mem_clock",
                            "category": cat,
                            "name": f"{clean_name} - Memory Clock",
                            "short_label": "MCLK",
                            "value": round(mem_clock, 0),
                            "val_text": f"{mem_clock:.0f} MHz",
                            "unit": "MHz",
                            "min": 0,
                            "max": 10000,
                            "pct": max(0.0, min(1.0, mem_clock / 10000.0)),
                            "icon": "bolt",
                            "default_color": "purple",
                        }

                    temp = _parse_num(g.get("temp"))
                    if temp > 0:
                        results[f"{gpu_slug}/temp"] = {
                            "id": f"{gpu_slug}/temp",
                            "category": cat,
                            "name": f"{clean_name} - Temperature",
                            "short_label": "GTEMP",
                            "value": round(temp, 1),
                            "val_text": f"{temp:.0f}°C",
                            "unit": "°C",
                            "min": 0,
                            "max": 105,
                            "pct": max(0.0, min(1.0, temp / 105.0)),
                            "icon": "flame",
                            "default_color": "orange",
                        }

                    power = _parse_num(g.get("power_draw"))
                    if power > 0:
                        results[f"{gpu_slug}/power"] = {
                            "id": f"{gpu_slug}/power",
                            "category": cat,
                            "name": f"{clean_name} - Power Draw",
                            "short_label": "GPWR",
                            "value": round(power, 1),
                            "val_text": f"{power:.1f} W",
                            "unit": "W",
                            "min": 0,
                            "max": 250,
                            "pct": max(0.0, min(1.0, power / 250.0)),
                            "icon": "bolt",
                            "default_color": "yellow",
                        }
            else:
                _NVTOP_AVAILABLE = False
        except FileNotFoundError:
            _NVTOP_AVAILABLE = False
        except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
            pass

    if not has_gpus and _NVIDIA_SMI_AVAILABLE is not False:
        try:
            cmd = [
                "nvidia-smi",
                "--query-gpu=index,name,utilization.gpu,utilization.memory,memory.total,memory.used,memory.free,temperature.gpu,power.draw,clocks.current.graphics,clocks.current.memory",
                "--format=csv,noheader,nounits",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1.5)
            if proc.returncode == 0 and proc.stdout.strip():
                _NVIDIA_SMI_AVAILABLE = True
                for line in proc.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 11:
                        has_gpus = True
                        idx = parts[0]
                        name = parts[1].replace(" Laptop GPU", "").replace(" Graphics", "")
                        gpu_slug = f"gpu_{idx}"
                        cat = "GPU"
                        load = _parse_num(parts[2])
                        vram_pct = _parse_num(parts[3])
                        total_mb = _parse_num(parts[4])
                        used_mb = _parse_num(parts[5])
                        free_mb = _parse_num(parts[6])
                        temp = _parse_num(parts[7])
                        power = _parse_num(parts[8])
                        gclk = _parse_num(parts[9])
                        mclk = _parse_num(parts[10])

                        results[f"{gpu_slug}/load"] = {
                            "id": f"{gpu_slug}/load",
                            "category": cat,
                            "name": f"{name} - Core Utilization",
                            "short_label": "GPU",
                            "value": round(load, 1),
                            "val_text": f"{load:.0f}%",
                            "unit": "%",
                            "min": 0,
                            "max": 100,
                            "pct": max(0.0, min(1.0, load / 100.0)),
                            "icon": "device-gamepad",
                            "default_color": "green",
                        }
                        if gclk > 0:
                            results[f"{gpu_slug}/core_clock"] = {
                                "id": f"{gpu_slug}/core_clock",
                                "category": cat,
                                "name": f"{name} - Core Clock",
                                "short_label": "GCLK",
                                "value": round(gclk, 0),
                                "val_text": f"{gclk:.0f} MHz",
                                "unit": "MHz",
                                "min": 0,
                                "max": 3000,
                                "pct": max(0.0, min(1.0, gclk / 2600.0)),
                                "icon": "bolt",
                                "default_color": "yellow",
                            }
                        results[f"{gpu_slug}/vram_pct"] = {
                            "id": f"{gpu_slug}/vram_pct",
                            "category": cat,
                            "name": f"{name} - VRAM Usage (%)",
                            "short_label": "VRAM%",
                            "value": round(vram_pct, 1),
                            "val_text": f"{vram_pct:.0f}%",
                            "unit": "%",
                            "min": 0,
                            "max": 100,
                            "pct": max(0.0, min(1.0, vram_pct / 100.0)),
                            "icon": "database",
                            "default_color": "blue",
                        }
                        if total_mb > 0:
                            used_gb = used_mb / 1024.0
                            free_gb = free_mb / 1024.0
                            total_gb = total_mb / 1024.0
                            results[f"{gpu_slug}/vram_used"] = {
                                "id": f"{gpu_slug}/vram_used",
                                "category": cat,
                                "name": f"{name} - VRAM Used (GB)",
                                "short_label": "VRAM",
                                "value": round(used_gb, 2),
                                "val_text": f"{used_gb:.1f} GB",
                                "unit": "GB",
                                "min": 0,
                                "max": round(total_gb, 1),
                                "pct": max(0.0, min(1.0, used_gb / total_gb)),
                                "icon": "database",
                                "default_color": "blue",
                            }
                            results[f"{gpu_slug}/vram_free"] = {
                                "id": f"{gpu_slug}/vram_free",
                                "category": cat,
                                "name": f"{name} - VRAM Free (GB)",
                                "short_label": "VFREE",
                                "value": round(free_gb, 2),
                                "val_text": f"{free_gb:.1f} GB",
                                "unit": "GB",
                                "min": 0,
                                "max": round(total_gb, 1),
                                "pct": max(0.0, min(1.0, free_gb / total_gb)),
                                "icon": "database",
                                "default_color": "green",
                            }
                        if mclk > 0:
                            results[f"{gpu_slug}/mem_clock"] = {
                                "id": f"{gpu_slug}/mem_clock",
                                "category": cat,
                                "name": f"{name} - Memory Clock",
                                "short_label": "MCLK",
                                "value": round(mclk, 0),
                                "val_text": f"{mclk:.0f} MHz",
                                "unit": "MHz",
                                "min": 0,
                                "max": 10000,
                                "pct": max(0.0, min(1.0, mclk / 10000.0)),
                                "icon": "bolt",
                                "default_color": "purple",
                            }
                        if temp > 0:
                            results[f"{gpu_slug}/temp"] = {
                                "id": f"{gpu_slug}/temp",
                                "category": cat,
                                "name": f"{name} - Temperature",
                                "short_label": "GTEMP",
                                "value": round(temp, 1),
                                "val_text": f"{temp:.0f}°C",
                                "unit": "°C",
                                "min": 0,
                                "max": 105,
                                "pct": max(0.0, min(1.0, temp / 105.0)),
                                "icon": "flame",
                                "default_color": "orange",
                            }
                        if power > 0:
                            results[f"{gpu_slug}/power"] = {
                                "id": f"{gpu_slug}/power",
                                "category": cat,
                                "name": f"{name} - Power Draw",
                                "short_label": "GPWR",
                                "value": round(power, 1),
                                "val_text": f"{power:.1f} W",
                                "unit": "W",
                                "min": 0,
                                "max": 250,
                                "pct": max(0.0, min(1.0, power / 250.0)),
                                "icon": "bolt",
                                "default_color": "yellow",
                            }
            else:
                _NVIDIA_SMI_AVAILABLE = False
        except FileNotFoundError:
            _NVIDIA_SMI_AVAILABLE = False
        except (subprocess.TimeoutExpired, OSError):
            pass

    if not has_gpus:
        card_dirs = sorted(glob.glob("/sys/class/drm/card[0-9]"))
        for card in card_dirs:
            card_name = os.path.basename(card)
            load_path = os.path.join(card, "device", "gpu_busy_percent")
            if os.path.exists(load_path):
                txt = read_text(load_path)
                if txt.isdigit():
                    pct = float(txt)
                    results[f"gpu/{card_name}/load"] = {
                        "id": f"gpu/{card_name}/load",
                        "category": "GPU",
                        "name": f"DRM {card_name} GPU Load",
                        "short_label": "GPU",
                        "value": round(pct, 1),
                        "val_text": f"{pct:.0f}%",
                        "unit": "%",
                        "min": 0,
                        "max": 100,
                        "pct": max(0.0, min(1.0, pct / 100.0)),
                        "icon": "device-gamepad",
                        "default_color": "green",
                    }

    return results


def sample_hwmon() -> Dict[str, Dict[str, Any]]:
    results = {}
    hwmon_dirs = sorted(glob.glob("/sys/class/hwmon/hwmon*"))

    for hdir in hwmon_dirs:
        hname = read_text(os.path.join(hdir, "name"), os.path.basename(hdir))

        chip_label = hname
        if hname == "k10temp":
            chip_label = "AMD Ryzen Thermal"
        elif hname == "coretemp":
            chip_label = "Intel CPU Thermal"
        elif hname == "zenpower":
            chip_label = "AMD Zen Thermal"
        elif hname == "asus":
            chip_label = "ASUS Hardware"
        elif hname == "amdgpu":
            chip_label = "AMD Radeon GPU"
        elif hname == "nouveau":
            chip_label = "NVIDIA Nouveau GPU"
        elif hname.startswith("nvme"):
            chip_label = f"NVMe SSD ({hname})"

        for temp_input in sorted(glob.glob(os.path.join(hdir, "temp*_input"))):
            idx = re.search(r"temp(\d+)_input", temp_input)
            if not idx:
                continue
            num = idx.group(1)
            try:
                deg_c = int(read_text(temp_input)) / 1000.0
            except (ValueError, TypeError):
                continue
            if not (-40 <= deg_c <= 150):
                continue

            lbl = read_text(os.path.join(hdir, f"temp{num}_label"), f"Temp {num}")
            sensor_id = f"hwmon/{hname}/temp{num}"

            full_name = f"{chip_label} - {lbl}"
            if lbl == "Tctl":
                full_name = "CPU Package Temperature (Tctl)"
                results["cpu/package_temp"] = {
                    "id": "cpu/package_temp",
                    "category": "CPU",
                    "name": "CPU Package Temperature",
                    "short_label": "TEMP",
                    "value": round(deg_c, 1),
                    "val_text": f"{deg_c:.0f}°C",
                    "unit": "°C",
                    "min": 0,
                    "max": 105,
                    "pct": max(0.0, min(1.0, deg_c / 105.0)),
                    "icon": "flame",
                    "default_color": "orange",
                }

            results[sensor_id] = {
                "id": sensor_id,
                "category": "Thermals",
                "name": full_name,
                "short_label": "TEMP",
                "value": round(deg_c, 1),
                "val_text": f"{deg_c:.0f}°C",
                "unit": "°C",
                "min": 0,
                "max": 105,
                "pct": max(0.0, min(1.0, deg_c / 105.0)),
                "icon": "flame",
                "default_color": "orange",
            }

        for fan_input in sorted(glob.glob(os.path.join(hdir, "fan*_input"))):
            idx = re.search(r"fan(\d+)_input", fan_input)
            if not idx:
                continue
            num = idx.group(1)
            try:
                rpm = int(read_text(fan_input))
            except (ValueError, TypeError):
                continue
            if rpm < 0 or rpm > 20000:
                continue
            lbl = read_text(os.path.join(hdir, f"fan{num}_label"), f"Fan {num}")
            sensor_id = f"hwmon/{hname}/fan{num}"
            results[sensor_id] = {
                "id": sensor_id,
                "category": "Fans & Cooling",
                "name": f"{chip_label} - {lbl}",
                "short_label": "FAN",
                "value": rpm,
                "val_text": f"{rpm} RPM",
                "unit": "RPM",
                "min": 0,
                "max": 6000,
                "pct": max(0.0, min(1.0, rpm / 6000.0)),
                "icon": "car-fan",
                "default_color": "cyan",
            }

        for pwr_input in sorted(glob.glob(os.path.join(hdir, "power*_input"))):
            idx = re.search(r"power(\d+)_input", pwr_input)
            if not idx:
                continue
            num = idx.group(1)
            try:
                watts = int(read_text(pwr_input)) / 1000000.0
            except (ValueError, TypeError):
                continue
            if watts < 0 or watts > 2000:
                continue
            lbl = read_text(os.path.join(hdir, f"power{num}_label"), f"Power {num}")
            sensor_id = f"hwmon/{hname}/power{num}"
            results[sensor_id] = {
                "id": sensor_id,
                "category": "Power & Battery",
                "name": f"{chip_label} - {lbl} Draw",
                "short_label": "PWR",
                "value": round(watts, 1),
                "val_text": f"{watts:.1f} W",
                "unit": "W",
                "min": 0,
                "max": 250,
                "pct": max(0.0, min(1.0, watts / 250.0)),
                "icon": "bolt",
                "default_color": "yellow",
            }

    return results


def sample_network() -> Dict[str, Dict[str, Any]]:
    global _PREV_NET_BYTES
    results = {}
    now = time.time()

    try:
        with open("/proc/net/dev", "r", encoding="utf-8") as f:
            for line in f:
                if ":" not in line:
                    continue
                parts = line.split(":")
                iface = parts[0].strip()
                if iface in {"lo"} or iface.startswith(("docker", "veth", "br-", "virbr")):
                    continue

                vals = parts[1].split()
                rx_bytes = int(vals[0])
                tx_bytes = int(vals[8])

                prev_rx, prev_tx, prev_time = _PREV_NET_BYTES.get(iface, (rx_bytes, tx_bytes, now))
                _PREV_NET_BYTES[iface] = (rx_bytes, tx_bytes, now)

                dt = max(0.1, now - prev_time)
                rx_rate = max(0.0, (rx_bytes - prev_rx) / dt)
                tx_rate = max(0.0, (tx_bytes - prev_tx) / dt)

                human_name = f"Wi-Fi ({iface})" if iface.startswith("wl") else f"Ethernet ({iface})"
                icon = "wifi" if iface.startswith("wl") else "network"

                id_rx = f"net/{iface}/rx_rate"
                rx_mb = rx_rate / (1024 * 1024)
                results[id_rx] = {
                    "id": id_rx,
                    "category": "Network",
                    "name": f"{human_name} - Download Speed",
                    "short_label": "DL",
                    "value": round(rx_mb, 2),
                    "val_text": format_rate_human(rx_rate),
                    "unit": "rate",
                    "min": 0,
                    "max": 25.0,
                    "pct": min(1.0, rx_mb / 25.0),
                    "icon": icon,
                    "default_color": "blue",
                }

                id_tx = f"net/{iface}/tx_rate"
                tx_mb = tx_rate / (1024 * 1024)
                results[id_tx] = {
                    "id": id_tx,
                    "category": "Network",
                    "name": f"{human_name} - Upload Speed",
                    "short_label": "UL",
                    "value": round(tx_mb, 2),
                    "val_text": format_rate_human(tx_rate),
                    "unit": "rate",
                    "min": 0,
                    "max": 10.0,
                    "pct": min(1.0, tx_mb / 10.0),
                    "icon": icon,
                    "default_color": "orange",
                }
    except OSError:
        pass

    return results


def sample_battery() -> Dict[str, Dict[str, Any]]:
    results = {}
    for pdir in glob.glob("/sys/class/power_supply/*"):
        pname = os.path.basename(pdir)
        ptype = read_text(os.path.join(pdir, "type"))
        if ptype != "Battery":
            continue

        cap_txt = read_text(os.path.join(pdir, "capacity"))
        if cap_txt.isdigit():
            cap = int(cap_txt)
            status = read_text(os.path.join(pdir, "status"), "Discharging")
            id_bat = f"battery/{pname}/capacity"
            results[id_bat] = {
                "id": id_bat,
                "category": "Power & Battery",
                "name": f"Battery {pname} Charge ({status})",
                "short_label": "BAT",
                "value": cap,
                "val_text": f"{cap}%",
                "unit": "%",
                "min": 0,
                "max": 100,
                "pct": cap / 100.0,
                "icon": "battery-charging" if status == "Charging" else "battery",
                "default_color": "green" if cap > 25 else "red",
            }
    return results


def sample_psi() -> Dict[str, Dict[str, Any]]:
    results = {}
    for res in ["cpu", "memory", "io"]:
        path = f"/proc/pressure/{res}"
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("some"):
                        m = re.search(r"avg10=([\d\.]+)", line)
                        if m:
                            val = float(m.group(1))
                            id_psi = f"psi/{res}/stall"
                            results[id_psi] = {
                                "id": id_psi,
                                "category": "Linux PSI Pressure",
                                "name": f"Linux PSI: {res.upper()} Stall Pressure (10s)",
                                "short_label": f"PSI-{res.upper()[:1]}",
                                "value": round(val, 2),
                                "val_text": f"{val:.1f}%",
                                "unit": "%",
                                "min": 0,
                                "max": 100,
                                "pct": max(0.0, min(1.0, val / 100.0)),
                                "icon": "gauge",
                                "default_color": "red" if val > 20 else "purple",
                            }
        except OSError:
            pass
    return results


def collect_all_sensors() -> Dict[str, Dict[str, Any]]:
    catalog = {}
    catalog.update(sample_filesystems())
    catalog.update(sample_cpu())
    catalog.update(sample_memory())
    catalog.update(sample_gpus())
    catalog.update(sample_hwmon())
    catalog.update(sample_network())
    catalog.update(sample_battery())
    catalog.update(sample_psi())
    return catalog


# ==============================================================================
# Gauge Graphic Renderers
# ==============================================================================

def render_arc_gauge(
    pct: float,
    palette_name: str,
    size: int,
    start_ang: float,
    sweep: float,
    cx: float,
    cy: float,
    r_mid: float,
    stroke_width: int = 7,
    scale: int = 3,
) -> Image.Image:
    pct = max(0.0, min(1.0, pct))
    w, h = size * scale, size * scale
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    accent_rgb = resolve_color(palette_name)
    arc_width = stroke_width * scale
    cap_r = arc_width * 0.5
    bead_r = arc_width * 0.58
    r_outer = r_mid + arc_width / 2.0
    bbox = [cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer]

    end_ang = start_ang + sweep

    # Background track with rounded caps
    for ang in (start_ang, end_ang):
        rad = math.radians(ang)
        x = cx + r_mid * math.cos(rad)
        y = cy + r_mid * math.sin(rad)
        draw.ellipse([x - cap_r, y - cap_r, x + cap_r, y + cap_r], fill=TRACK_COLOR)
    draw.arc(bbox, start=start_ang, end=end_ang, fill=TRACK_COLOR, width=int(arc_width))

    # Active fill arc
    active_end = start_ang + (sweep * pct)
    if pct > 0.01:
        s_rad = math.radians(start_ang)
        sx = cx + r_mid * math.cos(s_rad)
        sy = cy + r_mid * math.sin(s_rad)
        draw.ellipse([sx - cap_r, sy - cap_r, sx + cap_r, sy + cap_r], fill=(*accent_rgb, 255))
        draw.arc(bbox, start=start_ang, end=active_end, fill=(*accent_rgb, 255), width=int(arc_width))

    # Needle indicator bead with specular center
    tip_rad = math.radians(active_end)
    tip_x = cx + r_mid * math.cos(tip_rad)
    tip_y = cy + r_mid * math.sin(tip_rad)
    draw.ellipse([tip_x - bead_r, tip_y - bead_r, tip_x + bead_r, tip_y + bead_r], fill=(*accent_rgb, 255))
    highlight_rgb = tuple(min(255, int(c * 1.35) + 30) for c in accent_rgb)
    core_r = bead_r * 0.45
    draw.ellipse([tip_x - core_r, tip_y - core_r, tip_x + core_r, tip_y + core_r], fill=(*highlight_rgb, 255))

    return img.resize((size, size), Image.Resampling.LANCZOS)


def render_full_tachometer(
    pct: float,
    palette_name: str,
    size: int,
    stroke_width: int = 7,
    scale: int = 3,
) -> Image.Image:
    w = size * scale
    arc_width = stroke_width * scale
    bead_r = arc_width * 0.58
    pad = int(bead_r + 3)
    r_outer = (w - 2 * pad) / 2.0
    r_mid = r_outer - arc_width / 2.0
    cx, cy = w / 2.0, w / 2.0
    return render_arc_gauge(pct, palette_name, size, 105.0, 330.0, cx, cy, r_mid, stroke_width, scale)


def render_quarter_tachometer(
    pct: float,
    palette_name: str,
    size: int,
    stroke_width: int = 7,
    scale: int = 3,
) -> Image.Image:
    w = size * scale
    arc_width = stroke_width * scale
    bead_r = arc_width * 0.58
    margin = int(bead_r + 2)
    cx = cy = w - margin
    r_mid = cx - margin
    return render_arc_gauge(pct, palette_name, size, 180.0, 90.0, cx, cy, r_mid, stroke_width, scale)


def render_vertical_bar(
    pct: float,
    palette_name: str,
    size: int,
    stroke_width: int = 7,
    scale: int = 3,
) -> Image.Image:
    pct = max(0.0, min(1.0, pct))
    w, h = size * scale, size * scale
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    accent_rgb = resolve_color(palette_name)
    bar_w = int(max(10, size * 0.28) * scale)
    cx = w // 2
    pad_y = int(4 * scale)

    x0 = cx - bar_w // 2
    x1 = cx + bar_w // 2
    y0 = pad_y
    y1 = h - pad_y
    radius = bar_w // 2

    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=TRACK_COLOR)

    total_height = y1 - y0
    fill_height = int(total_height * pct)
    if fill_height > 2:
        fill_y0 = y1 - fill_height
        draw.rounded_rectangle([x0, fill_y0, x1, y1], radius=radius, fill=(*accent_rgb, 255))

    return img.resize((size, size), Image.Resampling.LANCZOS)


def render_geometric_wifi(
    pct: float,
    palette_name: str,
    size: int,
    stroke_width: int = 7,
    scale: int = 3,
) -> Image.Image:
    pct = max(0.0, min(1.0, pct))
    w, h = size * scale, size * scale
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    accent_rgb = resolve_color(palette_name)
    arc_w = int(max(3, stroke_width * 0.7) * scale)
    cx = w // 2
    base_y = int(h * 0.88)

    thresholds = [0.08, 0.35, 0.65, 0.90]
    radii = [int(size * scale * factor) for factor in [0.20, 0.42, 0.64, 0.84]]
    start_ang, end_ang = 225.0, 315.0

    for i in range(4):
        r = radii[i]
        bbox = [cx - r, base_y - r, cx + r, base_y + r]
        color = (*accent_rgb, 255) if pct >= thresholds[i] else TRACK_COLOR
        draw.arc(bbox, start=start_ang, end=end_ang, fill=color, width=arc_w)

    dot_r = int(arc_w * 1.1)
    dot_color = (*accent_rgb, 255) if pct > 0.02 else TRACK_COLOR
    draw.ellipse([cx - dot_r, base_y - dot_r, cx + dot_r, base_y + dot_r], fill=dot_color)

    return img.resize((size, size), Image.Resampling.LANCZOS)


def render_geometric_cellular(
    pct: float,
    palette_name: str,
    size: int,
    stroke_width: int = 7,
    scale: int = 3,
) -> Image.Image:
    pct = max(0.0, min(1.0, pct))
    w, h = size * scale, size * scale
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    accent_rgb = resolve_color(palette_name)
    num_bars = 4
    thresholds = [0.08, 0.35, 0.65, 0.90]
    height_ratios = [0.30, 0.52, 0.74, 0.96]

    pad_x = int(6 * scale)
    pad_y = int(4 * scale)
    usable_w = w - (2 * pad_x)
    bar_w = int(usable_w / (num_bars * 1.5))
    gap = int((usable_w - (num_bars * bar_w)) / (num_bars - 1))
    bottom_y = h - pad_y

    for i in range(num_bars):
        bx0 = pad_x + i * (bar_w + gap)
        bx1 = bx0 + bar_w
        bar_h = int((h - 2 * pad_y) * height_ratios[i])
        by0 = bottom_y - bar_h

        color = (*accent_rgb, 255) if pct >= thresholds[i] else TRACK_COLOR
        radius = bar_w // 2
        draw.rounded_rectangle([bx0, by0, bx1, bottom_y], radius=radius, fill=color)

    return img.resize((size, size), Image.Resampling.LANCZOS)


def render_graph_for_instance(
    graph_type: str,
    pct: float,
    color: str,
    size: int,
    stroke_width: int = 7,
) -> Optional[Image.Image]:
    if not HAS_PIL:
        return None

    if graph_type == "tachometer_quarter":
        return render_quarter_tachometer(pct, color, size, stroke_width)
    elif graph_type == "bar_vertical":
        return render_vertical_bar(pct, color, size, stroke_width)
    elif graph_type == "geometric_wifi":
        return render_geometric_wifi(pct, color, size, stroke_width)
    elif graph_type == "geometric_cellular":
        return render_geometric_cellular(pct, color, size, stroke_width)
    return render_full_tachometer(pct, color, size, stroke_width)


# ==============================================================================
# Instance Config Storage & IPC
# ==============================================================================

def load_instances() -> Dict[str, Dict[str, Any]]:
    if os.path.exists(INSTANCES_FILE):
        try:
            with open(INSTANCES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and data:
                    return data
        except (OSError, json.JSONDecodeError):
            pass

    save_instances(DEFAULT_INSTANCES)
    return copy.deepcopy(DEFAULT_INSTANCES)


def save_instances(instances: Dict[str, Dict[str, Any]]) -> None:
    tmp_path = INSTANCES_FILE + f".tmp.{os.getpid()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(instances, f, indent=2)
        os.replace(tmp_path, INSTANCES_FILE)
    except OSError:
        pass


def process_incoming_commands(
    instances: Dict[str, Dict[str, Any]],
    poll_interval: float = 2.0,
    bar_size: int = 34,
    desktop_size: int = 96,
) -> Tuple[Dict[str, Dict[str, Any]], bool, float, int, int]:
    changed = False
    proc_file = CMD_FILE + f".proc.{os.getpid()}"
    try:
        os.replace(CMD_FILE, proc_file)
    except OSError:
        return instances, changed, poll_interval, bar_size, desktop_size

    cmd = None
    try:
        with open(proc_file, "r", encoding="utf-8") as f:
            cmd = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass
    finally:
        try:
            os.remove(proc_file)
        except OSError:
            pass

    if not cmd or not isinstance(cmd, dict):
        return instances, changed, poll_interval, bar_size, desktop_size

    action = cmd.get("action")
    if action == "configure":
        new_poll = cmd.get("poll_interval")
        if isinstance(new_poll, (int, float)) and 0.2 <= new_poll <= 30.0:
            poll_interval = float(new_poll)
        new_bar = cmd.get("bar_size")
        if isinstance(new_bar, int) and 20 <= new_bar <= 120:
            bar_size = new_bar
            changed = True
        new_desk = cmd.get("desktop_size")
        if isinstance(new_desk, int) and 40 <= new_desk <= 240:
            desktop_size = new_desk
            changed = True
    elif action == "save_instance":
        inst = cmd.get("instance")
        if inst and "id" in inst:
            instances[inst["id"]] = inst
            changed = True
    elif action == "ensure_instance":
        inst_id = cmd.get("instance_id")
        if inst_id and inst_id not in instances:
            instances[inst_id] = {
                "id": inst_id,
                "name": inst_id,
                "sensor_id": "cpu/total_usage",
                "color": "cyan",
                "show_icon": True,
                "icon_type": "glyph",
                "icon_val": "cpu",
                "show_graph": True,
                "graph_type": "tachometer_quarter",
                "stroke_width": 7,
                "show_value": True,
                "decimals": 0,
                "show_label": True,
                "label": "CPU",
            }
            changed = True
    elif action == "delete_instance":
        inst_id = cmd.get("instance_id")
        if inst_id in instances:
            del instances[inst_id]
            changed = True
    elif action == "reset_defaults":
        instances = copy.deepcopy(DEFAULT_INSTANCES)
        changed = True

    if changed and action != "configure":
        save_instances(instances)

    return instances, changed, poll_interval, bar_size, desktop_size


# ==============================================================================
# Snapshot Builder & Cache Management
# ==============================================================================

def build_snapshot(
    instances: Dict[str, Dict[str, Any]],
    all_sensors: Dict[str, Dict[str, Any]],
    bar_size: int = 34,
    desktop_size: int = 96,
) -> Dict[str, Any]:
    global _INSTANCE_FILES, _INSTANCE_RENDER_KEYS
    stamp = int(time.time() * 1000)
    rendered_instances = {}

    for inst_id, cfg in instances.items():
        sensor_id = cfg.get("sensor_id") or "cpu/total_usage"
        sensor = all_sensors.get(sensor_id)
        if not sensor and all_sensors:
            sensor = all_sensors.get("cpu/total_usage") or list(all_sensors.values())[0]

        val = sensor["value"] if sensor else 0.0
        pct = sensor["pct"] if sensor else 0.0
        val_text = sensor["val_text"] if sensor else "0"
        unit = sensor["unit"] if sensor else ""

        decimals = int(cfg.get("decimals") or 0)
        stroke_width = int(cfg.get("stroke_width") or 7)

        if isinstance(val, (int, float)):
            if unit == "%":
                val_text = f"{val:.{decimals}f}%"
            elif unit == "°C":
                val_text = f"{val:.{decimals}f}°C"
            elif unit in ("rate", "human"):
                pass
            elif unit:
                val_text = f"{val:.{decimals}f} {unit}"
            else:
                val_text = f"{val:.{decimals}f}"

        color = cfg.get("color") or "cyan"
        graph_type = cfg.get("graph_type") or "tachometer_quarter"

        if inst_id not in _INSTANCE_FILES:
            _INSTANCE_FILES[inst_id] = {}
        if inst_id not in _INSTANCE_RENDER_KEYS:
            _INSTANCE_RENDER_KEYS[inst_id] = {}

        bar_img_path = _INSTANCE_FILES[inst_id].get("bar", "")
        desk_img_path = _INSTANCE_FILES[inst_id].get("desk", "")

        if cfg.get("show_graph", True) and HAS_PIL:
            quantized_pct = round(pct, 3)
            bar_key = (graph_type, quantized_pct, color, bar_size, stroke_width)
            desk_stroke = max(stroke_width, int(round(stroke_width * (desktop_size / 34.0))))
            desk_key = (graph_type, quantized_pct, color, desktop_size, desk_stroke)

            if _INSTANCE_RENDER_KEYS[inst_id].get("bar") != bar_key or not bar_img_path or not os.path.exists(bar_img_path):
                img_bar = render_graph_for_instance(graph_type, pct, color, bar_size, stroke_width)
                if img_bar:
                    slot = "1" if bar_img_path.endswith("_0.png") else "0"
                    new_bar_path = os.path.join(ASSETS_DIR, f"{inst_id}_bar_{slot}.png")
                    img_bar.save(new_bar_path)
                    if bar_img_path and bar_img_path != new_bar_path:
                        try:
                            os.remove(bar_img_path)
                        except OSError:
                            pass
                    bar_img_path = new_bar_path
                    _INSTANCE_FILES[inst_id]["bar"] = new_bar_path
                    _INSTANCE_RENDER_KEYS[inst_id]["bar"] = bar_key

            if _INSTANCE_RENDER_KEYS[inst_id].get("desk") != desk_key or not desk_img_path or not os.path.exists(desk_img_path):
                img_desk = render_graph_for_instance(graph_type, pct, color, desktop_size, desk_stroke)
                if img_desk:
                    slot = "1" if desk_img_path.endswith("_0.png") else "0"
                    new_desk_path = os.path.join(ASSETS_DIR, f"{inst_id}_desk_{slot}.png")
                    img_desk.save(new_desk_path)
                    if desk_img_path and desk_img_path != new_desk_path:
                        try:
                            os.remove(desk_img_path)
                        except OSError:
                            pass
                    desk_img_path = new_desk_path
                    _INSTANCE_FILES[inst_id]["desk"] = new_desk_path
                    _INSTANCE_RENDER_KEYS[inst_id]["desk"] = desk_key

        rendered_instances[inst_id] = {
            "id": inst_id,
            "name": cfg.get("name") or inst_id,
            "sensor_id": sensor_id,
            "sensor_name": sensor["name"] if sensor else sensor_id,
            "value": val,
            "val_text": val_text,
            "unit": unit,
            "pct": pct,
            "color": color,
            "show_icon": cfg.get("show_icon", True),
            "icon_type": cfg.get("icon_type", "glyph"),
            "icon_val": cfg.get("icon_val", "cpu"),
            "show_graph": cfg.get("show_graph", True),
            "graph_type": graph_type,
            "stroke_width": stroke_width,
            "show_value": cfg.get("show_value", True),
            "decimals": decimals,
            "show_label": cfg.get("show_label", True),
            "label": cfg.get("label", "CPU"),
            "bar_img": bar_img_path,
            "desk_img": desk_img_path,
            "bar_size": bar_size,
            "desktop_size": desktop_size,
        }

    catalog_list = list(all_sensors.values())
    catalog_list.sort(key=lambda s: (s["category"], s["name"]))

    return {
        "stamp": str(stamp),
        "instances": rendered_instances,
        "catalog": catalog_list,
        "categories": sorted(list(set(s["category"] for s in catalog_list))),
    }


def write_snapshot_file(snapshot_json: str) -> None:
    tmp_snap = SNAPSHOT_FILE + f".tmp.{os.getpid()}"
    try:
        with open(tmp_snap, "w", encoding="utf-8") as f:
            f.write(snapshot_json)
        os.replace(tmp_snap, SNAPSHOT_FILE)
    except OSError:
        pass


def cleanup_stale_assets() -> None:
    try:
        for p in glob.glob(os.path.join(ASSETS_DIR, "*.png")):
            os.remove(p)
    except OSError:
        pass


# ==============================================================================
# Main Runner (Daemon & One-shot)
# ==============================================================================

def run_daemon(poll_interval: float, bar_size: int, desktop_size: int) -> None:
    global _PREV_CPU_TIMES, _PREV_NET_BYTES
    _PREV_CPU_TIMES, _PREV_NET_BYTES = load_daemon_state()
    cleanup_stale_assets()

    pid_fd = None
    try:
        pid_fd = os.open(DAEMON_PID_FILE, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(pid_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.ftruncate(pid_fd, 0)
        os.write(pid_fd, f"{os.getpid()}\n".encode())
    except (BlockingIOError, OSError):
        if pid_fd is not None:
            try:
                os.close(pid_fd)
            except OSError:
                pass
        sys.exit(0)

    try:
        instances = load_instances()
        while True:
            instances, _, poll_interval, bar_size, desktop_size = process_incoming_commands(
                instances, poll_interval, bar_size, desktop_size
            )
            all_sensors = collect_all_sensors()
            snapshot = build_snapshot(instances, all_sensors, bar_size, desktop_size)
            snapshot_json = json.dumps(snapshot)

            write_snapshot_file(snapshot_json)

            try:
                sys.stdout.write(snapshot_json + "\n")
                sys.stdout.flush()
            except (BrokenPipeError, IOError):
                break

            deadline = time.monotonic() + poll_interval
            while time.monotonic() < deadline:
                if os.path.exists(CMD_FILE):
                    break
                time.sleep(min(0.25, max(0.05, deadline - time.monotonic())))
    except (BrokenPipeError, KeyboardInterrupt):
        pass
    finally:
        save_daemon_state(_PREV_CPU_TIMES, _PREV_NET_BYTES)
        if pid_fd is not None:
            try:
                fcntl.flock(pid_fd, fcntl.LOCK_UN)
                os.close(pid_fd)
                if os.path.exists(DAEMON_PID_FILE):
                    os.remove(DAEMON_PID_FILE)
            except OSError:
                pass


def run_once(bar_size: int, desktop_size: int) -> None:
    global _PREV_CPU_TIMES, _PREV_NET_BYTES
    _PREV_CPU_TIMES, _PREV_NET_BYTES = load_daemon_state()

    instances = load_instances()
    instances, _, _, bar_size, desktop_size = process_incoming_commands(instances, 2.0, bar_size, desktop_size)

    all_sensors = collect_all_sensors()
    snapshot = build_snapshot(instances, all_sensors, bar_size, desktop_size)
    snapshot_json = json.dumps(snapshot)

    write_snapshot_file(snapshot_json)
    save_daemon_state(_PREV_CPU_TIMES, _PREV_NET_BYTES)

    print(json.dumps({"status": "ok", "instances": len(snapshot["instances"]), "sensors": len(snapshot["catalog"])}))


def main():
    parser = argparse.ArgumentParser(description="TachoPulse Telemetry Engine")
    parser.add_argument("--daemon", action="store_true", help="Run as background streaming daemon")
    parser.add_argument("--bar-size", type=int, default=34, help="Bar gauge pixel size (20-120)")
    parser.add_argument("--desktop-size", type=int, default=96, help="Desktop gauge pixel size (40-240)")
    parser.add_argument("--poll", "--poll-interval", dest="poll_interval", type=float, default=2.0, help="Poll interval in seconds (0.5-10.0)")
    args = parser.parse_args()

    bar_size = max(20, min(120, args.bar_size))
    desktop_size = max(40, min(240, args.desktop_size))
    poll_interval = max(0.5, min(10.0, args.poll_interval))

    if args.daemon:
        run_daemon(poll_interval, bar_size, desktop_size)
    else:
        run_once(bar_size, desktop_size)


if __name__ == "__main__":
    main()
