"""Tauri v2 IPC bridge: type-safe command dispatch from Python sidecar.

Tauri v2 communicates with sidecars via stdin/stdout JSON messages.
Each IPC call from the frontend arrives as a JSON line; the sidecar
responds with a JSON line. This module provides a typed command registry
so Python sidecar handlers are registered by name and dispatched safely.

Pattern::

    registry = IpcRegistry()

    @registry.command("greet")
    async def greet(name: str) -> dict[str, str]:
        return {"greeting": f"Hello, {name}!"}

    async for line in sys.stdin:
        cmd = IpcCommand.from_json(line)
        response = await registry.dispatch(cmd)
        print(response.to_json())
"""

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine


class IpcErrorCode(IntEnum):
    """JSON-RPC-inspired error codes for IPC responses."""

    OK = 0
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    HANDLER_TIMEOUT = -32000
    HANDLER_EXCEPTION = -32001


@dataclass
class IpcError:
    """Structured IPC error.

    Attributes:
        code: Numeric error code.
        message: Human-readable error description.
        data: Optional additional context.
    """

    code: IpcErrorCode
    message: str
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON transport."""
        result: dict[str, Any] = {"code": int(self.code), "message": self.message}
        if self.data is not None:
            result["data"] = self.data
        return result


@dataclass
class IpcCommand:
    """A command received from the Tauri frontend.

    Attributes:
        id: Request correlation ID (echoed in response).
        command: Handler name to dispatch to.
        payload: Arguments dict for the handler.
    """

    id: str
    command: str
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: str) -> IpcCommand:
        """Parse a JSON line from Tauri frontend.

        Args:
            raw: JSON string.

        Raises:
            ValueError: If JSON is invalid or missing required fields.
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            err = f"Invalid JSON: {exc}"
            raise ValueError(err) from exc

        if not isinstance(data, dict):
            err = "IPC command must be a JSON object"
            raise ValueError(err)  # noqa: TRY004

        cmd_id = data.get("id", "")
        command = data.get("command", "")
        if not isinstance(cmd_id, str) or not isinstance(command, str):
            err = "IPC command requires string 'id' and 'command' fields"
            raise ValueError(err)  # noqa: TRY004

        payload = data.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}

        return cls(id=cmd_id, command=command, payload=payload)

    def to_json(self) -> str:
        """Serialize to JSON line for wire transport."""
        return json.dumps({"id": self.id, "command": self.command, "payload": self.payload})


@dataclass
class IpcResponse:
    """A response sent back to the Tauri frontend.

    Attributes:
        id: Correlation ID from the originating :class:`IpcCommand`.
        result: Successful result data (None on error).
        error: Error details (None on success).
    """

    id: str
    result: Any = None
    error: IpcError | None = None

    @property
    def ok(self) -> bool:
        """True if this response represents a success."""
        return self.error is None

    def to_json(self) -> str:
        """Serialize to JSON line for wire transport."""
        data: dict[str, Any] = {"id": self.id}
        if self.error is not None:
            data["error"] = self.error.to_dict()
        else:
            data["result"] = self.result
        return json.dumps(data)

    @classmethod
    def success(cls, cmd_id: str, result: Any) -> IpcResponse:
        """Create a success response."""
        return cls(id=cmd_id, result=result)

    @classmethod
    def failure(cls, cmd_id: str, code: IpcErrorCode, message: str) -> IpcResponse:
        """Create an error response."""
        return cls(id=cmd_id, error=IpcError(code=code, message=message))


# Type alias for async IPC handler functions (lazy — safe under TYPE_CHECKING imports)
type IpcHandler = Callable[..., Coroutine[Any, Any, Any]]


class IpcRegistry:
    """Registry of named IPC command handlers.

    Handlers are registered by name and dispatched when a matching
    :class:`IpcCommand` is received from the Tauri frontend.

    Args:
        default_timeout_s: Per-command timeout in seconds (0 = no timeout).
    """

    def __init__(self, default_timeout_s: float = 30.0) -> None:
        self._handlers: dict[str, IpcHandler] = {}
        self._timeout = default_timeout_s

    def command(self, name: str) -> Callable[[IpcHandler], IpcHandler]:
        """Decorator to register a handler for the given command name.

        Args:
            name: Command name as used by the Tauri frontend.

        Returns:
            Decorator that registers the function.
        """

        def decorator(fn: IpcHandler) -> IpcHandler:
            self._handlers[name] = fn
            return fn

        return decorator

    def register(self, name: str, handler: IpcHandler) -> None:
        """Imperatively register a handler.

        Args:
            name: Command name.
            handler: Async callable.
        """
        self._handlers[name] = handler

    def has_command(self, name: str) -> bool:
        """Return True if *name* is registered."""
        return name in self._handlers

    @property
    def command_names(self) -> list[str]:
        """Sorted list of registered command names."""
        return sorted(self._handlers)

    async def dispatch(self, cmd: IpcCommand) -> IpcResponse:
        """Dispatch a command to its handler and return the response.

        Args:
            cmd: Parsed :class:`IpcCommand`.

        Returns:
            :class:`IpcResponse` — always returns, never raises.
        """
        handler = self._handlers.get(cmd.command)
        if handler is None:
            return IpcResponse.failure(
                cmd.id,
                IpcErrorCode.METHOD_NOT_FOUND,
                f"Unknown command: {cmd.command!r}",
            )

        try:
            result = await handler(**cmd.payload)
        except TypeError as exc:
            return IpcResponse.failure(
                cmd.id,
                IpcErrorCode.INVALID_PARAMS,
                f"Invalid params for {cmd.command!r}: {exc}",
            )
        except Exception as exc:
            tb = traceback.format_exc()
            return IpcResponse.failure(
                cmd.id,
                IpcErrorCode.HANDLER_EXCEPTION,
                f"Handler {cmd.command!r} raised {type(exc).__name__}: {exc}\n{tb}",
            )
        else:
            return IpcResponse.success(cmd.id, result)
