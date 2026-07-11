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
- HTTP JSON-RPC transport with `/` and `/jsonrpc` endpoints
- Simple SSE event stream on `/events`
- Tool registration via `@mcp.tool(...)`
- Resource registration via `mcp.resource(...)`
- Prompt template registration via `mcp.prompt(...)`
- Runtime exposure through `resources/list` and `prompts/list`
- Type-hint driven schema generation
- Rust validation of tool-call payloads
- `io`, `process`, and `cpu` concurrency modes
- Minimal v0.1 dependency surface

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

## Resource and prompt example

```python
from nbmcp import Nbmcp

mcp = Nbmcp("weather")

mcp.resource(
    name="city_help",
    content="Use the canonical city name and ISO country code when making requests.",
    description="Shared documentation for tool callers",
)

mcp.prompt(
    name="weather_summary",
    template="City: {city}\nUnits: {units}\nProvide a concise weather summary.",
    description="Prompt template placeholder for future agent workflows",
)

@mcp.tool(description="Get current weather for a city")
def get_weather(city: str, units: str = "celsius") -> dict:
    return {"city": city, "temp": 24, "units": units}

if __name__ == "__main__":
    mcp.run_http("127.0.0.1:8080")
```

This means:

- invalid calls are rejected before Python ever runs
- validation overhead is lower
- Python only executes the tool body after validation succeeds
- blocking I/O in tools still releases the GIL normally

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

Pending:

- Production-ready SSE events beyond the connection handshake

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
