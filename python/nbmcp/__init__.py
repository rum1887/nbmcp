"""nbmcp: Rust transport + routing + schema validation, Python tool bodies.

    from nbmcp import Nbmcp

    mcp = Nbmcp("weather")

    @mcp.tool(description="Get current weather for a city")
    def get_weather(city: str) -> dict:
        return {"city": city, "temp_c": 21}

    if __name__ == "__main__":
        mcp.run()
"""

import importlib
import json
import warnings

from . import _concurrency
from ._schema import generate_tool_definition


def _load_native_engine():
    try:
        module = importlib.import_module("nbmcp._nbmcp_core")
        return module.NativeEngine
    except ImportError:
        warnings.warn(
            "nbmcp: native extension _nbmcp_core is not available; "
            "falling back to the pure-Python engine. "
            "This is supported on PyPy and non-extension installs.",
            ImportWarning,
            stacklevel=2,
        )
        from ._native_engine import NativeEngine

        return NativeEngine

__all__ = ["Nbmcp"]
__version__ = "0.1.0"

_VALID_CONCURRENCY = ("io", "process", "cpu")


class Nbmcp:
    """An MCP server. Protocol handling, routing, and argument validation
    happen in the Rust core; decorated functions are the tool bodies."""

    def __init__(self, name: str):
        self.name = name
        self._engine = _load_native_engine()(name)
        self.tools = {}

    def tool(self, description: str = "", concurrency: str = "io"):
        """Register a function as an MCP tool.

        The function's signature and type hints are used to generate the
        JSON schema the Rust core validates incoming calls against. Prefer
        explicit type hints (str, int, float, bool, list[T], dict) for an
        accurate schema.

        `concurrency` controls how the tool body executes once a call has
        passed validation:
          - "io" (default): call directly. Right for tools that spend their
            time waiting on network/disk/subprocess I/O -- the GIL is only
            held for the duration of the call, and a real I/O wait releases
            it the same way it would in plain Python.
          - "process": run in a worker process (ProcessPoolExecutor). Right
            for CPU-heavy tools (parsing, image processing, crypto). The
            function and its arguments/return value must be picklable, so
            define the tool at module level, not as a closure or lambda.
          - "cpu": like "process", but uses a genuinely separate Python
            interpreter (PEP 684) instead of a full OS process, when the
            stdlib supports it (Python 3.14+). Falls back to "process"
            with a one-time warning on earlier Python versions.
        """
        if concurrency not in _VALID_CONCURRENCY:
            raise ValueError(
                f"nbmcp: concurrency must be one of {_VALID_CONCURRENCY}, "
                f"got {concurrency!r}"
            )

        def decorator(func):
            tool_def = generate_tool_definition(func, description)
            dispatched = _concurrency.wrap(func, concurrency)
            self._engine.register_tool(func.__name__, json.dumps(tool_def), dispatched)
            self.tools[func.__name__] = func
            return func

        return decorator

    def resource(self, name: str, content: str, description: str = ""):
        """Register a reusable resource with the MCP runtime."""
        resource_def = {
            "name": name,
            "description": description,
            "content": content,
        }
        self._engine.register_resource(json.dumps(resource_def))
        return resource_def

    def prompt(self, name: str, template: str, description: str = ""):
        """Register a prompt template for future tool or agent workflows."""
        prompt_def = {
            "name": name,
            "description": description,
            "template": template,
        }
        self._engine.register_prompt(json.dumps(prompt_def))
        return prompt_def

    def run(self):
        """Start the MCP stdio server. Blocks until the client disconnects
        (stdin closes). Call this as the last line of your server script."""
        self._engine.run_stdio()

    def run_http(self, address: str = "127.0.0.1:8080"):
        """Start the MCP HTTP server on the given address.

        The server accepts JSON-RPC requests over HTTP POST on `/` or
        `/jsonrpc`, and exposes a simple SSE event stream on `/events`.
        """
        self._engine.run_http(address)
