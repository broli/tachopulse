# TachoPulse (`broli/tachopulse`)

**Dynamic KDE-Style Hardware Telemetry Widget & Desktop Monitor for Noctalia v5 (`plugin_api = 32`)**

**TachoPulse** combines **KDE Plasma System Monitor's** (`ksystemstats` / `libksysguard`) hardware-agnostic sensor discovery engine with **3x supersampled glowing visualizations** (`Ring` tachometer, `Bar` level meter, and `Histogram` rolling sparkline) for both the **Noctalia Status Bar** (`[[widget]]`) and **Desktop Wallpaper** (`[[desktop_widget]]`).

---

## ✨ Key Features

- **100% Hardware-Agnostic Discovery (Zero Hardcoding)**:
  Dynamically enumerates every sensor available on any Linux machine at runtime:
  - **Multi-GPU Support (`nvtop -s` + `/sys/class/drm/card*`)**: Discovers all GPUs individually (e.g., NVIDIA discrete GPU + Intel/AMD integrated GPU) with per-GPU Load %, VRAM Used/Free, Temperature, Power Draw, and GT Clocks.
  - **CPU (`/proc/stat` + `cpufreq` + `coretemp`/`k10temp`/`zenpower`)**: Total load, package temperature, average/max core clocks, 1m/5m load averages, and individual per-core threads (`cpu0..cpuN`).
  - **Motherboard & Chipset Sensors (`/sys/class/hwmon/hwmon*`)**: Every temperature probe (`temp*_input`), cooling fan RPM (`fan*_input`), power draw (`power*_input`), and thermal zone (CPU/GPU fans, NVMe drives, DDR5 SPD hubs, ACPI thermal zones).
  - **Memory, Storage, Network & PSI Pressure**: Physical RAM, Swap, root filesystem usage, per-disk read/write throughput (`/proc/diskstats`), per-interface RX/TX bandwidth (`/proc/net/dev`), battery charge (`/sys/class/power_supply/*`), and Linux PSI stall pressure (`/proc/pressure/{cpu,memory,io}`).
- **Two Synchronized Widget Modes**:
  1. **Status Bar Widget (`bar.luau`)**: Adaptive compact or full-ring telemetry directly in your top, bottom, or vertical side bar.
  2. **Desktop Widget (`desktop.luau`)**: Translucent multi-column card grid pinned to your desktop wallpaper.
- **Interactive Sensor Studio (`panel.luau`)**:
  Click the bar widget to open **TachoPulse Sensor Studio**, where you can:
  - Browse & search all discovered hardware sensors on your system with live readings.
  - Switch any sensor between **`Ring`** (circular tachometer), **`Bar`** (capsule meter), and **`Hist`** (rolling 30-sample area sparkline).
  - Toggle **`Icon`**, **`Label`**, **`Value`**, **`Graph`**, **`Top Bar`**, and **`Desktop`** visibility per sensor.
  - Cycle through 7 glowing color palettes (`blue`, `cyan`, `green`, `orange`, `purple`, `red`, `yellow`) and reorder sensors on the fly.

---

## 📏 Status Bar Thickness Recommendations

TachoPulse never assumes a fixed bar height and automatically adapts its rendering based on the **Bar Graph Size (`bar_gauge_size`)** setting:

| Bar Thickness | Recommended `bar_gauge_size` | Rendering Mode |
| :--- | :--- | :--- |
| **Standard Slim Bar (`32px–44px`)** | `30px–38px` *(Default: `34px`)* | **Compact High-Contrast Mode (`< 52px`)**: Draws a bold single value inside the glowing ring and places the sensor label & sub-metric cleanly inline beside the ring so text is never cramped. |
| **Tall Telemetry Bar (`56px–72px`)** | `52px–64px` | **Full 3-Line Tachometer Mode (`>= 52px`)**: Draws all 3 lines (`Value`, `Label`, `Sub-metric` like `°C` or `+Free VRAM`) inside the glowing ring itself. |
| **Desktop Wallpaper Widget** | `80px–140px` *(Default: `96px`)* | **Full 3-Line Tachometer Mode**: Full-sized glowing rings, capsule bars, and time-series histograms on your desktop. |

---

## 📦 Dependencies

- **Required**:
  - `noctalia` (`>= 5.3.0`, `plugin_api = 32`)
  - `python3` + `python-pillow` (`PIL` for 3x supersampled LANCZOS graph rendering)
- **Recommended (for Multi-GPU NVIDIA / AMD / Intel Telemetry)**:
  - `nvtop` (used via `nvtop -s` for JSON multi-GPU load, VRAM, temperature, and power queries; falls back cleanly to `/sys/class/drm/card*` if `nvtop` is not installed)

On Arch Linux / CachyOS:
```bash
sudo pacman -S python python-pillow nvtop
```

---

## 🚀 Installation

### Option 1: Install from Local Widgets Directory (Live Development)
If you keep your Noctalia widgets inside `~/Projects/Widgets` (with this repo at `~/Projects/Widgets/tachopulse`):
```bash
noctalia msg plugins source add local-widgets path /home/carlos/Projects/Widgets
noctalia msg plugins enable broli/tachopulse
```

### Option 2: Install Directly from GitHub
```bash
noctalia msg plugins source add tachopulse git https://github.com/broli/tachopulse
noctalia msg plugins enable broli/tachopulse
```

Then add **TachoPulse** (`broli/tachopulse:bar`) to any monitor bar in **Noctalia Settings → Bar**, or enable the **TachoPulse Desktop Widget** (`broli/tachopulse:desktop`) in **Noctalia Settings → Desktop Widgets**.

---

## 🙏 Credits & License

Licensed under **GPL-2.0-or-later** (see [LICENSE](./LICENSE)):
- **Hardware Sensor Identification & Classification Engine**: Adapted from **KDE Plasma's `ksystemstats` and `libksysguard`** plugins (`cpu`, `gpu`, `lmsensors`, `memory`, `disk`, `network`, `power`, `pressure`) by the KDE Project Contributors.
- **Supersampled Capsule-Arc Ring Gauge Inspiration**: Inspired by **`tordex/nvtop`** for Noctalia.
