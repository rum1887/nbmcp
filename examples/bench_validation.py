"""Benchmark nbmcp's Rust-side validation against pydantic-based validation.

Runs 1000 valid and 1000 malformed tool calls through both nbmcp (Rust
validation) and a FastMCP-equivalent pydantic validation path, measuring
p50 and p99 latency for each combination.

This version uses in-process timing for nbmcp (via NativeEngine.bench_validate
and NativeEngine.bench_tool_call) so the comparison is apples-to-apples:
both measure only the validation (or validation+call) time, without
subprocess spawn, IPC, or JSON-RPC protocol overhead.

Usage:
    python examples/bench_validation.py
"""

import json
import statistics
import time
from pathlib import Path

from nbmcp import Nbmcp


# ── Helpers ──────────────────────────────────────────────────────────────


def _percentile(data: list[float], p: int) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (p / 100.0) * (len(sorted_data) - 1)
    f = int(k)
    c = f + 1 if f + 1 < len(sorted_data) else f
    if c == f:
        return sorted_data[f]
    return sorted_data[f] * (c - k) + sorted_data[c] * (k - f)


def _pydantic_validate(args: dict, schema: dict) -> None:
    """Simulate pydantic validation of tool arguments (FastMCP-style)."""
    from pydantic import Field, create_model

    field_defs = {}
    for prop_name, prop_schema in schema.get("properties", {}).items():
        js_type = prop_schema.get("type", "string")
        py_type = {"string": str, "integer": int, "number": float, "boolean": bool}.get(js_type, str)
        field_defs[prop_name] = (py_type, Field(default=... if prop_name in schema.get("required", []) else None))
    model = create_model("ToolArgs", **field_defs)
    model(**args)


# ── In-process nbmcp benchmark ──────────────────────────────────────────


def bench_nbmcp_inprocess(n_valid: int = 1000, n_malformed: int = 1000) -> dict:
    """Benchmark nbmcp's Rust-side validation in-process (no subprocess/IPC).

    Uses NativeEngine.bench_validate() for validation-only timing and
    NativeEngine.bench_tool_call() for validation+Python-call timing.
    Both run entirely in-process, making this an apples-to-apples comparison
    with the pydantic benchmark.
    """
    mcp = Nbmcp("bench")

    @mcp.tool(description="A benchmark tool")
    def echo(name: str, count: int = 1, flag: bool = False) -> dict:
        return {"name": name, "count": count, "flag": flag}

    valid_args = json.dumps({"name": "test", "count": 42, "flag": True})
    malformed_args = json.dumps({"name": 123, "count": "not-an-int"})

    # Build argument lists
    valid_args_list = [valid_args] * n_valid
    malformed_args_list = [malformed_args] * n_malformed

    # Warmup
    _ = mcp._engine.bench_validate("echo", valid_args_list[:10])
    _ = mcp._engine.bench_validate("echo", malformed_args_list[:10])

    # Measure validation only (Rust schema validation, no Python call)
    valid_timings = mcp._engine.bench_validate("echo", valid_args_list)
    malformed_timings = mcp._engine.bench_validate("echo", malformed_args_list)

    # Measure full tool call (validation + Python function call)
    valid_full_timings = mcp._engine.bench_tool_call("echo", valid_args_list)
    malformed_full_timings = mcp._engine.bench_tool_call("echo", malformed_args_list)

    return {
        "valid": {
            "p50": statistics.median(valid_timings) if valid_timings else 0,
            "p99": _percentile(valid_timings, 99) if valid_timings else 0,
            "count": len(valid_timings),
        },
        "malformed": {
            "p50": statistics.median(malformed_timings) if malformed_timings else 0,
            "p99": _percentile(malformed_timings, 99) if malformed_timings else 0,
            "count": len(malformed_timings),
        },
        "valid_full": {
            "p50": statistics.median(valid_full_timings) if valid_full_timings else 0,
            "p99": _percentile(valid_full_timings, 99) if valid_full_timings else 0,
            "count": len(valid_full_timings),
        },
        "malformed_full": {
            "p50": statistics.median(malformed_full_timings) if malformed_full_timings else 0,
            "p99": _percentile(malformed_full_timings, 99) if malformed_full_timings else 0,
            "count": len(malformed_full_timings),
        },
    }


# ── Pydantic benchmark ──────────────────────────────────────────────────


