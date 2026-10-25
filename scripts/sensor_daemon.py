#!/usr/bin/env python3
"""
TachoPulse Sensor Discovery & Supersampled Graph Daemon for Noctalia v5.

Sensor identification and classification logic adapted from KDE Plasma's
`ksystemstats` and `libksysguard` plugins:
  - cpu:       /proc/stat, cpufreq sysfs, coretemp/k10temp hwmon
  - gpu:       DRM sysfs (/sys/class/drm/card*) + nvtop -s / nvidia-smi / fdinfo
  - memory:    /proc/meminfo (MemTotal - MemAvailable, Application, Swap)
  - lmsensors: /sys/class/hwmon/hwmon* (temp*, fan*, power*, in*, curr*)
  - disks:     /proc/diskstats + os.statvfs
  - network:   /sys/class/net/* + /proc/net/dev
  - power:     /sys/class/power_supply/*
  - pressure:  /proc/pressure/{cpu,memory,io} (Linux PSI)

License: GPL-2.0-or-later (crediting KDE Project Contributors for ksystemstats
sensor classification and tordex/nvtop for capsule-arc gauge inspiration).
"""

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
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

RUNTIME_BASE = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/dev/shm"),
    "tachopulse",
)
STATE_FILE = os.path.join(RUNTIME_BASE, "state.json")
CONFIG_FILE = os.path.join(RUNTIME_BASE, "config.json")
SNAPSHOT_FILE = os.path.join(RUNTIME_BASE, "snapshot.json")
MAX_HISTORY = 40

PALETTE_COLORS: Dict[str, Tuple[int, int, int]] = {
    "blue": (120, 180, 255),
    "cyan": (86, 214, 214),
    "green": (115, 230, 155),
    "orange": (255, 185, 110),
    "purple": (195, 145, 255),
    "red": (255, 110, 125),
    "yellow": (250, 225, 115),
}

SKINS = {
    "dark": {
        "track": (42, 45, 62, 235),
        "bg_card": (26, 28, 40, 210),
        "text": (238, 242, 252, 255),
        "subtext": (150, 160, 182, 255),
        "grid": (58, 62, 84, 150),
    },
    "light": {
        "track": (205, 210, 224, 235),
        "bg_card": (242, 244, 250, 220),
        "text": (28, 32, 42, 255),
        "subtext": (92, 100, 118, 255),
        "grid": (185, 192, 210, 150),
    },
}


def read_text(path: str, default: str = "") -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return default


def read_float(path: str, scale: float = 1.0) -> Optional[float]:
    raw = read_text(path)
    if not raw:
        return None
    try:
        return float(raw) * scale
    except ValueError:
        return None


def format_bytes(num_bytes: float) -> str:
    if num_bytes < 0:
        num_bytes = 0
    gb = num_bytes / (1024 ** 3)
    if gb >= 1.0:
        return f"{gb:.1f}G"
    mb = num_bytes / (1024 ** 2)
    if mb >= 1.0:
        return f"{mb:.0f}M"
    kb = num_bytes / 1024.0
    return f"{kb:.0f}K"


def format_rate(bytes_per_sec: float) -> str:
    if bytes_per_sec < 0:
        bytes_per_sec = 0
    mb = bytes_per_sec / (1024 ** 2)
    if mb >= 1.0:
        return f"{mb:.1f}M/s"
    kb = bytes_per_sec / 1024.0
    if kb >= 1.0:
        return f"{kb:.0f}K/s"
    return f"{bytes_per_sec:.0f}B/s"


# ---------------------------------------------------------------------------
# Font helper for Pillow 3x supersampled rendering
# ---------------------------------------------------------------------------
_FONT_CACHE: Dict[Tuple[int, bool], Any] = {}


