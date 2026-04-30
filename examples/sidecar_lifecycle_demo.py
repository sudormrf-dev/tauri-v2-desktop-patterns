"""Sidecar lifecycle demo: bundle → start → health-check → shutdown/restart.

Walks through the complete lifecycle of a Tauri v2 Python sidecar without
requiring a real Tauri runtime or an actual Python subprocess. All I/O is
simulated so the script is safe to run in CI and documentation contexts.

Lifecycle stages demonstrated:

1. **Bundle** — select a strategy, validate config, print build plan
2. **Start** — transition through STOPPED → STARTING → RUNNING
3. **Health-check loop** — send periodic heartbeats, detect unhealthy state
4. **Crash simulation** — force the sidecar into CRASHED state
5. **Auto-restart** — honour ``restart_on_crash`` / ``max_restarts`` limits
6. **Graceful shutdown** — transition RUNNING → STOPPING → STOPPED

Usage::

    python -m examples.sidecar_lifecycle_demo
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

from patterns.python_bundler import BundleConfig, BundleStrategy, PythonBundler, detect_runtime
from patterns.sidecar_manager import SidecarConfig, SidecarState

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger("sidecar_lifecycle_demo")


# ---------------------------------------------------------------------------
# Simulated sidecar (no real subprocess needed)
# ---------------------------------------------------------------------------


class SimulatedSidecarState(Enum):
    """Lifecycle state of the simulated sidecar."""

    STOPPED = auto()
    STARTING = auto()
    RUNNING = auto()
    STOPPING = auto()
    CRASHED = auto()


@dataclass
class HealthReport:
    """Result of a single health-check probe.

    Attributes:
        healthy: True when the sidecar responded within the timeout.
        latency_ms: Round-trip time in milliseconds.
        message: Human-readable status message.
    """

    healthy: bool
    latency_ms: float
    message: str


class SimulatedSidecarProcess:
    """Simulates a Python sidecar subprocess for lifecycle demonstrations.

    Mirrors the API of :class:`~patterns.sidecar_manager.SidecarProcess` but
    never spawns a real OS process. Crash injection is supported via
    :meth:`inject_crash` to test the restart path.

    Args:
        config: Sidecar configuration (used for restart limits / timeouts).
        startup_delay_s: How long ``start()`` appears to take.
    """

    def __init__(self, config: SidecarConfig | None = None, startup_delay_s: float = 0.05) -> None:
        self._cfg = config or SidecarConfig()
        self._state = SimulatedSidecarState.STOPPED
        self._restart_count = 0
        self._start_time: float | None = None
        self._startup_delay = startup_delay_s
        self._pid = 99_000  # fake PID

    @property
    def state(self) -> SimulatedSidecarState:
        """Current lifecycle state."""
        return self._state

    @property
    def restart_count(self) -> int:
        """Number of crash-restarts performed so far."""
        return self._restart_count

    @property
    def uptime_s(self) -> float:
        """Seconds since last successful start, or 0 if not running."""
        if self._start_time is None:
            return 0.0
        return time.monotonic() - self._start_time

    def is_running(self) -> bool:
        """True if the sidecar is in RUNNING state."""
        return self._state == SimulatedSidecarState.RUNNING

    async def start(self) -> bool:
        """Simulate starting the sidecar subprocess.

        Returns:
            True when successfully transitioned to RUNNING.
        """
        if self._state not in {SimulatedSidecarState.STOPPED, SimulatedSidecarState.CRASHED}:
            logger.warning("Cannot start sidecar from state %s", self._state.name)
            return False

        logger.info("  [%s → STARTING] spawning simulated sidecar (pid=%d)", self._state.name, self._pid)
        self._state = SimulatedSidecarState.STARTING
        await asyncio.sleep(self._startup_delay)
        self._state = SimulatedSidecarState.RUNNING
        self._start_time = time.monotonic()
        logger.info("  [RUNNING] sidecar is alive (uptime=%.2fs)", self.uptime_s)
        return True

    async def stop(self) -> None:
        """Simulate graceful shutdown."""
        if self._state not in {SimulatedSidecarState.RUNNING, SimulatedSidecarState.STARTING}:
            return
        logger.info("  [%s → STOPPING] sending SIGTERM to pid=%d", self._state.name, self._pid)
        self._state = SimulatedSidecarState.STOPPING
        await asyncio.sleep(0.02)  # simulate drain time
        self._state = SimulatedSidecarState.STOPPED
        self._start_time = None
        logger.info("  [STOPPED] sidecar exited cleanly")

    def inject_crash(self) -> None:
        """Force the sidecar into CRASHED state (testing helper)."""
        logger.warning("  [CRASH INJECTED] sidecar pid=%d terminated unexpectedly", self._pid)
        self._state = SimulatedSidecarState.CRASHED
        self._start_time = None

    async def health_check(self) -> HealthReport:
        """Send a heartbeat and measure round-trip time.

        Returns:
            :class:`HealthReport` with liveness and latency information.
        """
        if not self.is_running():
            return HealthReport(healthy=False, latency_ms=0.0, message=f"sidecar is {self._state.name}")
        t0 = time.perf_counter()
        await asyncio.sleep(0.005)  # simulate IPC round-trip
        latency_ms = (time.perf_counter() - t0) * 1000
        return HealthReport(healthy=True, latency_ms=latency_ms, message="heartbeat ok")

    async def restart(self) -> bool:
        """Stop then start; increment restart counter.

        Returns:
            True if the restart succeeded.
        """
        await self.stop()
        self._restart_count += 1
        logger.info("  [RESTART #%d] initiating restart...", self._restart_count)
        return await self.start()

    def should_restart(self) -> bool:
        """True if crash-restart policy allows another attempt."""
        if not self._cfg.restart_on_crash:
            return False
        if self._cfg.max_restarts == 0:
            return True
        return self._restart_count < self._cfg.max_restarts

    def _state_as_sidecar_state(self) -> SidecarState:
        """Map simulated state to the canonical :class:`SidecarState` enum."""
        mapping = {
            SimulatedSidecarState.STOPPED: SidecarState.STOPPED,
            SimulatedSidecarState.STARTING: SidecarState.STARTING,
            SimulatedSidecarState.RUNNING: SidecarState.RUNNING,
            SimulatedSidecarState.STOPPING: SidecarState.STOPPING,
            SimulatedSidecarState.CRASHED: SidecarState.CRASHED,
        }
        return mapping[self._state]


# ---------------------------------------------------------------------------
# Lifecycle stages
# ---------------------------------------------------------------------------


def stage_bundle(strategy: BundleStrategy = BundleStrategy.PYINSTALLER) -> None:
    """Stage 1 — bundle Python + dependencies.

    Args:
        strategy: Bundling strategy to demonstrate.
    """
    logger.info("=== Stage 1: Bundle ===")
    runtime = detect_runtime()
    logger.info("Detected Python %s on %s (venv=%s)", runtime.version, runtime.platform, runtime.is_venv)

    config = BundleConfig(
        strategy=strategy,
        entry_point="sidecar/__main__.py",
        app_name="ai-sidecar",
    )
    bundler = PythonBundler(config)
    warnings = bundler.validate()
    if warnings:
        for w in warnings:
            logger.warning("  Bundler warning: %s", w)
    else:
        logger.info("  Config valid — no warnings")

    min_mb, max_mb = bundler.estimated_size_mb()
    logger.info("  Estimated bundle size: %d–%d MB (strategy=%s)", min_mb, max_mb, strategy.value)
    logger.info("  Output binary: %s", bundler.output_binary_name())
    logger.info("  Build plan:")
    for step in bundler.build_plan():
        logger.info("    $ %s", step)


async def stage_start(sidecar: SimulatedSidecarProcess) -> None:
    """Stage 2 — start the sidecar process.

    Args:
        sidecar: The sidecar to start.
    """
    logger.info("=== Stage 2: Start ===")
    ok = await sidecar.start()
    if not ok:
        logger.error("Sidecar failed to start — aborting demo")
        raise RuntimeError("sidecar start failed")


async def stage_health_loop(sidecar: SimulatedSidecarProcess, probes: int = 4) -> None:
    """Stage 3 — run a health-check loop with periodic heartbeats.

    Args:
        sidecar: Running sidecar to probe.
        probes: Number of health-check iterations.
    """
    logger.info("=== Stage 3: Health-check loop (%d probes) ===", probes)
    for i in range(probes):
        report = await sidecar.health_check()
        status = "OK" if report.healthy else "FAIL"
        logger.info("  probe %d/%d  status=%-4s  latency=%.2fms  msg=%s", i + 1, probes, status, report.latency_ms, report.message)
        await asyncio.sleep(0.05)


async def stage_crash_and_restart(sidecar: SimulatedSidecarProcess) -> None:
    """Stage 4 & 5 — simulate a crash and auto-restart.

    Args:
        sidecar: Running sidecar to crash then restart.
    """
    logger.info("=== Stage 4: Crash simulation ===")
    sidecar.inject_crash()
    report = await sidecar.health_check()
    logger.info("  Health after crash: healthy=%s  msg=%s", report.healthy, report.message)

    logger.info("=== Stage 5: Auto-restart ===")
    if sidecar.should_restart():
        ok = await sidecar.restart()
        logger.info("  Restart #%d %s", sidecar.restart_count, "succeeded" if ok else "FAILED")
        report = await sidecar.health_check()
        logger.info("  Health after restart: healthy=%s  msg=%s", report.healthy, report.message)
    else:
        logger.warning("  Restart policy exhausted — sidecar will not be restarted")


async def stage_shutdown(sidecar: SimulatedSidecarProcess) -> None:
    """Stage 6 — graceful shutdown.

    Args:
        sidecar: Running sidecar to stop.
    """
    logger.info("=== Stage 6: Graceful shutdown ===")
    logger.info("  Uptime before shutdown: %.2fs", sidecar.uptime_s)
    await sidecar.stop()
    logger.info("  Final state: %s", sidecar.state.name)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    """Run all lifecycle stages in sequence."""
    logger.info("=== Tauri v2 Python Sidecar Lifecycle Demo ===")

    # Stage 1: show bundling plan (synchronous)
    stage_bundle(BundleStrategy.PYINSTALLER)

    # Create the simulated sidecar using SidecarConfig for restart policy
    cfg = SidecarConfig(
        executable="python",
        args=["-m", "sidecar"],
        startup_timeout_s=5.0,
        shutdown_timeout_s=3.0,
        restart_on_crash=True,
        max_restarts=3,
    )
    sidecar = SimulatedSidecarProcess(config=cfg, startup_delay_s=0.05)

    await stage_start(sidecar)
    await stage_health_loop(sidecar, probes=3)
    await stage_crash_and_restart(sidecar)
    await stage_shutdown(sidecar)

    logger.info("=== Lifecycle demo complete ===")


if __name__ == "__main__":
    asyncio.run(main())
