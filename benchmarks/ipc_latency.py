"""IPC latency benchmark: round-trip time across payload size categories.

Measures simulated Rust ↔ Python IPC latency for three payload classes:

============  =========  ==================================
Category      Size       Transport strategy
============  =========  ==================================
Small         < 1 KB     Synchronous JSON dispatch
Medium        1–100 KB   JSON serialise / deserialise
Large         > 100 KB   Streaming in fixed-size chunks
============  =========  ==================================

All measurements are in-process simulations — no real Tauri IPC socket is
used — so results reflect pure Python JSON overhead plus simulated async
context-switch cost, not OS socket latency.

Usage::

    python -m benchmarks.ipc_latency

    # Or from a test / script:
    from benchmarks.ipc_latency import run_all, print_table
    results = asyncio.run(run_all())
    print_table(results)
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field

from patterns.ipc_bridge import IpcCommand, IpcRegistry, IpcResponse


# ---------------------------------------------------------------------------
# Payload generators
# ---------------------------------------------------------------------------


def _make_small_payload(size_bytes: int = 256) -> dict:
    """Return a small JSON-serialisable dict of approximately *size_bytes*.

    Args:
        size_bytes: Target serialised size in bytes.

    Returns:
        Dict with a ``data`` key containing a padded string.
    """
    padding = "x" * max(0, size_bytes - 30)
    return {"command": "echo", "data": padding}


def _make_medium_payload(size_bytes: int = 10_000) -> dict:
    """Return a medium JSON-serialisable dict of approximately *size_bytes*.

    Args:
        size_bytes: Target serialised size in bytes.

    Returns:
        Dict with a ``records`` list of small objects.
    """
    record_size = 50
    record_count = max(1, size_bytes // record_size)
    records = [{"id": i, "value": "v" * 20} for i in range(record_count)]
    return {"command": "batch_process", "records": records}


def _make_large_payload(size_bytes: int = 200_000) -> dict:
    """Return a large JSON-serialisable dict of approximately *size_bytes*.

    Args:
        size_bytes: Target serialised size in bytes.

    Returns:
        Dict with a ``blob`` key containing a long string.
    """
    blob = "A" * max(0, size_bytes - 40)
    return {"command": "process_blob", "blob": blob}


# ---------------------------------------------------------------------------
# Benchmark result types
# ---------------------------------------------------------------------------


@dataclass
class LatencyResult:
    """Latency measurements for a single payload category.

    Attributes:
        category: Human-readable category label (e.g. "Small < 1 KB").
        payload_bytes: Actual serialised payload size in bytes.
        iterations: Number of round-trips measured.
        latencies_ms: Per-iteration round-trip times in milliseconds.
    """

    category: str
    payload_bytes: int
    iterations: int
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def mean_ms(self) -> float:
        """Mean round-trip time in milliseconds."""
        return statistics.mean(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def median_ms(self) -> float:
        """Median round-trip time in milliseconds."""
        return statistics.median(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def p99_ms(self) -> float:
        """99th-percentile round-trip time in milliseconds."""
        if not self.latencies_ms:
            return 0.0
        sorted_ms = sorted(self.latencies_ms)
        idx = max(0, int(len(sorted_ms) * 0.99) - 1)
        return sorted_ms[idx]

    @property
    def min_ms(self) -> float:
        """Minimum round-trip time in milliseconds."""
        return min(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def max_ms(self) -> float:
        """Maximum round-trip time in milliseconds."""
        return max(self.latencies_ms) if self.latencies_ms else 0.0


# ---------------------------------------------------------------------------
# Simulated IPC handlers
# ---------------------------------------------------------------------------


def _build_registry() -> IpcRegistry:
    """Build an IpcRegistry with handlers for each benchmark command.

    Returns:
        Populated :class:`~patterns.ipc_bridge.IpcRegistry`.
    """
    registry = IpcRegistry(default_timeout_s=30.0)

    @registry.command("echo")
    async def echo(data: str = "") -> dict:
        """Echo handler: deserialise payload and return its size.

        Args:
            data: Arbitrary string payload (ignored beyond length check).

        Returns:
            Dict with ``echoed_bytes`` count.
        """
        return {"echoed_bytes": len(data)}

    @registry.command("batch_process")
    async def batch_process(records: list | None = None) -> dict:
        """Batch handler: iterate records and return count.

        Args:
            records: List of record dicts.

        Returns:
            Dict with ``processed`` count.
        """
        recs = records or []
        # Simulate minimal CPU work: JSON round-trip per record
        serialised = json.dumps(recs)
        parsed = json.loads(serialised)
        return {"processed": len(parsed)}

    @registry.command("process_blob")
    async def process_blob(blob: str = "") -> dict:
        """Streaming blob handler: process in 64 KB chunks.

        Args:
            blob: Large string payload.

        Returns:
            Dict with ``chunks_processed`` count and ``total_bytes``.
        """
        chunk_size = 65_536
        chunks = [blob[i : i + chunk_size] for i in range(0, len(blob), chunk_size)]
        # Simulate per-chunk async yield (mimics real streaming IPC)
        for _ in chunks:
            await asyncio.sleep(0)
        return {"chunks_processed": len(chunks), "total_bytes": len(blob)}

    return registry


# ---------------------------------------------------------------------------
# Benchmark runners
# ---------------------------------------------------------------------------


async def _measure_latency(
    registry: IpcRegistry,
    command: str,
    payload: dict,
    iterations: int,
) -> list[float]:
    """Run *iterations* round-trips and return per-iteration latencies in ms.

    Args:
        registry: Populated command registry.
        command: IPC command name to call.
        payload: Payload dict for the command.
        iterations: Number of round-trips to measure.

    Returns:
        List of round-trip times in milliseconds.
    """
    latencies: list[float] = []
    for i in range(iterations):
        cmd = IpcCommand(id=f"bench-{i:04d}", command=command, payload=payload)
        t0 = time.perf_counter()
        _resp: IpcResponse = await registry.dispatch(cmd)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed_ms)
    return latencies


async def bench_small(registry: IpcRegistry, iterations: int = 200) -> LatencyResult:
    """Benchmark small (< 1 KB) synchronous IPC payloads.

    Args:
        registry: Command registry.
        iterations: Number of round-trips.

    Returns:
        :class:`LatencyResult` for the small category.
    """
    payload = _make_small_payload(256)
    serialised_size = len(json.dumps(payload).encode())
    latencies = await _measure_latency(registry, "echo", payload, iterations)
    return LatencyResult(
        category="Small  < 1 KB  (sync JSON)",
        payload_bytes=serialised_size,
        iterations=iterations,
        latencies_ms=latencies,
    )


async def bench_medium(registry: IpcRegistry, iterations: int = 100) -> LatencyResult:
    """Benchmark medium (1–100 KB) JSON serialisation IPC payloads.

    Args:
        registry: Command registry.
        iterations: Number of round-trips.

    Returns:
        :class:`LatencyResult` for the medium category.
    """
    payload = _make_medium_payload(10_000)
    serialised_size = len(json.dumps(payload).encode())
    latencies = await _measure_latency(registry, "batch_process", payload, iterations)
    return LatencyResult(
        category="Medium 1–100 KB (JSON serde)",
        payload_bytes=serialised_size,
        iterations=iterations,
        latencies_ms=latencies,
    )


async def bench_large(registry: IpcRegistry, iterations: int = 20) -> LatencyResult:
    """Benchmark large (> 100 KB) streaming-chunk IPC payloads.

    Args:
        registry: Command registry.
        iterations: Number of round-trips.

    Returns:
        :class:`LatencyResult` for the large category.
    """
    payload = _make_large_payload(200_000)
    serialised_size = len(json.dumps(payload).encode())
    latencies = await _measure_latency(registry, "process_blob", payload, iterations)
    return LatencyResult(
        category="Large  > 100 KB (streaming)",
        payload_bytes=serialised_size,
        iterations=iterations,
        latencies_ms=latencies,
    )


async def run_all() -> list[LatencyResult]:
    """Run all three latency benchmarks and return results.

    Returns:
        List of :class:`LatencyResult` — one per payload category.
    """
    registry = _build_registry()
    results: list[LatencyResult] = []
    for bench_fn in (bench_small, bench_medium, bench_large):
        result = await bench_fn(registry)
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_table(results: list[LatencyResult]) -> None:
    """Print a formatted latency table to stdout.

    Args:
        results: List of :class:`LatencyResult` to display.
    """
    col_w = [32, 10, 6, 8, 8, 8, 8, 8]
    headers = ["Category", "Size (B)", "N", "min ms", "med ms", "mean ms", "p99 ms", "max ms"]
    sep = "  ".join("-" * w for w in col_w)

    print()
    print("Tauri v2 IPC Latency Benchmark — Rust ↔ Python round-trip (simulated)")
    print(sep)
    header_line = "  ".join(h.ljust(w) for h, w in zip(headers, col_w, strict=False))
    print(header_line)
    print(sep)

    for r in results:
        row = [
            r.category.ljust(col_w[0]),
            str(r.payload_bytes).rjust(col_w[1]),
            str(r.iterations).rjust(col_w[2]),
            f"{r.min_ms:.3f}".rjust(col_w[3]),
            f"{r.median_ms:.3f}".rjust(col_w[4]),
            f"{r.mean_ms:.3f}".rjust(col_w[5]),
            f"{r.p99_ms:.3f}".rjust(col_w[6]),
            f"{r.max_ms:.3f}".rjust(col_w[7]),
        ]
        print("  ".join(row))

    print(sep)
    print()
    print("Notes:")
    print("  • All latencies are in-process Python simulations (no real IPC socket).")
    print("  • Real Tauri IPC adds ~0.1–2 ms OS overhead on Linux/macOS/Windows.")
    print("  • Large payloads use async chunking; median reflects per-chunk yield cost.")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _main() -> None:
    """Run benchmarks and display the latency table."""
    print("Running benchmarks (this takes a few seconds)...")
    results = await run_all()
    print_table(results)


if __name__ == "__main__":
    asyncio.run(_main())
