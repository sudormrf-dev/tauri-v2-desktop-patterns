"""Tauri v2 desktop patterns: async IPC, sidecar, Python bundling."""

from .ipc_bridge import (
    IpcCommand,
    IpcError,
    IpcErrorCode,
    IpcHandler,
    IpcRegistry,
    IpcResponse,
)
from .python_bundler import (
    BundleConfig,
    BundleStrategy,
    PythonBundler,
    RuntimeInfo,
    detect_runtime,
)
from .sidecar_manager import (
    SidecarConfig,
    SidecarProcess,
    SidecarState,
    StdioMessage,
    StdioMessageType,
)
from .window_state import (
    MonitorInfo,
    WindowGeometry,
    WindowStateManager,
    clamp_to_monitor,
    load_window_state,
    save_window_state,
)

__all__ = [
    "BundleConfig",
    "BundleStrategy",
    "IpcCommand",
    "IpcError",
    "IpcErrorCode",
    "IpcHandler",
    "IpcRegistry",
    "IpcResponse",
    "MonitorInfo",
    "PythonBundler",
    "RuntimeInfo",
    "SidecarConfig",
    "SidecarProcess",
    "SidecarState",
    "StdioMessage",
    "StdioMessageType",
    "WindowGeometry",
    "WindowStateManager",
    "clamp_to_monitor",
    "detect_runtime",
    "load_window_state",
    "save_window_state",
]
