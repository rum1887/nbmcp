# nbmcp Roadmap

## Planned Features

The following features are tracked as planned but not yet implemented or tested in the current codebase. Items here are aspirational — contributions welcome.

### CLI commands — not documented in README but partially implemented
- [ ] **`nbmcp check`** — exists in `_check.py`, lints tool definitions with AST inspection, but has no test coverage and is undocumented
- [ ] **`nbmcp lock generate/verify`** — exists in `_lock.py` and `_check.py`, generates/verifies SHA-256 lock files of project sources, but has no test coverage and is undocumented

### Concurrency modes — partial implementation
- [ ] **`cpu` concurrency mode (subinterpreters)** — only works on Python 3.14+; falls back to `process` on earlier versions. Requires Python 3.14+ with `concurrent.interpreters` for a genuine subinterpreter path. Code exists but is untested (no test environment with Python 3.14).

### Transport layer
- [ ] **HTTP/S** — `run_http()` exists and is partially tested in the Rust protocol layer, but has no end-to-end integration test
- [ ] **SSE `/events` stream** — server-side implementation exists in `protocol.rs`, but no end-to-end integration test
- [ ] **TLS support** — not yet implemented for HTTP transport
- [ ] **WebSocket transport** — not yet implemented

### Tool enhancements
- [ ] **Streaming tool results** — return partial results before completion
- [ ] **AuthN/AuthZ hooks** — per-tool authorization policies (inspired by nbmcp's trust-distribution thesis)
- [ ] **Plugin system** — custom validators injected into the Rust validation pipeline
- [ ] **Middleware / lifecycle hooks** — before/after hooks on tool calls

### Developer experience
- [ ] **Prebuilt wheels for Windows** — currently only macOS and Linux wheels are planned; Windows build CI needs to be added
- [ ] **`nbmcp init` scaffolding command** — generate a new MCP server project skeleton

### Testing gaps
- [ ] Add `ruff` / `pytest` to CI
- [ ] End-to-end test for HTTP mode with real HTTP requests
- [ ] End-to-end test for SSE event stream
- [ ] Unit tests for `_concurrency.py` (process mode / subinterpreter fallback)
- [ ] Unit tests for `_check.py` (CLI linting)
- [ ] Unit tests for `_lock.py` (lock generation / verification)
- [ ] Unit tests for `_schema.py` (schema generation from type hints)