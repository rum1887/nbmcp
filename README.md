# nbmcp

`nbmcp` is a fast MCP server framework that gives Rust ownership of transport,
routing, and schema validation while Python owns the tool bodies.

- Rust validates incoming tool arguments before Python executes the tool.
- Python defines tool behavior with plain functions and type hints.
- Supports `io`, `process`, and `cpu` concurrency modes.

```

## Why nbmcp

Most tool servers validate incoming JSON arguments in Python on every request.
`nbmcp` generates JSON schemas from Python function signatures at decoration time,
then hands those schemas to Rust for validation before Python executes the tool.

Benefits:

- invalid calls are rejected before Python runs
- validation overhead is lower
- the Python tool body only executes after validation succeeds
- blocking I/O in tools still releases the GIL normally

## Features

- Rust-side MCP JSON-RPC transport over stdio
- HTTP JSON-RPC transport with `/` and `/jsonrpc`
- SSE event stream on `/events`
- tool registration via `@mcp.tool(...)`
- resource registration via `mcp.resource(...)`
- prompt template registration via `mcp.prompt(...)`
- runtime discovery with `resources/list`, `prompts/list`, `resources/get`, `prompts/get`
- prompt rendering via `prompts/render`
- type-hint-driven tool input schema generation
- Rust validation of tool-call payloads
- `io`, `process`, and `cpu` concurrency modes
## Installation

Assuming `nbmcp` is published on PyPI, install the package with:

```bash
python3 -m pip install nbmcp
```

For local development, install the repository in editable mode:

```bash
python3 -m pip install -e .
```

Optional convenience install using `uv`:

```bash
python3 -m pip install uv
uv install .
```

If you want to install a development preview directly from GitHub:

```bash
python3 -m pip install git+https://github.com/<user>/nbmcp.git
```

## Quick start

```python
from nbmcp import Nbmcp

mcp = Nbmcp("weather")

@mcp.tool(description="Get current weather for a city")
def get_weather(city: str, units: str = "celsius") -> dict:
    return {"city": city, "temp": 24, "units": units}

if __name__ == "__main__":
    mcp.run()
```

Run the server:

```bash
python examples/weather_server.py
```

Run a client in another shell:

```bash
python examples/test_client.py

## Usage

### Standard stdio server

```python
from nbmcp import Nbmcp

mcp = Nbmcp("weather")

@mcp.tool(description="Get current weather for a city")
def get_weather(city: str, units: str = "celsius") -> dict:
    return {"city": city, "temp": 24, "units": units}

if __name__ == "__main__":
    mcp.run()
```

### HTTP server

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

### Resources and prompts

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
```

Clients can then discover runtime assets via:

- `initialize` returns `resources` and `prompts`
- `resources/list` and `prompts/list`
- `resources/get` and `prompts/get`
- `prompts/render`

## Concurrency modes

```python
@mcp.tool()
def get_weather(city: str) -> dict: ...

@mcp.tool(concurrency="process")
def count_primes(n: int) -> dict: ...

@mcp.tool(concurrency="cpu")
def analyze(data: list) -> dict: ...
```

- `io` — default mode; best for I/O-bound tools
- `process` — worker processes for CPU-bound work
- `cpu` — separate interpreter on Python 3.14+, falling back to `process`

## Development

Install the repository for local development:

```bash
python3 -m pip install -e .
```

Optional developer dependencies:

```bash
python3 -m pip install ruff pytest
```

Run tests and examples:

```bash
cargo test --lib
python examples/test_client.py
python examples/test_concurrency.py
```

## Publishing to PyPI

Build source and wheel distributions:

```bash
python3 -m pip install build twine
python3 -m build
```

Upload to PyPI:

```bash
python3 -m twine upload dist/*
```

If you do not want to publish immediately, install directly from GitHub:

```bash
python3 -m pip install git+https://github.com/<user>/nbmcp.git
```

## Packaging notes

`nbmcp` is configured to build as a native extension with `maturin`.
If users install from source, a Rust toolchain is required unless prebuilt
wheels are available for their platform.

## Project structure

- `src/` — Rust implementation and PyO3 bridge
- `python/nbmcp/` — Python public API and schema generation
- `examples/` — sample server and client scripts

## License

MIT
