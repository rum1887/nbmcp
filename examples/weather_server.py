"""Minimal example nbmcp server: run with `python examples/weather_server.py`
and speak MCP JSON-RPC to it over stdio."""

from nbmcp import Nbmcp

mcp = Nbmcp("weather")


@mcp.tool(description="Get the current weather for a city")
def get_weather(city: str, units: str = "celsius") -> dict:
    # Stand-in for a real API call. Because this runs on a blocking-thread
    # task in the Rust core, a real `requests.get(...)` here would release
    # the GIL for the network wait the same way it does in plain Python.
    fake_temps = {"celsius": 24, "fahrenheit": 75}
    return {"city": city, "temp": fake_temps.get(units, 24), "units": units}


@mcp.tool(description="Add two numbers")
def add(a: int, b: int) -> int:
    return a + b


if __name__ == "__main__":
    mcp.run()
