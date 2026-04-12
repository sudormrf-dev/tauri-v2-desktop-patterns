"""Tests for sidecar_manager.py."""

from __future__ import annotations

import pytest

from patterns.sidecar_manager import (
    SidecarConfig,
    SidecarProcess,
    SidecarState,
    StdioMessage,
    StdioMessageType,
)


class TestStdioMessage:
    def test_to_json_round_trip(self):
        msg = StdioMessage(
            type=StdioMessageType.COMMAND,
            id="abc",
            payload={"command": "greet", "name": "Alice"},
        )
        raw = msg.to_json()
        restored = StdioMessage.from_json(raw)
        assert restored.type == StdioMessageType.COMMAND
        assert restored.id == "abc"
        assert restored.payload["command"] == "greet"

    def test_from_json_with_newline(self):
        msg = StdioMessage(type=StdioMessageType.HEARTBEAT, id="", payload={})
        raw = msg.to_json() + "\n"
        restored = StdioMessage.from_json(raw)
        assert restored.type == StdioMessageType.HEARTBEAT

    def test_from_json_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid sidecar JSON"):
            StdioMessage.from_json("not json")

    def test_from_json_unknown_type_raises(self):
        import json

        raw = json.dumps({"type": "UNKNOWN_TYPE", "id": "", "payload": {}})
        with pytest.raises(ValueError, match="Unknown message type"):
            StdioMessage.from_json(raw)

    def test_all_message_types(self):
        for msg_type in StdioMessageType:
            msg = StdioMessage(type=msg_type, payload={})
            restored = StdioMessage.from_json(msg.to_json())
            assert restored.type == msg_type


class TestSidecarConfig:
    def test_defaults(self):
        cfg = SidecarConfig()
        assert cfg.executable == "python"
        assert cfg.restart_on_crash is True
        assert cfg.max_restarts == 5

    def test_custom_values(self):
        cfg = SidecarConfig(executable="/usr/bin/python3", args=["-m", "sidecar"], max_restarts=3)
        assert cfg.args == ["-m", "sidecar"]
        assert cfg.max_restarts == 3


class TestSidecarProcess:
    def test_initial_state_stopped(self):
        p = SidecarProcess()
        assert p.state == SidecarState.STOPPED

    def test_pid_none_when_stopped(self):
        p = SidecarProcess()
        assert p.pid is None

    def test_not_running_when_stopped(self):
        p = SidecarProcess()
        assert p.is_running() is False

    def test_restart_count_zero(self):
        p = SidecarProcess()
        assert p.restart_count == 0

    async def test_start_invalid_executable_returns_false(self):
        cfg = SidecarConfig(executable="/nonexistent/python_xyz")
        p = SidecarProcess(cfg)
        result = await p.start()
        assert result is False
        assert p.state == SidecarState.CRASHED

    async def test_start_when_running_raises(self):
        cfg = SidecarConfig(executable="python", args=["-c", "import time; time.sleep(10)"])
        p = SidecarProcess(cfg)
        started = await p.start()
        if started:
            with pytest.raises(RuntimeError):
                await p.start()
            await p.stop()

    async def test_stop_when_stopped_is_noop(self):
        p = SidecarProcess()
        await p.stop()  # Should not raise
        assert p.state == SidecarState.STOPPED

    async def test_send_message_raises_when_not_running(self):
        p = SidecarProcess()
        msg = StdioMessage(type=StdioMessageType.COMMAND, payload={})
        with pytest.raises(RuntimeError):
            await p.send_message(msg)

    def test_should_retry_false_when_disabled(self):
        cfg = SidecarConfig(restart_on_crash=False)
        p = SidecarProcess(cfg)
        assert p.should_restart() is False

    def test_should_retry_true_infinite(self):
        cfg = SidecarConfig(restart_on_crash=True, max_restarts=0)
        p = SidecarProcess(cfg)
        assert p.should_restart() is True

    def test_should_retry_false_at_limit(self):
        cfg = SidecarConfig(restart_on_crash=True, max_restarts=2)
        p = SidecarProcess(cfg)
        p._restart_count = 2
        assert p.should_restart() is False

    def test_should_retry_true_below_limit(self):
        cfg = SidecarConfig(restart_on_crash=True, max_restarts=3)
        p = SidecarProcess(cfg)
        p._restart_count = 2
        assert p.should_restart() is True
