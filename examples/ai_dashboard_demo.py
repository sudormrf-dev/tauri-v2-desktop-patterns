"""AI dashboard demo: Tauri v2 + Python sidecar full-stack walkthrough.

Demonstrates the complete flow of an AI-powered desktop dashboard built with
Tauri v2 (Rust frontend) and a Python AI engine running as a sidecar:

    Rust command handler
        → IPC bridge (JSON over stdin/stdout)
            → Python AI engine
                → streaming response tokens
                    → Rust frontend renders chunks

This script simulates the communication without a real Tauri runtime, making
it runnable standalone for documentation and integration-testing purposes.

Usage::

    python -m examples.ai_dashboard_demo
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from patterns.ipc_bridge import IpcCommand, IpcRegistry, IpcResponse

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger("ai_dashboard_demo")


# ---------------------------------------------------------------------------
# Simulated Python AI engine
# ---------------------------------------------------------------------------


@dataclass
class AiEngineConfig:
    """Configuration for the simulated AI engine sidecar.

    Attributes:
        model_name: Name of the AI model being simulated.
        chunk_delay_s: Simulated per-token streaming delay in seconds.
        tokens_per_response: Number of tokens in each streamed response.
    """

    model_name: str = "tauri-llm-v1"
    chunk_delay_s: float = 0.05
    tokens_per_response: int = 20


class PythonAiEngine:
    """Simulated Python AI engine that registers IPC handlers.

    In a real Tauri app this class lives inside the sidecar process and reads
    commands from stdin. Here it registers handlers on an :class:`IpcRegistry`
    and processes commands in-process for demonstration.

    Args:
        config: AI engine settings.
    """

    def __init__(self, config: AiEngineConfig | None = None) -> None:
        self._cfg = config or AiEngineConfig()
        self.registry = IpcRegistry(default_timeout_s=30.0)
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Wire all supported IPC commands into the registry."""

        @self.registry.command("ai_query")
        async def ai_query(prompt: str, session_id: str = "") -> dict[str, Any]:
            """Handle a single-shot AI query.

            Args:
                prompt: User prompt text.
                session_id: Optional session identifier for context retention.

            Returns:
                Dict with ``answer``, ``model``, and ``latency_ms`` fields.
            """
            start = time.perf_counter()
            await asyncio.sleep(self._cfg.chunk_delay_s * 3)  # simulate inference
            answer = f"[{self._cfg.model_name}] Response to: {prompt!r} (session={session_id or 'anon'})"
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            return {"answer": answer, "model": self._cfg.model_name, "latency_ms": elapsed_ms}

        @self.registry.command("ai_stream_query")
        async def ai_stream_query(prompt: str, max_tokens: int = 50) -> dict[str, Any]:
            """Handle a streaming AI query, yielding chunks one-by-one.

            In a real sidecar, each chunk would be written to stdout as an
            IPC event. Here we collect chunks and return the full result.

            Args:
                prompt: User prompt text.
                max_tokens: Maximum tokens to generate.

            Returns:
                Dict with ``chunks`` list and ``total_tokens`` count.
            """
            token_count = min(max_tokens, self._cfg.tokens_per_response)
            chunks: list[str] = []
            for i in range(token_count):
                await asyncio.sleep(self._cfg.chunk_delay_s)
                chunk = f"token_{i} "
                chunks.append(chunk)
            return {"chunks": chunks, "total_tokens": token_count, "prompt_echo": prompt[:40]}

        @self.registry.command("model_info")
        async def model_info() -> dict[str, Any]:
            """Return metadata about the loaded AI model.

            Returns:
                Dict with model name, version, and capability flags.
            """
            return {
                "model": self._cfg.model_name,
                "version": "1.0.0",
                "streaming": True,
                "max_context_tokens": 4096,
                "capabilities": ["text_generation", "summarization", "qa"],
            }

        @self.registry.command("health_check")
        async def health_check() -> dict[str, Any]:
            """Liveness probe for the sidecar health-check loop.

            Returns:
                Dict with ``status`` and ``uptime_s`` fields.
            """
            return {"status": "ok", "model": self._cfg.model_name, "uptime_s": 0}


