"""Tests for ipc_bridge.py."""

from __future__ import annotations

import pytest

from patterns.ipc_bridge import (
    IpcCommand,
    IpcError,
    IpcErrorCode,
    IpcRegistry,
    IpcResponse,
)


class TestIpcCommand:
    def test_from_json_valid(self):
        raw = '{"id": "abc", "command": "greet", "payload": {"name": "Alice"}}'
        cmd = IpcCommand.from_json(raw)
        assert cmd.id == "abc"
        assert cmd.command == "greet"
        assert cmd.payload == {"name": "Alice"}

    def test_from_json_minimal(self):
        raw = '{"id": "1", "command": "ping"}'
        cmd = IpcCommand.from_json(raw)
        assert cmd.payload == {}

    def test_from_json_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            IpcCommand.from_json("not json")

    def test_from_json_non_object_raises(self):
        with pytest.raises(ValueError):
            IpcCommand.from_json('"just a string"')

    def test_to_json_round_trip(self):
        cmd = IpcCommand(id="x1", command="add", payload={"a": 1, "b": 2})
        restored = IpcCommand.from_json(cmd.to_json())
        assert restored.id == "x1"
        assert restored.command == "add"
        assert restored.payload == {"a": 1, "b": 2}


class TestIpcResponse:
    def test_success_ok(self):
        r = IpcResponse.success("id1", {"val": 42})
        assert r.ok is True
        assert r.error is None

    def test_failure_not_ok(self):
        r = IpcResponse.failure("id1", IpcErrorCode.INTERNAL_ERROR, "oops")
        assert r.ok is False
        assert r.error is not None

    def test_to_json_success(self):
        import json

        r = IpcResponse.success("id1", 99)
        data = json.loads(r.to_json())
        assert data["id"] == "id1"
        assert data["result"] == 99
        assert "error" not in data

    def test_to_json_error(self):
        import json

        r = IpcResponse.failure("id1", IpcErrorCode.METHOD_NOT_FOUND, "no such command")
        data = json.loads(r.to_json())
        assert "error" in data
        assert data["error"]["code"] == IpcErrorCode.METHOD_NOT_FOUND


class TestIpcError:
    def test_to_dict_includes_data(self):
        err = IpcError(
            code=IpcErrorCode.INVALID_PARAMS,
            message="bad params",
            data={"field": "name"},
        )
        d = err.to_dict()
        assert d["code"] == IpcErrorCode.INVALID_PARAMS
        assert d["data"] == {"field": "name"}

    def test_to_dict_omits_none_data(self):
        err = IpcError(code=IpcErrorCode.OK, message="ok")
        d = err.to_dict()
        assert "data" not in d


class TestIpcRegistry:
    async def test_dispatch_registered_command(self):
        reg = IpcRegistry()

        @reg.command("add")
        async def add(a: int, b: int) -> int:
            return a + b

        cmd = IpcCommand(id="1", command="add", payload={"a": 3, "b": 4})
        resp = await reg.dispatch(cmd)
        assert resp.ok
        assert resp.result == 7

    async def test_dispatch_unknown_command(self):
        reg = IpcRegistry()
        cmd = IpcCommand(id="1", command="unknown", payload={})
        resp = await reg.dispatch(cmd)
        assert not resp.ok
        assert resp.error is not None
        assert resp.error.code == IpcErrorCode.METHOD_NOT_FOUND

    async def test_dispatch_invalid_params(self):
        reg = IpcRegistry()

        @reg.command("greet")
        async def greet(name: str) -> str:
            return f"Hello {name}"

        cmd = IpcCommand(id="1", command="greet", payload={"wrong_param": "x"})
        resp = await reg.dispatch(cmd)
        assert not resp.ok
        assert resp.error is not None
        assert resp.error.code == IpcErrorCode.INVALID_PARAMS

    async def test_dispatch_handler_exception(self):
        reg = IpcRegistry()

        @reg.command("boom")
        async def boom() -> None:
            err = "exploded"
            raise RuntimeError(err)

        cmd = IpcCommand(id="1", command="boom", payload={})
        resp = await reg.dispatch(cmd)
        assert not resp.ok
        assert resp.error is not None
        assert resp.error.code == IpcErrorCode.HANDLER_EXCEPTION

    def test_has_command_true(self):
        reg = IpcRegistry()
        reg.register("ping", _async_noop)
        assert reg.has_command("ping") is True

    def test_has_command_false(self):
        reg = IpcRegistry()
        assert reg.has_command("missing") is False

    def test_command_names_sorted(self):
        reg = IpcRegistry()
        reg.register("zebra", _async_noop)
        reg.register("alpha", _async_noop)
        assert reg.command_names == ["alpha", "zebra"]

    async def test_dispatch_returns_correlation_id(self):
        reg = IpcRegistry()
        reg.register("noop", _async_noop)
        cmd = IpcCommand(id="req-42", command="noop", payload={})
        resp = await reg.dispatch(cmd)
        assert resp.id == "req-42"


async def _async_noop() -> None:
    pass