def bench_pydantic(n_valid: int = 1000, n_malformed: int = 1000) -> dict:
    """Benchmark pydantic-based validation (FastMCP-style)."""
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "count": {"type": "integer"},
            "flag": {"type": "boolean"},
        },
        "required": ["name", "count"],
    }

    valid_args = {"name": "test", "count": 42, "flag": True}
    malformed_args = {"name": 123, "count": "not-an-int"}  # wrong types

    timings = {"valid": [], "malformed": []}

    # Warmup
    for _ in range(10):
        _pydantic_validate(valid_args, schema)
    for _ in range(10):
        try:
            _pydantic_validate(malformed_args, schema)
        except Exception:
            pass

    # Measure valid
    for _ in range(n_valid):
        t0 = time.perf_counter_ns()
        _pydantic_validate(valid_args, schema)
        t1 = time.perf_counter_ns()
        timings["valid"].append((t1 - t0) / 1000)

    # Measure malformed
    for _ in range(n_malformed):
        t0 = time.perf_counter_ns()
        try:
            _pydantic_validate(malformed_args, schema)
        except Exception:
            pass
        t1 = time.perf_counter_ns()
        timings["malformed"].append((t1 - t0) / 1000)

    return {
        "valid": {
            "p50": statistics.median(timings["valid"]),
            "p99": _percentile(timings["valid"], 99),
            "count": len(timings["valid"]),
        },
        "malformed": {
            "p50": statistics.median(timings["malformed"]),
            "p99": _percentile(timings["malformed"], 99),
            "count": len(timings["malformed"]),
        },
    }


# ── Main ────────────────────────────────────────────────────────────────


def main():
    print("=== nbmcp Rust-side validation benchmark (in-process) ===")
    print("Benchmarking 1000 valid + 1000 malformed calls per framework...")
    print()

    print("Running nbmcp benchmark (in-process)...")
    nbmcp_results = bench_nbmcp_inprocess(1000, 1000)
    print("  nbmcp validation only:")
    print("    valid:     p50={p50:.3f}µs  p99={p99:.3f}µs  (n={count})".format(**nbmcp_results["valid"]))
    print("    malformed: p50={p50:.3f}µs  p99={p99:.3f}µs  (n={count})".format(**nbmcp_results["malformed"]))
    print("  nbmcp validation + Python call:")
    print("    valid:     p50={p50:.3f}µs  p99={p99:.3f}µs  (n={count})".format(**nbmcp_results["valid_full"]))
    print("    malformed: p50={p50:.3f}µs  p99={p99:.3f}µs  (n={count})".format(**nbmcp_results["malformed_full"]))
    print()

    print("Running pydantic (FastMCP-style) benchmark...")
    pydantic_results = bench_pydantic(1000, 1000)
    print("  pydantic valid:     p50={p50:.3f}µs  p99={p99:.3f}µs  (n={count})".format(**pydantic_results["valid"]))
    print("  pydantic malformed: p50={p50:.3f}µs  p99={p99:.3f}µs  (n={count})".format(**pydantic_results["malformed"]))
    print()

    print("=== Summary ===")
    print("| Framework | Call type | p50 (µs) | p99 (µs) |")
    print("|-----------|----------|----------|----------|")
    print(f"| nbmcp (Rust) — validation only | Valid | {nbmcp_results['valid']['p50']:.3f} | {nbmcp_results['valid']['p99']:.3f} |")
    print(f"| nbmcp (Rust) — validation only | Malformed | {nbmcp_results['malformed']['p50']:.3f} | {nbmcp_results['malformed']['p99']:.3f} |")
    print(f"| nbmcp (Rust) — full call | Valid | {nbmcp_results['valid_full']['p50']:.3f} | {nbmcp_results['valid_full']['p99']:.3f} |")
    print(f"| nbmcp (Rust) — full call | Malformed | {nbmcp_results['malformed_full']['p50']:.3f} | {nbmcp_results['malformed_full']['p99']:.3f} |")
    print(f"| FastMCP (pydantic) | Valid | {pydantic_results['valid']['p50']:.3f} | {pydantic_results['valid']['p99']:.3f} |")
    print(f"| FastMCP (pydantic) | Malformed | {pydantic_results['malformed']['p50']:.3f} | {pydantic_results['malformed']['p99']:.3f} |")


if __name__ == "__main__":
    main()