def get_font(px_size: int, bold: bool = False) -> Any:
    key = (px_size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    candidates = (
        [
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
            "DejaVuSans-Bold.ttf",
        ]
        if bold
        else [
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/usr/share/fonts/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
            "DejaVuSans.ttf",
        ]
    )
    for name in candidates:
        try:
            font = ImageFont.truetype(name, px_size)
            _FONT_CACHE[key] = font
            return font
        except OSError:
            continue
    font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


# ---------------------------------------------------------------------------
# KDE ksystemstats-inspired Sensor Discovery & Polling Engine
# ---------------------------------------------------------------------------
class KdeStyleSensorEngine:
    def __init__(self, prev_state: Dict[str, Any], respect_gpu_sleep: bool = False):
        self.prev_state = prev_state
        self.respect_gpu_sleep = respect_gpu_sleep
        self.now = time.time()
        self.dt = max(0.05, self.now - float(prev_state.get("timestamp", self.now - 1.0)))
        self.new_state: Dict[str, Any] = {
            "timestamp": self.now,
            "cpu_ticks": {},
            "disk_io": {},
            "net_io": {},
            "history": prev_state.get("history", {}),
        }
        self.sensors: Dict[str, Dict[str, Any]] = {}

    def register(
        self,
        sensor_id: str,
        category: str,
        name: str,
        short_label: str,
        value: float,
        unit: str,
        val_text: str,
        sub_text: str = "",
        percent: Optional[float] = None,
        min_val: float = 0.0,
        max_val: float = 100.0,
        icon: str = "cpu",
        default_color: str = "blue",
    ) -> None:
        if percent is None:
            if max_val > min_val:
                percent = max(0.0, min(100.0, (value - min_val) / (max_val - min_val) * 100.0))
            else:
                percent = 0.0
        else:
            percent = max(0.0, min(100.0, float(percent)))

        hist_map = self.new_state["history"]
        hist = list(hist_map.get(sensor_id, []))
        hist.append(round(percent, 1))
        if len(hist) > MAX_HISTORY:
            hist = hist[-MAX_HISTORY:]
        hist_map[sensor_id] = hist

        self.sensors[sensor_id] = {
            "id": sensor_id,
            "category": category,
            "name": name,
            "short_label": short_label,
            "value": round(float(value), 2),
            "unit": unit,
            "val_text": val_text,
            "sub_text": sub_text,
            "percent": round(percent, 1),
            "history": hist,
            "icon": icon,
            "default_color": default_color,
        }

    def collect_all(self) -> Dict[str, Dict[str, Any]]:
        hwmon_summary = self._collect_lmsensors()
        self._collect_cpu(hwmon_summary)
        self._collect_memory()
        self._collect_gpus()
        self._collect_disks()
        self._collect_network()
        self._collect_power()
        self._collect_pressure()
        return self.sensors

    def _collect_lmsensors(self) -> Dict[str, Any]:
        """Scans /sys/class/hwmon/hwmon* using KDE lmsensors plugin conventions."""
        summary: Dict[str, Any] = {"cpu_pkg_temp": None, "cpu_max_temp": None}
        chip_counts: Dict[str, int] = {}
        # Sort by real device path so multi-instance chips (e.g. DDR5 spd5118) have reboot-stable order
        hw_dirs = sorted(
            glob.glob("/sys/class/hwmon/hwmon*"),
            key=lambda p: (
                read_text(os.path.join(p, "name"), ""),
                os.path.realpath(os.path.join(p, "device")) if os.path.exists(os.path.join(p, "device")) else p,
            ),
        )
        for hw_dir in hw_dirs:
            chip = read_text(os.path.join(hw_dir, "name"), os.path.basename(hw_dir))
            cnt = chip_counts.get(chip, 0) + 1
            chip_counts[chip] = cnt
            chip_key = chip if cnt == 1 else f"{chip}_{cnt}"

            # Temperatures (millidegrees C)
            for inp in sorted(glob.glob(os.path.join(hw_dir, "temp*_input"))):
                prefix = inp[:-6]  # strip _input
                t_id = os.path.basename(prefix)
                val_c = read_float(inp, 0.001)
                if val_c is None or val_c <= -50 or val_c >= 200:
                    continue
                label = read_text(prefix + "_label", f"{chip} {t_id}")
                crit = read_float(prefix + "_crit", 0.001) or read_float(prefix + "_max", 0.001) or 100.0
                if crit <= 0 or crit > 150:
                    crit = 100.0

                if chip in ("coretemp", "k10temp", "zenpower", "cpu_thermal"):
                    if "package" in label.lower() or "tctl" in label.lower() or "tdie" in label.lower() or summary["cpu_pkg_temp"] is None:
                        summary["cpu_pkg_temp"] = val_c
                    if summary["cpu_max_temp"] is None or val_c > summary["cpu_max_temp"]:
                        summary["cpu_max_temp"] = val_c

                sid = f"lmsensors/{chip_key}/{t_id}"
                short = label[:6].upper() if len(label) <= 6 else chip[:6].upper()
                self.register(
                    sensor_id=sid,
                    category="Hardware Sensors (hwmon)",
                    name=f"{chip_key}: {label} Temp",
                    short_label=short,
                    value=val_c,
                    unit="°C",
                    val_text=f"{val_c:.0f}°C",
                    sub_text=f"max {crit:.0f}°C",
                    min_val=20.0,
                    max_val=crit,
                    icon="temperature",
                    default_color="orange" if val_c >= 70 else "cyan",
                )

            # Fans (RPM)
            for inp in sorted(glob.glob(os.path.join(hw_dir, "fan*_input"))):
                prefix = inp[:-6]
                f_id = os.path.basename(prefix)
                rpm = read_float(inp, 1.0)
                if rpm is None or rpm < 0:
                    continue
                label = read_text(prefix + "_label", f"{chip} {f_id}")
                max_rpm = read_float(prefix + "_max", 1.0) or 5500.0
                if max_rpm <= 0:
                    max_rpm = 5500.0
                sid = f"lmsensors/{chip_key}/{f_id}"
                short = "CPUFAN" if "cpu" in label.lower() else ("GPUFAN" if "gpu" in label.lower() else "FAN")
                self.register(
                    sensor_id=sid,
                    category="Fans & Cooling",
                    name=f"{chip_key}: {label}",
                    short_label=short,
                    value=rpm,
                    unit="RPM",
                    val_text=f"{rpm:.0f}",
                    sub_text="RPM",
                    min_val=0.0,
                    max_val=max_rpm,
                    icon="car-fan",
                    default_color="green",
                )

            # Power sensors (microwatts -> Watts)
            for inp in sorted(glob.glob(os.path.join(hw_dir, "power*_input"))):
                prefix = inp[:-6]
                p_id = os.path.basename(prefix)
                watts = read_float(inp, 1e-6)
                if watts is None or watts < 0:
                    continue
                label = read_text(prefix + "_label", f"{chip} {p_id}")
                max_w = read_float(prefix + "_max", 1e-6) or 100.0
                sid = f"lmsensors/{chip_key}/{p_id}"
                self.register(
                    sensor_id=sid,
                    category="Power & Voltage",
                    name=f"{chip_key}: {label} Power",
                    short_label="PWR",
                    value=watts,
                    unit="W",
                    val_text=f"{watts:.1f}W",
                    sub_text=chip_key,
                    min_val=0.0,
                    max_val=max_w,
                    icon="bolt",
                    default_color="yellow",
                )

        return summary

    def _collect_cpu(self, hwmon_summary: Dict[str, Any]) -> None:
        prev_ticks = self.prev_state.get("cpu_ticks", {})
        cur_ticks: Dict[str, Tuple[int, int]] = {}
        usages: Dict[str, float] = {}

        try:
            with open("/proc/stat", "r", encoding="utf-8") as f:
                for line in f:
                    if not line.startswith("cpu"):
                        continue
                    parts = line.split()
                    cpu_name = parts[0]
                    nums = [int(x) for x in parts[1:9]]
                    idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
                    total = sum(nums)
                    cur_ticks[cpu_name] = (total, idle)
                    if cpu_name in prev_ticks:
                        p_tot, p_idle = prev_ticks[cpu_name]
                        d_tot = total - p_tot
                        d_idle = idle - p_idle
                        if d_tot > 0:
                            usages[cpu_name] = max(0.0, min(100.0, (1.0 - d_idle / d_tot) * 100.0))
                        else:
                            usages[cpu_name] = 0.0
                    else:
                        usages[cpu_name] = 0.0
        except OSError:
            pass

        self.new_state["cpu_ticks"] = cur_ticks

        # CPU frequencies (kHz -> GHz)
        freqs_ghz: List[float] = []
        for fpath in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq"):
            ghz = read_float(fpath, 1e-6)
            if ghz is not None:
                freqs_ghz.append(ghz)
        avg_ghz = sum(freqs_ghz) / len(freqs_ghz) if freqs_ghz else 0.0
        max_ghz = max(freqs_ghz) if freqs_ghz else 0.0

        pkg_temp = hwmon_summary.get("cpu_pkg_temp")
        sub_cpu = f"{pkg_temp:.0f}°C" if pkg_temp is not None else (f"{avg_ghz:.1f}GHz" if avg_ghz > 0 else "")

        total_usage = usages.get("cpu", 0.0)
        self.register(
            sensor_id="cpu/all/usage",
            category="CPU",
            name="CPU Total Load",
            short_label="CPU",
            value=total_usage,
            unit="%",
            val_text=f"{total_usage:.0f}%",
            sub_text=sub_cpu,
            percent=total_usage,
            icon="cpu",
            default_color="blue",
        )

        if pkg_temp is not None:
            self.register(
                sensor_id="cpu/all/temperature",
                category="CPU",
                name="CPU Package Temperature",
                short_label="CPUTMP",
                value=pkg_temp,
                unit="°C",
                val_text=f"{pkg_temp:.0f}°C",
                sub_text=f"{avg_ghz:.1f}GHz" if avg_ghz > 0 else "Package",
                min_val=25.0,
                max_val=100.0,
                icon="temperature",
                default_color="orange",
            )

        if avg_ghz > 0:
            self.register(
                sensor_id="cpu/all/averageFrequency",
                category="CPU",
                name="CPU Average Clock",
                short_label="CLK",
                value=avg_ghz,
                unit="GHz",
                val_text=f"{avg_ghz:.2f}G",
                sub_text=f"max {max_ghz:.1f}G",
                min_val=0.4,
                max_val=max(5.5, max_ghz),
                icon="bolt",
                default_color="cyan",
            )

        try:
            l1, l5, _ = os.getloadavg()
            ncpu = max(1, os.cpu_count() or 1)
            self.register(
                sensor_id="cpu/all/loadaverage1",
                category="CPU",
                name="System Load Average (1m)",
                short_label="LOAD",
                value=l1,
                unit="",
                val_text=f"{l1:.2f}",
                sub_text=f"5m {l5:.1f}",
                min_val=0.0,
                max_val=float(ncpu),
                icon="activity",
                default_color="purple",
            )
        except OSError:
            pass

        # Individual CPU cores (sorted numerically)
        core_keys = sorted(
            [k for k in usages.keys() if k != "cpu"],
            key=lambda x: int(x[3:]) if x[3:].isdigit() else 999,
        )
        for ck in core_keys:
            idx = ck[3:]
            u = usages[ck]
            self.register(
                sensor_id=f"cpu/{ck}/usage",
                category="CPU Cores",
                name=f"CPU Core {idx} Load",
                short_label=f"C{idx}",
                value=u,
                unit="%",
                val_text=f"{u:.0f}%",
                sub_text=f"Core {idx}",
                percent=u,
                icon="cpu",
                default_color="blue",
            )

    def _collect_memory(self) -> None:
        info: Dict[str, int] = {}
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        k = parts[0].strip()
                        v_parts = parts[1].strip().split()
                        if v_parts and v_parts[0].isdigit():
                            info[k] = int(v_parts[0]) * 1024
        except OSError:
            return

        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        if total > 0:
            used = max(0, total - avail)
            pct = used / total * 100.0
            self.register(
                sensor_id="memory/physical/usedPercent",
                category="Memory",
                name="RAM Usage",
                short_label="RAM",
                value=pct,
                unit="%",
                val_text=format_bytes(used),
                sub_text=f"+{format_bytes(avail)}",
                percent=pct,
                icon="server-2",
                default_color="purple",
            )

        swap_total = info.get("SwapTotal", 0)
        swap_free = info.get("SwapFree", 0)
        if swap_total > 0:
            swap_used = max(0, swap_total - swap_free)
            swap_pct = swap_used / swap_total * 100.0
            self.register(
                sensor_id="memory/swap/usedPercent",
                category="Memory",
                name="Swap Usage",
                short_label="SWAP",
                value=swap_pct,
                unit="%",
                val_text=format_bytes(swap_used),
                sub_text=f"{swap_pct:.0f}%",
                percent=swap_pct,
                icon="arrows-exchange",
                default_color="orange",
            )

    def _collect_gpus(self) -> None:
        """
        Discovers all GPUs dynamically via `nvtop -s` (which supports NVIDIA,
        AMD, and Intel iGPUs including per-process fdinfo memory/load) and
        supplements with /sys/class/drm/card* sysfs.
        """
        nvtop_gpus: List[Dict[str, Any]] = []
        try:
            proc = subprocess.run(
                ["nvtop", "-s"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=1.5,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                parsed = json.loads(proc.stdout)
                if isinstance(parsed, list):
                    nvtop_gpus = parsed
        except Exception:
            nvtop_gpus = []

        # Also inspect /sys/class/drm/card* for Intel GT clocks or cards not in nvtop
        drm_cards: List[Dict[str, Any]] = []
        for card_path in sorted(glob.glob("/sys/class/drm/card[0-9]*")):
            base = os.path.basename(card_path)
            if "-" in base:
                continue
            vendor = read_text(os.path.join(card_path, "device/vendor")).lower()
            runtime_status = read_text(os.path.join(card_path, "power/runtime_status"), "active")
            gt_cur = read_float(os.path.join(card_path, "gt_cur_freq_mhz")) or read_float(
                os.path.join(card_path, "gt/gt0/rps_cur_freq_mhz")
            )
            gt_max = read_float(os.path.join(card_path, "gt_max_freq_mhz")) or read_float(
                os.path.join(card_path, "gt/gt0/rps_max_freq_mhz")
            )
            busy_pct = read_float(os.path.join(card_path, "device/gpu_busy_percent"))
            drm_cards.append(
                {
                    "card": base,
                    "vendor": vendor,
                    "runtime_status": runtime_status,
                    "gt_cur_mhz": gt_cur,
                    "gt_max_mhz": gt_max,
                    "busy_pct": busy_pct,
                }
            )

        is_multi_gpu = len(nvtop_gpus) > 1 or len(drm_cards) > 1
        for idx, g in enumerate(nvtop_gpus):
            if not isinstance(g, dict):
                continue
            dev_name = str(g.get("device_name") or f"GPU {idx}")
            short_tag = f"GPU{idx}"
            if "nvidia" in dev_name.lower() or "rtx" in dev_name.lower() or "gtx" in dev_name.lower():
                short_tag = "dGPU" if is_multi_gpu else "GPU"
            elif "intel" in dev_name.lower() or "uhd" in dev_name.lower() or "iris" in dev_name.lower():
                short_tag = "iGPU" if is_multi_gpu else "GPU"
            elif "amd" in dev_name.lower() or "radeon" in dev_name.lower():
                short_tag = f"GPU{idx}" if is_multi_gpu else "GPU"

            util_str = str(g.get("gpu_util") or "0%").replace("%", "").strip()
            util_pct = float(util_str) if util_str.replace(".", "", 1).isdigit() else 0.0
            temp_str = str(g.get("temp") or "")
            power_str = str(g.get("power_draw") or "")
            clock_str = str(g.get("gpu_clock") or "")

            sub = temp_str if temp_str and temp_str != "None" else (clock_str if clock_str and clock_str != "None" else short_tag)

            self.register(
                sensor_id=f"gpu/gpu{idx}/usage",
                category=f"GPU {idx}: {dev_name}",
                name=f"{dev_name} Load",
                short_label=short_tag,
                value=util_pct,
                unit="%",
                val_text=f"{util_pct:.0f}%",
                sub_text=sub,
                percent=util_pct,
                icon="device-desktop",
                default_color="blue" if idx == 0 else "cyan",
            )

            # VRAM
            mem_used = g.get("mem_used")
            mem_total = g.get("mem_total")
            if mem_used is not None and mem_total is not None:
                try:
                    u_b = float(mem_used)
                    t_b = float(mem_total)
                    if t_b > 0:
                        f_b = max(0.0, t_b - u_b)
                        vram_pct = u_b / t_b * 100.0
                        vram_label = "VRAM" if short_tag in ("GPU", "dGPU") else f"{short_tag}M"
                        self.register(
                            sensor_id=f"gpu/gpu{idx}/usedVramPct",
                            category=f"GPU {idx}: {dev_name}",
                            name=f"{dev_name} Memory",
                            short_label=vram_label,
                            value=vram_pct,
                            unit="%",
                            val_text=format_bytes(u_b),
                            sub_text=f"+{format_bytes(f_b)}",
                            percent=vram_pct,
                            icon="cpu-2",
                            default_color="blue" if idx == 0 else "cyan",
                        )
                except (ValueError, TypeError):
                    pass

            # Temperature
            if temp_str and temp_str != "None":
                m_t = re.search(r"(\d+)", temp_str)
                if m_t:
                    t_val = float(m_t.group(1))
                    self.register(
                        sensor_id=f"gpu/gpu{idx}/temperature",
                        category=f"GPU {idx}: {dev_name}",
                        name=f"{dev_name} Temperature",
                        short_label=f"{short_tag}T",
                        value=t_val,
                        unit="°C",
                        val_text=f"{t_val:.0f}°C",
                        sub_text=power_str if power_str and power_str != "None" else short_tag,
                        min_val=25.0,
                        max_val=95.0,
                        icon="temperature",
                        default_color="orange",
                    )

            # Power
            if power_str and power_str != "None":
                m_p = re.search(r"(\d+(?:\.\d+)?)", power_str)
                if m_p:
                    p_val = float(m_p.group(1))
                    self.register(
                        sensor_id=f"gpu/gpu{idx}/power",
                        category=f"GPU {idx}: {dev_name}",
                        name=f"{dev_name} Power Draw",
                        short_label=f"{short_tag}W",
                        value=p_val,
                        unit="W",
                        val_text=f"{p_val:.0f}W",
                        sub_text=clock_str if clock_str and clock_str != "None" else short_tag,
                        min_val=0.0,
                        max_val=140.0,
                        icon="bolt",
                        default_color="yellow",
                    )

        # Supplement with DRM sysfs cards (e.g. Intel GT frequency or when inside sandbox)
        has_intel_in_nvtop = any(
            "intel" in str(g.get("device_name", "")).lower() or "uhd" in str(g.get("device_name", "")).lower()
            for g in nvtop_gpus
        )
        for dc in drm_cards:
            card_name = dc["card"]
            if dc["vendor"] == "0x8086" and dc["gt_cur_mhz"] is not None:
                cur_mhz = dc["gt_cur_mhz"]
                max_mhz = dc["gt_max_mhz"] or 1450.0
                pct = max(0.0, min(100.0, cur_mhz / max_mhz * 100.0))
                if not has_intel_in_nvtop:
                    idx = len(nvtop_gpus)
                    self.register(
                        sensor_id=f"gpu/gpu{idx}/usage",
                        category=f"GPU {idx}: Intel Integrated Graphics ({card_name})",
                        name="Intel iGPU Activity / Clock",
                        short_label="iGPU",
                        value=pct,
                        unit="%",
                        val_text=f"{pct:.0f}%",
                        sub_text=f"{cur_mhz:.0f}MHz",
                        percent=pct,
                        icon="device-desktop",
                        default_color="cyan",
                    )
                self.register(
                    sensor_id=f"gpu/{card_name}/gtFrequency",
                    category=f"DRM GPU ({card_name})",
                    name=f"Intel iGPU GT Frequency ({card_name})",
                    short_label="iCLK",
                    value=cur_mhz,
                    unit="MHz",
                    val_text=f"{cur_mhz:.0f}M",
                    sub_text=f"max {max_mhz:.0f}M",
                    min_val=0.0,
                    max_val=max_mhz,
                    icon="bolt",
                    default_color="cyan",
                )

    def _collect_disks(self) -> None:
        # Root filesystem usage
        try:
            st = os.statvfs("/")
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
            if total > 0:
                used = max(0, total - free)
                pct = used / total * 100.0
                self.register(
                    sensor_id="disk/root/usedPercent",
                    category="Disks & Storage",
                    name="Root Filesystem (/) Usage",
                    short_label="DISK",
                    value=pct,
                    unit="%",
                    val_text=format_bytes(used),
                    sub_text=f"+{format_bytes(free)}",
                    percent=pct,
                    icon="database",
                    default_color="green",
                )
        except OSError:
            pass

        # Block device I/O rates from /proc/diskstats
        prev_io = self.prev_state.get("disk_io", {})
        cur_io: Dict[str, Tuple[int, int]] = {}
        block_devs = {
            os.path.basename(p)
            for p in glob.glob("/sys/block/*")
            if not os.path.basename(p).startswith(("loop", "ram", "zram", "dm-"))
        }
        try:
            with open("/proc/diskstats", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 14:
                        continue
                    dev = parts[2]
                    if dev not in block_devs:
                        continue
                    r_bytes = int(parts[5]) * 512
                    w_bytes = int(parts[9]) * 512
                    cur_io[dev] = (r_bytes, w_bytes)
                    if dev in prev_io:
                        pr, pw = prev_io[dev]
                        r_rate = max(0.0, (r_bytes - pr) / self.dt)
                        w_rate = max(0.0, (w_bytes - pw) / self.dt)
                    else:
                        r_rate, w_rate = 0.0, 0.0

                    self.register(
                        sensor_id=f"disk/{dev}/read",
                        category="Disks & Storage",
                        name=f"Disk {dev} Read Speed",
                        short_label="DSKR",
                        value=r_rate / (1024 ** 2),
                        unit="MB/s",
                        val_text=format_rate(r_rate),
                        sub_text=dev,
                        min_val=0.0,
                        max_val=500.0,
                        icon="arrow-down",
                        default_color="green",
                    )
                    self.register(
                        sensor_id=f"disk/{dev}/write",
                        category="Disks & Storage",
                        name=f"Disk {dev} Write Speed",
                        short_label="DSKW",
                        value=w_rate / (1024 ** 2),
                        unit="MB/s",
                        val_text=format_rate(w_rate),
                        sub_text=dev,
                        min_val=0.0,
                        max_val=500.0,
                        icon="arrow-up",
                        default_color="orange",
                    )
        except OSError:
            pass
        self.new_state["disk_io"] = cur_io

    def _collect_network(self) -> None:
        prev_net = self.prev_state.get("net_io", {})
        cur_net: Dict[str, Tuple[int, int]] = {}
        total_rx = 0.0
        total_tx = 0.0

        try:
            with open("/proc/net/dev", "r", encoding="utf-8") as f:
                for line in f:
                    if ":" not in line:
                        continue
                    iface, rest = line.split(":", 1)
                    iface = iface.strip()
                    if iface == "lo" or iface.startswith(("veth", "docker", "br-", "virbr")):
                        continue
                    fields = rest.split()
                    if len(fields) < 10:
                        continue
                    rx_b = int(fields[0])
                    tx_b = int(fields[8])
                    cur_net[iface] = (rx_b, tx_b)
                    if iface in prev_net:
                        prx, ptx = prev_net[iface]
                        rx_rate = max(0.0, (rx_b - prx) / self.dt)
                        tx_rate = max(0.0, (tx_b - ptx) / self.dt)
                    else:
                        rx_rate, tx_rate = 0.0, 0.0

                    total_rx += rx_rate
                    total_tx += tx_rate

                    self.register(
                        sensor_id=f"network/{iface}/download",
                        category="Network Interfaces",
                        name=f"{iface} Download Rate",
                        short_label="NET↓",
                        value=rx_rate / (1024 ** 2),
                        unit="MB/s",
                        val_text=format_rate(rx_rate),
                        sub_text=iface,
                        min_val=0.0,
                        max_val=50.0,
                        icon="arrow-down",
                        default_color="cyan",
                    )
                    self.register(
                        sensor_id=f"network/{iface}/upload",
                        category="Network Interfaces",
                        name=f"{iface} Upload Rate",
                        short_label="NET↑",
                        value=tx_rate / (1024 ** 2),
                        unit="MB/s",
                        val_text=format_rate(tx_rate),
                        sub_text=iface,
                        min_val=0.0,
                        max_val=50.0,
                        icon="arrow-up",
                        default_color="purple",
                    )
        except OSError:
            pass

        self.new_state["net_io"] = cur_net
        self.register(
            sensor_id="network/all/download",
            category="Network Interfaces",
            name="Total Network Download",
            short_label="DOWN",
            value=total_rx / (1024 ** 2),
            unit="MB/s",
            val_text=format_rate(total_rx),
            sub_text=f"↑{format_rate(total_tx)}",
            min_val=0.0,
            max_val=50.0,
            icon="arrow-down",
            default_color="cyan",
        )
        self.register(
            sensor_id="network/all/upload",
            category="Network Interfaces",
            name="Total Network Upload",
            short_label="UP",
            value=total_tx / (1024 ** 2),
            unit="MB/s",
            val_text=format_rate(total_tx),
            sub_text=f"↓{format_rate(total_rx)}",
            min_val=0.0,
            max_val=50.0,
            icon="arrow-up",
            default_color="purple",
        )

    def _collect_power(self) -> None:
        for psy in sorted(glob.glob("/sys/class/power_supply/*")):
            pname = os.path.basename(psy)
            ptype = read_text(os.path.join(psy, "type"))
            if ptype.lower() == "battery":
                cap = read_float(os.path.join(psy, "capacity"))
                status = read_text(os.path.join(psy, "status"), "Unknown")
                if cap is not None:
                    self.register(
                        sensor_id=f"power/{pname}/chargePercent",
                        category="Power & Voltage",
                        name=f"Battery {pname} Charge",
                        short_label="BAT",
                        value=cap,
                        unit="%",
                        val_text=f"{cap:.0f}%",
                        sub_text=status,
                        percent=cap,
                        icon="battery",
                        default_color="green",
                    )

    def _collect_pressure(self) -> None:
        for res in ("cpu", "memory", "io"):
            pfile = f"/proc/pressure/{res}"
            txt = read_text(pfile)
            if not txt:
                continue
            for line in txt.splitlines():
                if line.startswith("some "):
                    m = re.search(r"avg10=(\d+(?:\.\d+)?)", line)
                    if m:
                        val = float(m.group(1))
                        self.register(
                            sensor_id=f"pressure/{res}/some10",
                            category="Linux PSI Pressure",
                            name=f"{res.upper()} Pressure (10s avg)",
                            short_label=f"PSI-{res[0].upper()}",
                            value=val,
                            unit="%",
                            val_text=f"{val:.1f}%",
                            sub_text="10s stall",
                            percent=val,
                            icon="activity",
                            default_color="red" if val > 10 else "yellow",
                        )


# ---------------------------------------------------------------------------
# 3x Supersampled Pillow Renderer: Ring, Bar, and Histogram
# ---------------------------------------------------------------------------
def _draw_capsule_arc(
    draw: Any,
    center: Tuple[float, float],
    radius: float,
    thickness: float,
    start_angle: float,
    end_angle: float,
    fill: Tuple[int, int, int, int],
) -> None:
    cx, cy = center
    mid_r = radius - thickness / 2.0
    cap_r = thickness / 2.0
    bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
    draw.arc(bbox, start=start_angle, end=end_angle, fill=fill, width=int(thickness))
    for angle in (start_angle, end_angle):
        rad = math.radians(angle)
        cap_x = cx + mid_r * math.cos(rad)
        cap_y = cy + mid_r * math.sin(rad)
        draw.ellipse([cap_x - cap_r, cap_y - cap_r, cap_x + cap_r, cap_y + cap_r], fill=fill)


def render_sensor_graph(
    sensor: Dict[str, Any],
    graph_type: str,
    color_name: str,
    skin_name: str,
    target_px: int,
    compact: bool,
    out_path: str,
) -> bool:
    if not HAS_PIL:
        return False

    skin = SKINS.get(skin_name, SKINS["dark"])
    rgb = PALETTE_COLORS.get(color_name, PALETTE_COLORS.get(sensor.get("default_color", "blue"), (120, 180, 255)))
    percent = float(sensor.get("percent", 0.0))
    val_text = str(sensor.get("val_text", ""))
    label_text = str(sensor.get("short_label", ""))
    sub_text = str(sensor.get("sub_text", ""))
    history = sensor.get("history", [percent])

    scale = 3
    if graph_type == "ring":
        w_out = max(24, int(target_px))
        h_out = w_out
        w, h = w_out * scale, h_out * scale
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        cx, cy = w / 2.0, h / 2.0
        radius = (w / 2.0) - (3 * scale if compact else 7 * scale)
        thickness = max(3 * scale, radius * (0.24 if compact else 0.165))

        halo_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        halo_draw = ImageDraw.Draw(halo_layer)
        draw = ImageDraw.Draw(img)

        # Fill inner gauge face with subtle dark/light disc so text has high contrast on any wallpaper
        inner_r = radius - thickness * 0.5
        draw.ellipse(
            [cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r],
            fill=skin["bg_card"],
        )

        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            outline=skin["track"],
            width=int(thickness),
        )

        if percent > 0.2:
            start_deg = -90.0
            span = max(360.0 * min(percent, 100.0) / 100.0, 14.0)
            end_deg = start_deg + span

            if not compact:
                _draw_capsule_arc(
                    halo_draw,
                    (cx, cy),
                    radius + 4 * scale,
                    thickness + 8 * scale,
                    start_deg,
                    end_deg,
                    rgb + (68,),
                )
                img = Image.alpha_composite(img, halo_layer)
                draw = ImageDraw.Draw(img)

            _draw_capsule_arc(
                draw,
                (cx, cy),
                radius,
                thickness,
                start_deg,
                end_deg,
                rgb + (255,),
            )

        if compact and w_out < 52:
            # Compact ring for standard top bars: crisp single centered value scaled to character count
            short_val = val_text.replace("/s", "")
            n_chars = len(short_val)
            ratio = 0.26 if n_chars <= 3 else (0.205 if n_chars == 4 else 0.175)
            f_val = get_font(max(8 * scale, int(w_out * ratio * scale)), bold=True)
            draw.text((cx, cy), short_val, font=f_val, fill=skin["text"], anchor="mm")
        else:
            # Full 3-line NVTOP/KDE tachometer inside the ring
            n_chars = len(val_text)
            val_ratio = 0.175 if n_chars <= 4 else (0.15 if n_chars <= 6 else 0.13)
            f_val = get_font(max(10 * scale, int(w_out * val_ratio * scale)), bold=True)
            f_lbl = get_font(max(9 * scale, int(w_out * 0.13 * scale)), bold=True)
            f_sub = get_font(max(8 * scale, int(w_out * 0.11 * scale)), bold=False)
            draw.text((cx, cy - w * 0.13), val_text, font=f_val, fill=skin["text"], anchor="mm")
            draw.text((cx, cy + w * 0.04), label_text, font=f_lbl, fill=rgb + (255,), anchor="mm")
            if sub_text:
                draw.text((cx, cy + w * 0.19), sub_text, font=f_sub, fill=skin["subtext"], anchor="mm")

    elif graph_type == "bar":
        if compact:
            w_out = max(44, int(target_px * 1.5))
            h_out = max(14, int(target_px * 0.42))
        else:
            w_out = max(96, int(target_px * 1.15))
            h_out = max(28, int(target_px * 0.34))
        w, h = w_out * scale, h_out * scale
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        pad = 2 * scale
        r = (h - 2 * pad) / 2.0
        draw.rounded_rectangle([pad, pad, w - pad, h - pad], radius=r, fill=skin["track"])
        fill_w = max(2 * r, (w - 2 * pad) * (min(100.0, max(0.0, percent)) / 100.0))
        if percent > 0.5:
            draw.rounded_rectangle([pad, pad, pad + fill_w, h - pad], radius=r, fill=rgb + (220,))
        if not compact:
            bar_str = f"{label_text} {val_text}"
            ratio = 0.34 if len(bar_str) <= 9 else 0.28
            f_txt = get_font(max(9 * scale, int(h_out * ratio * scale)), bold=True)
            draw.text(
                (w / 2.0, h / 2.0),
                bar_str,
                font=f_txt,
                fill=skin["text"],
                anchor="mm",
                stroke_width=max(1, scale // 2),
                stroke_fill=skin["bg_card"],
            )

    else:  # histogram (rolling sparkline/area graph)
        if compact:
            w_out = max(48, int(target_px * 1.55))
            h_out = max(20, int(target_px * 0.72))
        else:
            w_out = max(100, int(target_px * 1.15))
            h_out = max(56, int(target_px * 0.68))
        w, h = w_out * scale, h_out * scale
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        pad = 2 * scale
        r = 6 * scale
        draw.rounded_rectangle([pad, pad, w - pad, h - pad], radius=r, fill=skin["bg_card"], outline=skin["track"], width=scale)

        pts = list(history[-30:]) if history else [percent]
        while len(pts) < 30:
            pts.insert(0, pts[0] if pts else 0.0)

        inner_w = w - 4 * pad
        inner_h = h - 4 * pad
        step_x = inner_w / max(1, len(pts) - 1)
        poly = [(2 * pad, h - 2 * pad)]
        line_pts = []
        for i, p in enumerate(pts):
            px = 2 * pad + i * step_x
            py = (h - 2 * pad) - (max(0.0, min(100.0, float(p))) / 100.0) * inner_h
            poly.append((px, py))
            line_pts.append((px, py))
        poly.append((2 * pad + inner_w, h - 2 * pad))

        area_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        area_draw = ImageDraw.Draw(area_layer)
        area_draw.polygon(poly, fill=rgb + (90,))
        img = Image.alpha_composite(img, area_layer)
        draw = ImageDraw.Draw(img)
        if len(line_pts) >= 2:
            draw.line(line_pts, fill=rgb + (255,), width=2 * scale, joint="curve")

        if not compact:
            f_lbl = get_font(int(h_out * 0.22 * scale), bold=True)
            draw.text((4 * pad, 3 * pad), f"{label_text} {val_text}", font=f_lbl, fill=skin["text"], anchor="lt")

    final_img = img.resize((w_out, h_out), Image.Resampling.LANCZOS)
    try:
        tmp_path = out_path + ".tmp"
        final_img.save(tmp_path, "PNG")
        os.replace(tmp_path, out_path)
        return True
    except OSError:
        return False


def build_default_active_list(sensors: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Hardware-agnostic starter sensor list based on whatever hardware exists on the host."""
    defaults: List[Dict[str, Any]] = []

    def add_if_exists(sid: str, graph_type: str = "ring", color: str = "blue", on_bar: bool = True) -> None:
        if sid in sensors:
            s = sensors[sid]
            defaults.append(
                {
                    "id": sid,
                    "label": s["short_label"],
                    "graph_type": graph_type,
                    "color": color,
                    "show_icon": False,
                    "show_label": True,
                    "show_value": True,
                    "show_graph": True,
                    "on_bar": on_bar,
                    "on_desktop": True,
                }
            )

    add_if_exists("cpu/all/usage", "ring", "blue", True)
    add_if_exists("memory/physical/usedPercent", "ring", "purple", True)

    # Add every discovered GPU's Load and VRAM automatically!
    gpu_ids = sorted([k for k in sensors.keys() if re.match(r"^gpu/gpu\d+/usage$", k)])
    for idx, gid in enumerate(gpu_ids):
        col = "blue" if idx == 0 else "cyan"
        add_if_exists(gid, "ring", col, True)
        vram_id = gid.replace("/usage", "/usedVramPct")
        add_if_exists(vram_id, "ring", col, True)

    # Add first 2 hardware fans if available (e.g. CPU/GPU fans) on desktop by default
    fan_ids = sorted([k for k in sensors.keys() if "/fan" in k])[:2]
    for fid in fan_ids:
        add_if_exists(fid, "bar", "green", False)

    return defaults


def main() -> None:
    os.makedirs(RUNTIME_BASE, exist_ok=True)

    prev_state: Dict[str, Any] = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                prev_state = json.load(f)
        except Exception:
            prev_state = {}

    cfg: Dict[str, Any] = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

    theme = str(sys.argv[1] if len(sys.argv) > 1 else cfg.get("theme", "dark"))
    bar_size = int(sys.argv[2] if len(sys.argv) > 2 else cfg.get("bar_size", 34))
    desktop_size = int(sys.argv[3] if len(sys.argv) > 3 else cfg.get("desktop_size", 96))
    respect_gpu_sleep = (
        sys.argv[4].lower() == "true"
        if len(sys.argv) > 4
        else bool(cfg.get("respect_gpu_sleep", False))
    )
    sensors_json_path = sys.argv[5] if len(sys.argv) > 5 else ""

    engine = KdeStyleSensorEngine(prev_state, respect_gpu_sleep=respect_gpu_sleep)
    sensors = engine.collect_all()

    try:
        with open(STATE_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(engine.new_state, f)
        os.replace(STATE_FILE + ".tmp", STATE_FILE)
    except OSError:
        pass

    active_cfg = None
    if sensors_json_path and os.path.exists(sensors_json_path):
        try:
            with open(sensors_json_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, list) and len(loaded) > 0:
                    active_cfg = loaded
        except Exception:
            active_cfg = None

    if active_cfg is None:
        active_cfg = cfg.get("active_sensors")

    if not isinstance(active_cfg, list) or len(active_cfg) == 0:
        active_cfg = build_default_active_list(sensors)
        if sensors_json_path:
            try:
                os.makedirs(os.path.dirname(sensors_json_path), exist_ok=True)
                with open(sensors_json_path + ".tmp", "w", encoding="utf-8") as f:
                    json.dump(active_cfg, f, indent=2)
                os.replace(sensors_json_path + ".tmp", sensors_json_path)
            except OSError:
                pass

    stamp = int(time.time() * 1000)
    rendered_active: List[Dict[str, Any]] = []

    for idx, item in enumerate(active_cfg):
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id", ""))
        sdata = sensors.get(sid)
        if not sdata:
            continue

        gtype = str(item.get("graph_type", "ring"))
        if gtype not in ("ring", "bar", "histogram"):
            gtype = "ring"
        color = str(item.get("color", sdata.get("default_color", "blue")))
        custom_label = str(item.get("label") or sdata["short_label"])

        sensor_copy = dict(sdata)
        sensor_copy["short_label"] = custom_label

        bar_img_path = ""
        desk_img_path = ""
        safe_id = re.sub(r"[^a-zA-Z0-9_]", "_", sid)

        if item.get("show_graph", True) is not False and HAS_PIL:
            if item.get("on_bar", True) is not False:
                bpath = os.path.join(RUNTIME_BASE, f"bar_{idx}_{safe_id}.png")
                if render_sensor_graph(sensor_copy, gtype, color, theme, bar_size, compact=(bar_size < 52), out_path=bpath):
                    bar_img_path = bpath
            if item.get("on_desktop", True) is not False or True:
                dpath = os.path.join(RUNTIME_BASE, f"desk_{idx}_{safe_id}.png")
                if render_sensor_graph(sensor_copy, gtype, color, theme, desktop_size, compact=False, out_path=dpath):
                    desk_img_path = dpath

        rendered_active.append(
            {
                "id": sid,
                "name": sdata["name"],
                "category": sdata["category"],
                "label": custom_label,
                "val_text": sdata["val_text"],
                "sub_text": sdata["sub_text"],
                "percent": sdata["percent"],
                "icon": sdata["icon"],
                "graph_type": gtype,
                "color": color,
                "show_icon": bool(item.get("show_icon", False)),
                "show_label": bool(item.get("show_label", True)),
                "show_value": bool(item.get("show_value", True)),
                "show_graph": bool(item.get("show_graph", True)),
                "on_bar": bool(item.get("on_bar", True)),
                "on_desktop": bool(item.get("on_desktop", True)),
                "bar_img": bar_img_path,
                "desk_img": desk_img_path,
                "bar_w": bar_size if gtype == "ring" else int(bar_size * 1.5),
                "bar_h": bar_size if gtype == "ring" else (int(bar_size * 0.42) if gtype == "bar" else int(bar_size * 0.72)),
                "desk_w": desktop_size if gtype == "ring" else int(desktop_size * 1.15),
                "desk_h": desktop_size if gtype == "ring" else (int(desktop_size * 0.34) if gtype == "bar" else int(desktop_size * 0.68)),
            }
        )

    # Compact catalog for the interactive sensor picker panel
    catalog_list = [
        {
            "id": s["id"],
            "category": s["category"],
            "name": s["name"],
            "short_label": s["short_label"],
            "val_text": s["val_text"],
            "sub_text": s["sub_text"],
            "icon": s["icon"],
            "default_color": s["default_color"],
        }
        for s in sensors.values()
    ]
    catalog_list.sort(key=lambda x: (x["category"], x["name"]))

    output = {
        "stamp": str(stamp),
        "active": rendered_active,
        "catalog": catalog_list,
    }

    try:
        with open(SNAPSHOT_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(output, f)
        os.replace(SNAPSHOT_FILE + ".tmp", SNAPSHOT_FILE)
    except OSError:
        pass

    print(json.dumps({"status": "ok", "snapshot": SNAPSHOT_FILE, "stamp": str(stamp)}))


if __name__ == "__main__":
    main()
