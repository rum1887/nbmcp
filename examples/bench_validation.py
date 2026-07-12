"""Benchmark nbmcp's Rust-side validation against pydantic-based validation.

Runs 1000 valid and 1000 malformed tool calls through both nbmcp (Rust
validation) and a FastMCP-equivalent pydantic validation path, measuring
p50 and p99 latency for each combination.

Usage:
    python examples/bench_validation.py
"""

import json
import subprocess
import sys
import time
import statistics
from pathlib import Path

# ── Helpers ──────────────────────────────────────────────────────────────

HERE = Path(__file__).parent


def _run_nbmcp_server(tool_calls: list[dict]) -> list[dict]:
    """Run a subprocess that starts an nbmcp server and makes tool calls."""
    server_script = HERE / "_bench_server.py"
    if not server_script.exists():
        # Generate the server script on the fly
        server_script.write_text("""\
import json, sys
from nbmcp import Nbmcp
mcp = Nbmcp("bench")
@mcp.tool(description="A benchmark tool")
def echo(name: str, count: int = 1, flag: bool = False) -> dict:
    return {"name": name, "count": count, "flag": flag}
if __name__ == "__main__":
    mcp.run()
""")
    proc = subprocess.Popen(
        [sys.executable, str(server_script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    # Initialize
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n")
    proc.stdin.flush()
    proc.stdout.readline()  # consume initialize response
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n")
    proc.stdin.flush()

    results = []
    for call in tool_calls:
        proc.stdin.write(json.dumps(call) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        if line:
            results.append(json.loads(line))
    proc.kill()
    proc.wait()
    return results


def _pydantic_validate(args: dict, schema: dict) -> None:
    """Simulate pydantic validation of tool arguments (FastMCP-style)."""
    # This is a simplified pydantic-like validation that mimics what FastMCP
    # does internally when validating tool call arguments.
    from pydantic import Field, create_model

    # Build a pydantic model from the schema
    field_defs = {}
    for prop_name, prop_schema in schema.get("properties", {}).items():
        js_type = prop_schema.get("type", "string")
        py_type = {"string": str, "integer": int, "number": float, "boolean": bool}.get(js_type, str)
        field_defs[prop_name] = (py_type, Field(default=... if prop_name in schema.get("required", []) else None))
    model = create_model("ToolArgs", **field_defs)
    model(**args)


def bench_nbmcp(n_valid: int = 1000, n_malformed: int = 1000) -> dict:
    """Benchmark nbmcp's Rust-side validation."""
    valid_calls = [
        {
            "jsonrpc": "2.0", "id": i, "method": "tools/call",
            "params": {"name": "echo", "arguments": {"name": "test", "count": 42, "flag": True}},
        }
        for i in range(n_valid)
    ]
    malformed_calls = [
        {
            "jsonrpc": "2.0", "id": i, "method": "tools/call",
            "params": {"name": "echo", "arguments": {"name": 123, "count": "not-an-int"}},
        }
        for i in range(n_valid, n_valid + n_malformed)
    ]

    all_calls = valid_calls + malformed_calls

    # Warmup
    _run_nbmcp_server(all_calls[:10])

    timings = {"valid": [], "malformed": []}

    # Measure in batches to avoid overwhelming the subprocess
    batch_size = 200
    for start in range(0, len(all_calls), batch_size):
        batch = all_calls[start:start + batch_size]
        t0 = time.perf_counter_ns()
        results = _run_nbmcp_server(batch)
        t1 = time.perf_counter_ns()
        elapsed_us = (t1 - t0) / 1000
        per_call_us = elapsed_us / len(batch) if batch else 0

        for r in results:
            is_error = r.get("error") is not None or r.get("result", {}).get("isError", False)
            category = "malformed" if is_error else "valid"
            timings[category].append(per_call_us)

    return {
        "valid": {
            "p50": statistics.median(timings["valid"]) if timings["valid"] else 0,
            "p99": _percentile(timings["valid"], 99) if timings["valid"] else 0,
            "count": len(timings["valid"]),
        },
        "malformed": {
            "p50": statistics.median(timings["malformed"]) if timings["malformed"] else 0,
            "p99": _percentile(timings["malformed"], 99) if timings["malformed"] else 0,
            "count": len(timings["malformed"]),
        },
    }


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


def main():
    print("=== nbmcp Rust-side validation benchmark ===")
    print("Benchmarking 1000 valid + 1000 malformed calls per framework...")
    print()

    print("Running nbmcp benchmark...")
    nbmcp_results = bench_nbmcp(1000, 1000)
    print("  nbmcp valid:     p50={p50:.1f}µs  p99={p99:.1f}µs  (n={count})".format(**nbmcp_results["valid"]))
    print("  nbmcp malformed: p50={p50:.1f}µs  p99={p99:.1f}µs  (n={count})".format(**nbmcp_results["malformed"]))
    print()

    print("Running pydantic (FastMCP-style) benchmark...")
    pydantic_results = bench_pydantic(1000, 1000)
    print("  pydantic valid:     p50={p50:.1f}µs  p99={p99:.1f}µs  (n={count})".format(**pydantic_results["valid"]))
    print("  pydantic malformed: p50={p50:.1f}µs  p99={p99:.1f}µs  (n={count})".format(**pydantic_results["malformed"]))
    print()

    print("=== Summary ===")
    print("| Framework | Call type | p50 (µs) | p99 (µs) |")
    print("|-----------|----------|----------|----------|")
    print(f"| nbmcp (Rust) | Valid | {nbmcp_results['valid']['p50']:.1f} | {nbmcp_results['valid']['p99']:.1f} |")
    print(f"| nbmcp (Rust) | Malformed | {nbmcp_results['malformed']['p50']:.1f} | {nbmcp_results['malformed']['p99']:.1f} |")
    print(f"| FastMCP (pydantic) | Valid | {pydantic_results['valid']['p50']:.1f} | {pydantic_results['valid']['p99']:.1f} |")
    print(f"| FastMCP (pydantic) | Malformed | {pydantic_results['malformed']['p50']:.1f} | {pydantic_results['malformed']['p99']:.1f} |")


if __name__ == "__main__":
    main()