# ---------------------------------------------------------------------------
# Simulated Rust frontend command dispatcher
# ---------------------------------------------------------------------------


@dataclass
class RustCommand:
    """A command as it would be issued from a Tauri Rust handler.

    Attributes:
        name: IPC command name.
        args: Keyword arguments forwarded to the Python handler.
    """

    name: str
    args: dict[str, Any] = field(default_factory=dict)

    def to_ipc_command(self) -> IpcCommand:
        """Convert to a wire-format :class:`IpcCommand` with a fresh ID."""
        return IpcCommand(id=str(uuid.uuid4())[:8], command=self.name, payload=self.args)


async def simulate_rust_frontend(engine: PythonAiEngine) -> None:
    """Simulate a sequence of Rust frontend → IPC → Python round-trips.

    Exercises the three main command types supported by the AI dashboard:
    1. ``model_info`` — capability discovery at startup
    2. ``health_check`` — readiness probe
    3. ``ai_query`` — single-shot inference
    4. ``ai_stream_query`` — chunked streaming inference

    Args:
        engine: Running :class:`PythonAiEngine` instance.
    """
    commands = [
        RustCommand("model_info"),
        RustCommand("health_check"),
        RustCommand("ai_query", {"prompt": "Explain Tauri v2 sidecars", "session_id": "demo-01"}),
        RustCommand("ai_stream_query", {"prompt": "List IPC patterns", "max_tokens": 10}),
    ]

    for rust_cmd in commands:
        ipc_cmd = rust_cmd.to_ipc_command()
        logger.info("Rust → Python  cmd=%r  id=%s  payload=%s", ipc_cmd.command, ipc_cmd.id, ipc_cmd.payload)

        response: IpcResponse = await engine.registry.dispatch(ipc_cmd)

        if response.ok:
            result_preview = json.dumps(response.result)[:120]
            logger.info("Python → Rust  id=%s  result=%s", response.id, result_preview)
        else:
            assert response.error is not None
            logger.error("Python → Rust  id=%s  error=%s", response.id, response.error.message)


# ---------------------------------------------------------------------------
# Streaming chunk demo
# ---------------------------------------------------------------------------


async def demonstrate_streaming(engine: PythonAiEngine) -> None:
    """Demonstrate chunked response rendering in the Rust UI.

    In a real Tauri app each chunk triggers a ``emit()`` call that updates the
    dashboard in real time. Here we print each chunk as it arrives.

    Args:
        engine: Running :class:`PythonAiEngine` instance.
    """
    logger.info("--- Streaming demo ---")
    ipc_cmd = IpcCommand(
        id="stream-01",
        command="ai_stream_query",
        payload={"prompt": "Describe Tauri sidecar lifecycle", "max_tokens": 8},
    )
    response = await engine.registry.dispatch(ipc_cmd)
    if response.ok and isinstance(response.result, dict):
        chunks: list[str] = response.result.get("chunks", [])
        logger.info("Rendering %d chunks in Tauri webview:", len(chunks))
        for idx, chunk in enumerate(chunks):
            # Simulate Tauri emit("ai_chunk", { chunk }) to the frontend
            logger.info("  [chunk %02d] emit → webview: %r", idx, chunk.strip())
    logger.info("--- Streaming complete ---")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    """Run the full AI dashboard demo end-to-end."""
    logger.info("=== Tauri v2 AI Dashboard Demo ===")
    logger.info("Initialising Python AI engine (sidecar simulation)...")

    engine = PythonAiEngine(AiEngineConfig(model_name="tauri-llm-v1", chunk_delay_s=0.02, tokens_per_response=8))
    logger.info("Registered commands: %s", engine.registry.command_names)

    await simulate_rust_frontend(engine)
    await demonstrate_streaming(engine)

    logger.info("=== Demo complete ===")


if __name__ == "__main__":
    asyncio.run(main())
