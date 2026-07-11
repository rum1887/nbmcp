"""nbmcp: Rust transport + routing + schema validation, Python tool bodies.

    from nbmcp import Nbmcp

    mcp = Nbmcp("weather")

    @mcp.tool(description="Get current weather for a city")
    def get_weather(city: str) -> dict:
        return {"city": city, "temp_c": 21}

    if __name__ == "__main__":
        mcp.run()
"""

import json

from ._nbmcp_core import NativeEngine
from ._schema import generate_tool_definition

__all__ = ["Nbmcp"]
__version__ = "0.1.0"


class Nbmcp:
    """An MCP server. Protocol handling, routing, and argument validation
    happen in the Rust core; decorated functions are the tool bodies."""

    def __init__(self, name: str):
        self.name = name
        self._engine = NativeEngine(name)
        self.tools = {}

    def tool(self, description: str = ""):
        """Register a function as an MCP tool.

        The function's signature and type hints are used to generate the
        JSON schema the Rust core validates incoming calls against. Prefer
        explicit type hints (str, int, float, bool, list[T], dict) for an
        accurate schema.
        """

        def decorator(func):
            tool_def = generate_tool_definition(func, description)
            self._engine.register_tool(func.__name__, json.dumps(tool_def), func)
            self.tools[func.__name__] = func
            return func

        return decorator

    def run(self):
        """Start the MCP stdio server. Blocks until the client disconnects
        (stdin closes). Call this as the last line of your server script."""
        self._engine.run_stdio()
