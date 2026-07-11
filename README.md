# nbmcp

An MCP server framework: Rust owns the transport, routing, and schema
validation; Python owns the tool bodies.

```python
from nbmcp import Nbmcp

mcp = Nbmcp("weather")

@mcp.tool(description="Get current weather for a city")
def get_weather(city: str, units: str = "celsius") -> dict:
    return {"city": city, "temp": 24, "units": units}

if __name__ == "__main__":
    mcp.run()
```

## Why

FastMCP-style frameworks validate every tool call's JSON arguments in pure
Python (Pydantic) on the request path. nbmcp generates a JSON schema once,
at decoration time, from your function's type hints, and validates every
incoming call against it **in Rust, before your Python code ever runs.**
Malformed calls never touch the interpreter.

Tool bodies still run in Python — nbmcp only holds the GIL for the
duration of the actual call, so a tool doing blocking I/O (an HTTP request,
a DB query, a subprocess) releases the GIL the same way it would in plain
Python, and multiple in-flight calls can genuinely overlap instead of the
transport layer serializing them.

## Architecture (v0.1)

```
stdin ─▶ Rust: read line ─▶ parse JSON-RPC ─▶ validate args against schema
                                                       │
                                          (invalid) ───┤
                                                       │ (valid)
                                                       ▼
                                          spawn_blocking task: acquire GIL,
                                          call Python function, release GIL
                                                       │
stdout ◀── Rust: write JSON-RPC response ◀─────────────┘
```

- `src/schema.rs` — small hand-rolled JSON Schema validator (type, required,
  properties, array items). Covers everything the Python-side schema
  generator emits. Deliberately not a full-spec validator, to avoid an
  extra dependency and its MSRV baggage for v0.1 — swappable later.
- `src/protocol.rs` — MCP JSON-RPC-over-stdio transport (`initialize`,
  `tools/list`, `tools/call`), talked directly rather than via the `rmcp`
  crate, to keep the dependency surface and build small for v0.1. `rmcp`
  is a reasonable thing to adopt later for resources/prompts/sampling
  support — the protocol handling is isolated in this one file.
- `src/convert.rs` — JSON <-> Python object conversion.
- `src/lib.rs` — `NativeEngine`: the PyO3-exposed tool registry and the
  entry point Python calls into (`run_stdio`).
- `python/nbmcp/_schema.py` — generates a JSON schema from a function's
  type hints (str, int, float, bool, list[T], dict, Optional[T]).
- `python/nbmcp/__init__.py` — the public `Nbmcp` class and `@tool()`
  decorator.

## What's *not* here yet (known scope for v0.2+)

- Resources and prompts (tools only, for now)
- HTTP/SSE transport (stdio only)
- Per-tool concurrency modes (`io` / `cpu` / `process`) — everything is a
  blocking-thread task today; subinterpreter-based true parallelism for
  CPU-bound tools is future work
- `nbmcp check` (the schema linter) and `nbmcp.lock` — separate, planned
  as standalone pieces of the ecosystem

## Building

```bash
pip install maturin
maturin develop --release   # builds the Rust extension, installs nbmcp into your venv
python examples/weather_server.py
```

## Testing

```bash
cargo test --lib                  # Rust schema validator unit tests
python examples/test_client.py    # end-to-end: real JSON-RPC handshake against the example server
```
