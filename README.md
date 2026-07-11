# nbmcp

`nbmcp` is a lightweight MCP server framework that lets Rust own transport,
routing, and schema validation while Python owns the tool bodies.

- Rust validates incoming tool arguments before Python executes the tool.
- Python defines tool behavior with plain functions and type hints.
- Supports `io`, `process`, and `cpu` concurrency modes.

## Quick start

```bash
cd /Users/ramya/projects/nbmcp
pip install maturin
python -m maturin develop --release
python examples/weather_server.py
```

In another shell:

```bash
python examples/test_client.py
```

## Example

```python
from nbmcp import Nbmcp

mcp = Nbmcp("weather")

@mcp.tool(description="Get current weather for a city")
def get_weather(city: str, units: str = "celsius") -> dict:
    return {"city": city, "temp": 24, "units": units}

if __name__ == "__main__":
    mcp.run()
```

## HTTP transport example

```python
from nbmcp import Nbmcp

mcp = Nbmcp("weather")

@mcp.tool(description="Get current weather for a city")
def get_weather(city: str, units: str = "celsius") -> dict:
    return {"city": city, "temp": 24, "units": units}

if __name__ == "__main__":
    mcp.run_http("127.0.0.1:8080")
```

The HTTP server accepts JSON-RPC POST requests on `/` or `/jsonrpc` and
exposes a simple SSE stream on `/events`.

## Why nbmcp

Most tool servers validate incoming JSON arguments in Python on every request.
`nbmcp` instead generates a JSON schema once at decoration time from Python
function signatures, then hands that schema to the Rust core.

This means:

- invalid calls are rejected before Python ever runs
- validation overhead is lower
- Python only executes the tool body after validation succeeds
- blocking I/O in tools still releases the GIL normally

## Features

- Rust-side MCP JSON-RPC transport over stdio
- Tool registration via `@mcp.tool(...)`
- Type-hint driven schema generation
- Rust validation of tool-call payloads
- `io`, `process`, and `cpu` concurrency modes
- Minimal v0.1 dependency surface

## Architecture

```text
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

### Core components

- `src/schema.rs` — lightweight JSON schema validator for generated schemas
- `src/protocol.rs` — MCP JSON-RPC-over-stdio transport
- `src/convert.rs` — JSON ↔ Python conversion
- `src/lib.rs` — PyO3 `NativeEngine` bridge
- `python/nbmcp/_schema.py` — generates tool input schemas from type hints
- `python/nbmcp/__init__.py` — public `Nbmcp` API and decorator

## Concurrency modes

```python
@mcp.tool()
def get_weather(city: str) -> dict: ...

@mcp.tool(concurrency="process")
def count_primes(n: int) -> dict: ...

@mcp.tool(concurrency="cpu")
def analyze(data: list) -> dict: ...
```

- `io` — default mode. Best for I/O-bound tools.
- `process` — run tool bodies in worker processes. Best for CPU-bound work.
- `cpu` — use a separate interpreter on Python 3.14+; falls back to `process`
on older Python versions.

## Roadmap

Planned v0.2+ work:

- Resources and prompts
- HTTP/SSE transport
- `nbmcp check` schema linter
- `nbmcp.lock`

## Build

```bash
pip install maturin
python -m maturin develop --release
```

## Test

```bash
cargo test --lib
python examples/test_client.py
python examples/test_concurrency.py
```

## License

MIT
