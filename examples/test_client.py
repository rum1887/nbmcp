"""Drives examples/weather_server.py through a real MCP JSON-RPC handshake
over stdio, as an actual client would, to prove the end-to-end path works:
Python decorator -> Rust schema registration -> stdio transport ->
Rust-side validation -> Python tool call -> response.
"""

import json
import subprocess
import sys

proc = subprocess.Popen(
    [sys.executable, "examples/weather_server.py"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)


def send(msg):
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()


def recv():
    line = proc.stdout.readline()
    return json.loads(line) if line else None


# 1. initialize
send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
print("initialize ->", recv())

# 2. notifications/initialized (no response expected)
send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

# 3. tools/list
send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
print("tools/list ->", json.dumps(recv(), indent=2))

# 4. tools/call: valid arguments
send({
    "jsonrpc": "2.0", "id": 3, "method": "tools/call",
    "params": {"name": "get_weather", "arguments": {"city": "Bengaluru"}},
})
print("tools/call (valid) ->", recv())

# 5. tools/call: missing required argument -> should be rejected in Rust
send({
    "jsonrpc": "2.0", "id": 4, "method": "tools/call",
    "params": {"name": "get_weather", "arguments": {}},
})
print("tools/call (missing required arg) ->", recv())

# 6. tools/call: wrong type -> should be rejected in Rust
send({
    "jsonrpc": "2.0", "id": 5, "method": "tools/call",
    "params": {"name": "add", "arguments": {"a": "not a number", "b": 2}},
})
print("tools/call (wrong type) ->", recv())

# 7. tools/call: valid add
send({
    "jsonrpc": "2.0", "id": 6, "method": "tools/call",
    "params": {"name": "add", "arguments": {"a": 2, "b": 3}},
})
print("tools/call (add) ->", recv())

# 8. unknown method
send({"jsonrpc": "2.0", "id": 7, "method": "not/a/real/method", "params": {}})
print("unknown method ->", recv())

proc.stdin.close()
proc.terminate()
stderr = proc.stderr.read()
if stderr.strip():
    print("\n--- server stderr ---\n" + stderr, file=sys.stderr)
