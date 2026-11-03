"""
Unit tests for TachoPulse telemetry sensor daemon.
"""

import os
import sys

# Add scripts directory to module path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))

from sensor_daemon import (
    DEFAULT_INSTANCES,
    _parse_num,
    build_snapshot,
    format_rate_human,
    resolve_color,
)


def test_resolve_color():
    assert resolve_color("cyan") == (86, 214, 214)
    assert resolve_color("blue") == (120, 180, 255)
    assert resolve_color("orange") == (255, 185, 110)
    assert resolve_color("#ffb96e") == (255, 185, 110)
    assert resolve_color("") == (86, 214, 214)
    assert resolve_color("unknown_color") == (86, 214, 214)


def test_parse_num():
    assert _parse_num("42.5%") == 42.5
    assert _parse_num("100") == 100.0
    assert _parse_num(55.2) == 55.2
    assert _parse_num(None) == 0.0
    assert _parse_num("invalid") == 0.0


def test_format_rate_human():
    assert format_rate_human(500) == "500 B/s"
    assert format_rate_human(1024 * 5) == "5.0 KB/s"
    assert format_rate_human(1024 * 15) == "15 KB/s"
    assert format_rate_human(1024 * 1024 * 12.5) == "12 MB/s"
    assert format_rate_human(1024 * 1024 * 1024 * 2.5) == "2.5 GB/s"


def test_default_instances_structure():
    assert "default" in DEFAULT_INSTANCES
    for inst_id, inst in DEFAULT_INSTANCES.items():
        assert inst["id"] == inst_id
        assert "sensor_id" in inst
        assert "color" in inst
        assert "show_graph" in inst
        assert "show_icon" in inst
        assert "show_value" in inst
        assert "show_label" in inst


def test_build_snapshot():
    instances = {
        "test_inst": {
            "id": "test_inst",
            "name": "Test Metric",
            "sensor_id": "cpu/total_usage",
            "color": "cyan",
            "show_icon": True,
            "show_graph": False,
            "show_value": True,
            "decimals": 0,
            "show_label": True,
            "label": "CPU",
        }
    }
    all_sensors = {
        "cpu/total_usage": {
            "id": "cpu/total_usage",
            "category": "CPU",
            "name": "CPU Total Load",
            "short_label": "CPU",
            "value": 45.2,
            "val_text": "45%",
            "unit": "%",
            "min": 0,
            "max": 100,
            "pct": 0.452,
            "icon": "cpu",
            "default_color": "cyan",
        },
        "storage/root/free_gb": {
            "id": "storage/root/free_gb",
            "category": "Storage",
            "name": "Storage: Root (/) - Free Space",
            "short_label": "FREE",
            "value": 150.0,
            "val_text": "150.0 GB",
            "unit": "GB",
            "min": 0,
            "max": 500.0,
            "pct": 0.30,
            "icon": "database",
            "default_color": "green",
        },
    }

    snap = build_snapshot(instances, all_sensors, bar_size=34, desktop_size=96)
    assert "instances" in snap
    assert "catalog" in snap
    assert "categories" in snap
    assert snap["categories"] == ["CPU", "Storage"]
    assert "test_inst" in snap["instances"]
    assert snap["instances"]["test_inst"]["val_text"] == "45%"
