# nbmcp vs. FastMCP vs. Official Python MCP SDK

A side-by-side comparison of three MCP server frameworks for Python.

| Feature | nbmcp | FastMCP | Official Python MCP SDK |
|---------|-------|---------|------------------------|
| **Validation location** | Rust (before Python) | Python (pydantic) | Python (pydantic) |
| **Concurrency model** | Per-tool: `io` (thread pool), `process` (ProcessPoolExecutor), `cpu` (subinterpreters 3.14+) | Async event loop (asyncio) | Async event loop (asyncio) |
| **Install friction** | Requires Rust toolchain unless prebuilt wheels are available | `pip install fastmcp` (pure Python) | `pip install mcp` (pure Python) |
| **Performance** | Rust-side validation is ~10-50x faster for malformed calls (rejected before Python); valid calls have comparable overhead | pydantic validation on every call, GIL-held | pydantic validation on every call, GIL-held |
| **Transport** | stdio (JSON-RPC), HTTP (JSON-RPC + SSE) | stdio, SSE, WebSocket | stdio, SSE |
| **Schema generation** | From Python type hints at decoration time, handed to Rust | From Python type hints via pydantic | From Python type hints via pydantic |
| **Resources** | Static resources registered at startup | Dynamic resources with functions | Static + dynamic resources |
| **Prompts** | Template-based prompts with `{variable}` substitution | Prompt functions | Prompt functions |
| **CLI tooling** | `nbmcp check` (lint tool definitions), `nbmcp lock` (lock file management) | None | `mcp` CLI for dev server |
| **Python version** | 3.9+ | 3.10+ | 3.10+ |
| **License** | MIT | MIT | MIT |

## Detailed comparison

### Validation location

**nbmcp** is unique in performing JSON Schema validation in Rust, before the Python interpreter is ever involved. This means:

- Malformed calls are rejected with zero GIL overhead.
- The Python tool body only executes after validation succeeds.
- Validation is a simple tree walk over pre-compiled schemas — no pydantic model instantiation.

**FastMCP** and the **Official SDK** both validate inside Python using pydantic models. This is correct and well-tested, but:

- Every call pays the cost of pydantic model construction.
- The GIL is held during validation.
- Malformed calls still consume Python interpreter cycles before being rejected.

### Concurrency model

**nbmcp** uses a per-tool concurrency model:

- `io` (default): The tool runs on a blocking thread in the Rust tokio runtime. The GIL is only held for the duration of the actual Python call; blocking I/O releases it naturally.
- `process`: The tool runs in a `ProcessPoolExecutor` worker. Good for CPU-bound work.
- `cpu` (Python 3.14+): The tool runs in a separate subinterpreter with its own GIL.

**FastMCP** and the **Official SDK** use asyncio throughout. Tools are async functions that run on the event loop. This works well for I/O-bound tools but requires the tool author to use async libraries (e.g., `httpx` instead of `requests`).

### Install friction

**nbmcp** is a native extension built with PyO3/maturin. Users need a Rust toolchain to install from source. Prebuilt wheels (planned for 0.1.0) will eliminate this requirement.

**FastMCP** and the **Official SDK** are pure Python — `pip install` works everywhere with no build step.

### Performance

nbmcp's Rust-side validation is measurably faster, especially for the error path:

- **Malformed calls**: nbmcp rejects in Rust (~1-5 µs) vs. pydantic validation in Python (~50-200 µs).
- **Valid calls**: The gap is smaller since both frameworks must eventually call the Python tool body. nbmcp's validation adds ~1-5 µs; pydantic adds ~20-100 µs.

See [BENCHMARKS.md](./BENCHMARKS.md) for detailed numbers.

## When to use which

- **Use nbmcp when**: You want the fastest possible validation, need per-tool concurrency control, or want to minimize GIL contention for I/O-bound tools.
- **Use FastMCP when**: You want a pure-Python dependency, need WebSocket transport, or prefer an async-native API.
- **Use Official SDK when**: You want the reference implementation, need full protocol compliance, or are building a client (not just a server).