"""Minimal example nbmcp server: run with `python examples/weather_server.py`
and speak MCP JSON-RPC to it over stdio."""

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


@mcp.tool(
    description="Count primes below n (CPU-bound, runs in a worker process)",
    concurrency="process",
)
def count_primes(n: int) -> dict:
    import os

    def is_prime(k):
        if k < 2:
            return False
        i = 2
        while i * i <= k:
            if k % i == 0:
                return False
            i += 1
        return True

    count = sum(1 for k in range(2, n) if is_prime(k))
    # Returning the worker PID makes it easy to prove in a test that this
    # really ran in a different process from the server's main process.
    return {"n": n, "count": count, "worker_pid": os.getpid()}


if __name__ == "__main__":
    mcp.run()
