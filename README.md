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

## Concurrency modes

```python
@mcp.tool()                              # default: concurrency="io"
def get_weather(city: str) -> dict: ...

@mcp.tool(concurrency="process")         # CPU-bound: runs in a worker process
def count_primes(n: int) -> dict: ...

@mcp.tool(concurrency="cpu")             # like "process", but a real separate
def analyze(data: list) -> dict: ...     # interpreter (PEP 684) on Python 3.14+
```

- **`io`** (default) — call directly. The Rust core only holds the GIL for
  the duration of the call; a real I/O wait (`requests.get`, a DB query,
  a subprocess) releases the GIL the same way it does in plain Python, so
  multiple in-flight calls overlap.
- **`process`** — dispatched to a `ProcessPoolExecutor` (`multiprocessing`,
  `spawn` start method — see the comment in `_concurrency.py` for why
  *not* `fork`, it's not a stylistic choice). The function and its
  arguments/return value must be picklable, so define tools at module
  level, not as closures or lambdas. Verified end-to-end in
  `examples/test_concurrency.py`: two heavy `count_primes` calls fired
  back-to-back finish within ~10ms of each other instead of one taking
  roughly 2x as long as the other.
- **`cpu`** — intended to use a genuinely separate interpreter (PEP 684,
  per-interpreter GIL) via the stdlib `concurrent.interpreters` module.
  That module only exists on **Python 3.14+**. On earlier versions there
  is no safe public API for this yet: the private `_xxsubinterpreters`
  module available on 3.12/3.13 has no channel/queue mechanism to pass
  results back safely, and C extensions — including nbmcp's own PyO3
  core — aren't guaranteed subinterpreter-safe. So on < 3.14, `cpu` falls
  back to `process` with a one-time `warnings.warn`, rather than silently
  pretending to give you subinterpreter isolation it can't deliver. This
  is the honest state of subinterpreters in the Python ecosystem today,
  not an nbmcp limitation specifically.

## What's *not* here yet (known scope for v0.2+)

- Resources and prompts (tools only, for now)
- HTTP/SSE transport (stdio only)
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
cargo test --lib                     # Rust schema validator unit tests
python examples/test_client.py       # end-to-end: real JSON-RPC handshake against the example server
python examples/test_concurrency.py  # proves concurrency="process" calls genuinely overlap
```
