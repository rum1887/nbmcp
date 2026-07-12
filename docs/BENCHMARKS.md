# Benchmarks

## Rust-side validation (nbmcp) vs. pydantic-based validation (FastMCP)

### Methodology

- **nbmcp**: JSON Schema validation implemented in Rust (`src/schema.rs`), called before the Python tool body executes.
- **FastMCP (pydantic)**: Pydantic model validation of tool arguments inside the Python process, on every call.
- **Test setup**: 1000 valid tool calls + 1000 malformed tool calls (missing required fields, wrong types) for each framework.
- **Measurement**: p50 (median) and p99 (99th percentile) latency in microseconds, measured **in-process** (no subprocess/IPC overhead) for both frameworks.
- **Hardware**: macOS (Apple Silicon M-series), Python 3.14, Rust release build.

### Results

| Framework | Call type | p50 (µs) | p99 (µs) |
|-----------|-----------|----------|----------|
| nbmcp (Rust) — validation only | Valid | 0.458 | 0.542 |
| nbmcp (Rust) — validation only | Malformed | 0.250 | 0.333 |
| nbmcp (Rust) — full call | Valid | 0.792 | 1.000 |
| nbmcp (Rust) — full call | Malformed | 0.209 | 0.375 |
| FastMCP (pydantic) | Valid | 142.834 | 480.547 |
| FastMCP (pydantic) | Malformed | 141.708 | 196.463 |

*Results populated on 2026-07-12. Hardware: macOS (Apple Silicon M-series), Python 3.14, Rust release build.*

### Running the benchmark

```bash
# Build the Rust extension in release mode
python -m maturin develop --release

# Run the benchmark
python examples/bench_validation.py
```

### Key takeaways

- **nbmcp's Rust validation is ~300x faster than pydantic** for valid calls (0.458µs vs 142.834µs p50).
- **nbmcp rejects malformed calls ~560x faster** (0.250µs vs 141.708µs p50) because validation happens in Rust before Python is ever involved.
- **nbmcp's full call path** (validation + Python function call) is still **~180x faster** than pydantic validation alone (0.792µs vs 142.834µs p50).
- Malformed calls are faster than valid calls in nbmcp because the Rust validator rejects them immediately without ever calling into Python.
- The previous benchmark version measured subprocess spawn + IPC overhead (~200µs) rather than pure validation time, which masked nbmcp's actual performance advantage.