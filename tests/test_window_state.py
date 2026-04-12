"""Tests for window_state.py."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from patterns.window_state import (
    MonitorInfo,
    WindowGeometry,
    WindowStateManager,
    clamp_to_monitor,
    load_window_state,
    save_window_state,
)


class TestWindowGeometry:
    def test_defaults(self):
        g = WindowGeometry()
        assert g.width == 1280
        assert g.height == 800
        assert g.maximized is False

    def test_is_valid_positive(self):
        g = WindowGeometry(width=800, height=600)
        assert g.is_valid() is True

    def test_is_valid_zero_width(self):
        g = WindowGeometry(width=0, height=600)
        assert g.is_valid() is False

    def test_to_dict_from_dict_round_trip(self):
        g = WindowGeometry(x=50, y=75, width=1024, height=768, maximized=True)
        restored = WindowGeometry.from_dict(g.to_dict())
        assert restored.x == 50
        assert restored.y == 75
        assert restored.maximized is True

    def test_from_dict_ignores_extra_keys(self):
        data = {"x": 0, "y": 0, "width": 100, "height": 100, "unknown_key": "ignored"}
        g = WindowGeometry.from_dict(data)
        assert g.width == 100


class TestMonitorInfo:
    def test_right_and_bottom(self):
        m = MonitorInfo(x=0, y=0, width=1920, height=1080)
        assert m.right == 1920
        assert m.bottom == 1080

    def test_right_with_offset(self):
        m = MonitorInfo(x=1920, y=0, width=1920, height=1080)
        assert m.right == 3840


class TestClampToMonitor:
    def _monitor(self) -> MonitorInfo:
        return MonitorInfo(x=0, y=0, width=1920, height=1080)

    def test_fits_unchanged(self):
        g = WindowGeometry(x=100, y=100, width=800, height=600)
        clamped = clamp_to_monitor(g, self._monitor())
        assert clamped.x == 100
        assert clamped.y == 100
        assert clamped.width == 800
        assert clamped.height == 600

    def test_clamp_x_negative(self):
        g = WindowGeometry(x=-100, y=100, width=800, height=600)
        clamped = clamp_to_monitor(g, self._monitor())
        assert clamped.x >= 0

    def test_clamp_position_off_right(self):
        g = WindowGeometry(x=2000, y=100, width=800, height=600)
        clamped = clamp_to_monitor(g, self._monitor())
        assert clamped.x + clamped.width <= 1920

    def test_clamp_size_wider_than_monitor(self):
        g = WindowGeometry(x=0, y=0, width=3000, height=600)
        clamped = clamp_to_monitor(g, self._monitor())
        assert clamped.width <= 1920

    def test_preserves_maximized_flag(self):
        g = WindowGeometry(x=0, y=0, width=800, height=600, maximized=True)
        clamped = clamp_to_monitor(g, self._monitor())
        assert clamped.maximized is True


class TestSaveLoadWindowState:
    def test_save_load_round_trip(self, tmp_path: Path):
        g = WindowGeometry(x=10, y=20, width=1024, height=768)
        save_window_state(tmp_path, "main", g)
        loaded = load_window_state(tmp_path, "main")
        assert loaded.x == 10
        assert loaded.width == 1024

    def test_load_missing_returns_default(self, tmp_path: Path):
        default = WindowGeometry(width=640, height=480)
        loaded = load_window_state(tmp_path, "nonexistent", default)
        assert loaded.width == 640

    def test_load_missing_no_default_returns_default_geometry(self, tmp_path: Path):
        loaded = load_window_state(tmp_path, "nonexistent")
        assert isinstance(loaded, WindowGeometry)

    def test_load_corrupt_json_returns_default(self, tmp_path: Path):
        (tmp_path / "bad.json").write_text("not valid json{}")
        default = WindowGeometry(width=500)
        loaded = load_window_state(tmp_path, "bad", default)
        assert loaded.width == 500


class TestWindowStateManager:
    def test_save_and_load(self, tmp_path: Path):
        mgr = WindowStateManager(state_dir=tmp_path)
        g = WindowGeometry(x=5, y=10, width=1280, height=800)
        mgr.save("main", g)
        loaded = mgr.load("main")
        assert loaded.x == 5

    def test_load_missing_returns_default(self, tmp_path: Path):
        mgr = WindowStateManager(state_dir=tmp_path)
        loaded = mgr.load("absent")
        assert isinstance(loaded, WindowGeometry)

    def test_delete_existing(self, tmp_path: Path):
        mgr = WindowStateManager(state_dir=tmp_path)
        mgr.save("main", WindowGeometry())
        assert mgr.delete("main") is True
        assert not (tmp_path / "main.json").exists()

    def test_delete_nonexistent_returns_false(self, tmp_path: Path):
        mgr = WindowStateManager(state_dir=tmp_path)
        assert mgr.delete("ghost") is False

    def test_list_windows(self, tmp_path: Path):
        mgr = WindowStateManager(state_dir=tmp_path)
        mgr.save("alpha", WindowGeometry())
        mgr.save("beta", WindowGeometry())
        windows = mgr.list_windows()
        assert "alpha" in windows
        assert "beta" in windows
        assert windows == sorted(windows)

    def test_list_windows_empty_dir(self, tmp_path: Path):
        mgr = WindowStateManager(state_dir=tmp_path)
        assert mgr.list_windows() == []

    def test_list_windows_nonexistent_dir(self, tmp_path: Path):
        mgr = WindowStateManager(state_dir=tmp_path / "does_not_exist")
        assert mgr.list_windows() == []

    def test_state_dir_property(self, tmp_path: Path):
        mgr = WindowStateManager(state_dir=tmp_path)
        assert mgr.state_dir == tmp_path
