# tauri-v2-desktop-patterns

Production patterns for Tauri v2 desktop apps: async IPC, Rust+Python sidecar lifecycle, Python bundling strategies, window state persistence.

## Patterns

### IPC Bridge (`patterns/ipc_bridge.py`)
- `IpcRegistry` — register async handlers by name via `@registry.command("name")` decorator
- `IpcCommand` — parse JSON lines from Tauri frontend stdin
- `IpcResponse` — typed success/error responses with correlation IDs
- `IpcErrorCode` — JSON-RPC-inspired error codes (METHOD_NOT_FOUND, INVALID_PARAMS, etc.)
- Full exception isolation: handler crashes return structured error, never crash the sidecar

### Sidecar Manager (`patterns/sidecar_manager.py`)
- `SidecarProcess` — start/stop/restart a Python subprocess with STOPPED→RUNNING state machine
- `StdioMessage` — typed message protocol (COMMAND/RESPONSE/EVENT/ERROR/HEARTBEAT)
- `SidecarConfig` — crash-restart policy, max restarts, startup/shutdown timeouts
- `send_message()` / `send_command()` — stdin/stdout typed communication

### Python Bundler (`patterns/python_bundler.py`)
- `BundleStrategy` — EMBEDDED_VENV / PYINSTALLER / NUITKA / SYSTEM_PYTHON / CONDA_PACK
- `PythonBundler.build_plan()` — generates shell commands for CI/CD scripts
- `PythonBundler.validate()` — configuration warnings before building
- `detect_runtime()` — detects current Python executable, version, venv status
- Size estimates and build time factors per strategy

### Window State (`patterns/window_state.py`)
- `WindowGeometry` — serializable position/size with `to_dict()` / `from_dict()`
- `clamp_to_monitor()` — prevents windows from appearing off-screen
- `WindowStateManager` — per-window JSON persistence in `~/.config`
- `save_window_state()` / `load_window_state()` — standalone helpers

## Quick Start

```python
import sys
import asyncio
from patterns import IpcRegistry, IpcCommand

registry = IpcRegistry()

@registry.command("greet")
async def greet(name: str) -> dict[str, str]:
    return {"greeting": f"Hello, {name}!"}

async def main() -> None:
    for line in sys.stdin:
        cmd = IpcCommand.from_json(line.strip())
        response = await registry.dispatch(cmd)
        print(response.to_json(), flush=True)

asyncio.run(main())
```

## Installation

```bash
pip install -e ".[dev]"
pytest -q
```

## Requirements

- Python 3.12+
- No runtime dependencies (stdlib only)
- pytest, pytest-asyncio for tests
