"""Window state persistence for Tauri v2 desktop apps.

Tauri v2 provides window position/size APIs. This module implements:
- Serializable WindowGeometry with monitor clamping
- WindowStateManager: save/load/apply window geometry per window label
- Monitor boundary enforcement so windows never appear off-screen

Usage::

    manager = WindowStateManager(state_dir=Path("~/.config/myapp"))
    geometry = manager.load("main")
    # ... apply geometry via Tauri window API ...
    manager.save("main", geometry)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class WindowGeometry:
    """Position and size of a desktop window.

    Attributes:
        x: Left edge in logical pixels.
        y: Top edge in logical pixels.
        width: Window width in logical pixels.
        height: Window height in logical pixels.
        maximized: True if the window is maximized.
        fullscreen: True if the window is fullscreen.
    """

    x: int = 100
    y: int = 100
    width: int = 1280
    height: int = 800
    maximized: bool = False
    fullscreen: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to plain dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WindowGeometry:
        """Deserialize from dict, ignoring unknown keys."""
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def is_valid(self) -> bool:
        """Return True if width and height are positive."""
        return self.width > 0 and self.height > 0


@dataclass
class MonitorInfo:
    """Physical or logical monitor description.

    Attributes:
        x: Left edge of the monitor's work area.
        y: Top edge of the monitor's work area.
        width: Width of the monitor's work area.
        height: Height of the monitor's work area.
        scale_factor: DPI scale factor (1.0 = no scaling).
    """

    x: int
    y: int
    width: int
    height: int
    scale_factor: float = 1.0

    @property
    def right(self) -> int:
        """Right edge of the work area."""
        return self.x + self.width

    @property
    def bottom(self) -> int:
        """Bottom edge of the work area."""
        return self.y + self.height


def clamp_to_monitor(geometry: WindowGeometry, monitor: MonitorInfo) -> WindowGeometry:
    """Clamp a window geometry to fit within a monitor's work area.

    Ensures the window's top-left corner is visible and the window does
    not extend outside the monitor boundaries.

    Args:
        geometry: Desired window geometry.
        monitor: Monitor to clamp against.

    Returns:
        New :class:`WindowGeometry` clamped to the monitor.
    """
    # Clamp size to monitor work area
    max_w = monitor.width
    max_h = monitor.height
    width = min(geometry.width, max_w)
    height = min(geometry.height, max_h)

    # Clamp position so the window fits within the work area
    x = max(monitor.x, min(geometry.x, monitor.right - width))
    y = max(monitor.y, min(geometry.y, monitor.bottom - height))

    return WindowGeometry(
        x=x,
        y=y,
        width=width,
        height=height,
        maximized=geometry.maximized,
        fullscreen=geometry.fullscreen,
    )


def save_window_state(
    state_dir: Path,
    label: str,
    geometry: WindowGeometry,
) -> None:
    """Persist window geometry to a JSON file.

    Args:
        state_dir: Directory to store state files.
        label: Window label (used as filename base).
        geometry: Geometry to persist.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / f"{label}.json"
    path.write_text(json.dumps(geometry.to_dict(), indent=2))


def load_window_state(
    state_dir: Path,
    label: str,
    default: WindowGeometry | None = None,
) -> WindowGeometry:
    """Load persisted window geometry from a JSON file.

    Args:
        state_dir: Directory containing state files.
        label: Window label.
        default: Fallback geometry if no state file exists.

    Returns:
        Loaded or default :class:`WindowGeometry`.
    """
    path = state_dir / f"{label}.json"
    if not path.exists():
        return default if default is not None else WindowGeometry()

    try:
        data = json.loads(path.read_text())
        return WindowGeometry.from_dict(data)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else WindowGeometry()


class WindowStateManager:
    """Manages per-window geometry persistence for a Tauri app.

    Args:
        state_dir: Directory used to persist JSON state files.
    """

    def __init__(self, state_dir: Path | None = None) -> None:
        self._dir = state_dir or Path.home() / ".config" / "tauri-app" / "windows"

    @property
    def state_dir(self) -> Path:
        """The directory used for state files."""
        return self._dir

    def save(self, label: str, geometry: WindowGeometry) -> None:
        """Persist geometry for a window label.

        Args:
            label: Window label.
            geometry: Current window geometry.
        """
        save_window_state(self._dir, label, geometry)

    def load(self, label: str, default: WindowGeometry | None = None) -> WindowGeometry:
        """Load geometry for a window label.

        Args:
            label: Window label.
            default: Fallback if no saved state exists.

        Returns:
            Loaded or default :class:`WindowGeometry`.
        """
        return load_window_state(self._dir, label, default)

    def delete(self, label: str) -> bool:
        """Delete the state file for a window label.

        Args:
            label: Window label to delete.

        Returns:
            True if the file existed and was deleted.
        """
        path = self._dir / f"{label}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def list_windows(self) -> list[str]:
        """Return labels of all windows with persisted state.

        Returns:
            Sorted list of window labels.
        """
        if not self._dir.exists():
            return []
        return sorted(p.stem for p in self._dir.glob("*.json"))
