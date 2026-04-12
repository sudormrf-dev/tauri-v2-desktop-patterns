"""Tauri v2 Python sidecar process management.

A Tauri sidecar is a subprocess bundled with the app. The frontend
communicates with it via stdin/stdout. This module provides:

- SidecarProcess: manages lifecycle of a Python sidecar subprocess
- StdioMessage: typed message protocol over stdin/stdout
- State machine: STOPPED → STARTING → RUNNING → STOPPING → STOPPED

Usage (from Python tests / integration)::

    config = SidecarConfig(executable="python", args=["-m", "my_sidecar"])
    process = SidecarProcess(config)
    await process.start()
    response = await process.send_command("ping", {})
    await process.stop()
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

logger = logging.getLogger(__name__)


class SidecarState(Enum):
    """Lifecycle state of a sidecar process."""

    STOPPED = auto()
    STARTING = auto()
    RUNNING = auto()
    STOPPING = auto()
    CRASHED = auto()


class StdioMessageType(str, Enum):
    """Type tag for sidecar stdio messages."""

    COMMAND = "command"
    RESPONSE = "response"
    EVENT = "event"
    ERROR = "error"
    HEARTBEAT = "heartbeat"


@dataclass
class StdioMessage:
    """A typed message exchanged over sidecar stdin/stdout.

    Attributes:
        type: Message type tag.
        id: Correlation ID (optional for events).
        payload: Message body.
    """

    type: StdioMessageType
    payload: dict[str, Any]
    id: str = ""

    def to_json(self) -> str:
        """Serialize to newline-terminated JSON for stdin/stdout transport."""
        return json.dumps({"type": self.type.value, "id": self.id, "payload": self.payload})

    @classmethod
    def from_json(cls, raw: str) -> StdioMessage:
        """Parse a JSON line from the sidecar.

        Args:
            raw: JSON string (without trailing newline).

        Raises:
            ValueError: If JSON is malformed or missing required fields.
        """
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError as exc:
            err = f"Invalid sidecar JSON: {exc}"
            raise ValueError(err) from exc

        try:
            msg_type = StdioMessageType(data.get("type", ""))
        except ValueError as exc:
            err = f"Unknown message type: {data.get('type')!r}"
            raise ValueError(err) from exc

        return cls(
            type=msg_type,
            id=str(data.get("id", "")),
            payload=data.get("payload", {}),
        )


@dataclass
class SidecarConfig:
    """Configuration for a Python sidecar process.

    Attributes:
        executable: Path or name of the Python interpreter / sidecar binary.
        args: Command-line arguments.
        env: Additional environment variables.
        startup_timeout_s: Seconds to wait for the process to become ready.
        shutdown_timeout_s: Seconds to wait for graceful exit.
        restart_on_crash: Automatically restart if the process crashes.
        max_restarts: Maximum crash-restart cycles (0 = unlimited).
    """

    executable: str = "python"
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    startup_timeout_s: float = 5.0
    shutdown_timeout_s: float = 3.0
    restart_on_crash: bool = True
    max_restarts: int = 5


class SidecarProcess:
    """Manages a Python sidecar subprocess with typed stdio messaging.

    Args:
        config: Sidecar configuration.
    """

    def __init__(self, config: SidecarConfig | None = None) -> None:
        self._cfg = config or SidecarConfig()
        self._state = SidecarState.STOPPED
        self._process: asyncio.subprocess.Process | None = None
        self._restart_count = 0
        self._pending: dict[str, asyncio.Future[StdioMessage]] = {}

    @property
    def state(self) -> SidecarState:
        """Current lifecycle state."""
        return self._state

    @property
    def pid(self) -> int | None:
        """Process ID, or None if not running."""
        if self._process is not None:
            return self._process.pid
        return None

    @property
    def restart_count(self) -> int:
        """Number of crash-restarts performed."""
        return self._restart_count

    def is_running(self) -> bool:
        """True if the sidecar is in RUNNING state."""
        return self._state == SidecarState.RUNNING

    async def start(self) -> bool:
        """Start the sidecar subprocess.

        Returns:
            True if started successfully.

        Raises:
            RuntimeError: If already running.
        """
        if self._state not in {SidecarState.STOPPED, SidecarState.CRASHED}:
            err = f"Cannot start sidecar in state {self._state.name}"
            raise RuntimeError(err)

        self._state = SidecarState.STARTING
        try:
            cmd = [self._cfg.executable, *self._cfg.args]
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._state = SidecarState.RUNNING
            logger.info("Sidecar started (pid=%d)", self._process.pid)
            return True
        except OSError:
            self._state = SidecarState.CRASHED
            logger.exception("Failed to start sidecar")
            return False

    async def stop(self) -> None:
        """Gracefully stop the sidecar subprocess."""
        if self._state not in {SidecarState.RUNNING, SidecarState.STARTING}:
            return

        self._state = SidecarState.STOPPING
        if self._process is not None:
            try:
                self._process.terminate()
                await asyncio.wait_for(
                    self._process.wait(),
                    timeout=self._cfg.shutdown_timeout_s,
                )
            except TimeoutError:
                logger.warning("Sidecar did not exit cleanly — killing")
                self._process.kill()
            except ProcessLookupError:
                pass  # already gone
            finally:
                self._process = None

        self._state = SidecarState.STOPPED

    async def send_message(self, message: StdioMessage) -> None:
        """Write a JSON message to the sidecar's stdin.

        Args:
            message: Message to send.

        Raises:
            RuntimeError: If sidecar is not running.
        """
        if self._process is None or self._process.stdin is None:
            err = "Sidecar is not running"
            raise RuntimeError(err)
        line = (message.to_json() + "\n").encode()
        self._process.stdin.write(line)
        await self._process.stdin.drain()

    async def send_command(
        self,
        command: str,
        payload: dict[str, Any],
        cmd_id: str = "",
    ) -> dict[str, Any]:
        """Send a COMMAND message and read the next RESPONSE from stdout.

        This is a simplified request/response helper for testing.
        Production code should read stdout in a background loop.

        Args:
            command: Command name.
            payload: Command arguments.
            cmd_id: Correlation ID (auto-generated if empty).

        Returns:
            Response payload dict.
        """
        if not cmd_id:
            import uuid

            cmd_id = str(uuid.uuid4())[:8]

        msg = StdioMessage(
            type=StdioMessageType.COMMAND,
            id=cmd_id,
            payload={"command": command, **payload},
        )
        await self.send_message(msg)

        if self._process is None or self._process.stdout is None:
            err = "Sidecar stdout not available"
            raise RuntimeError(err)

        line = await asyncio.wait_for(
            self._process.stdout.readline(),
            timeout=self._cfg.startup_timeout_s,
        )
        response = StdioMessage.from_json(line.decode())
        return response.payload

    def should_restart(self) -> bool:
        """True if crash-restart is configured and limit not reached."""
        if not self._cfg.restart_on_crash:
            return False
        if self._cfg.max_restarts == 0:
            return True
        return self._restart_count < self._cfg.max_restarts

    async def restart(self) -> bool:
        """Stop then start the sidecar.

        Returns:
            True if restarted successfully.
        """
        await self.stop()
        self._restart_count += 1
        return await self.start()
