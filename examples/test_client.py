"""Drives examples/weather_server.py through a real MCP JSON-RPC handshake
over stdio, as an actual client would, to prove the end-to-end path works:
Python decorator -> Rust schema registration -> stdio transport ->
Rust-side validation -> Python tool call -> response.
"""

import json
import os
import signal
import subprocess
import sys

# start_new_session=True puts the server (and any worker processes it later
# spawns for concurrency="process"/"cpu" tools) in their own process group,
# so we can reliably tear down the whole tree afterwards -- otherwise a
# spawned worker can outlive the server and keep its inherited stdio pipes
# open, hanging anything that tries to read them to EOF.
proc = subprocess.Popen(
    [sys.executable, "examples/weather_server.py"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
    start_new_session=True,
)


def send(msg):
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()


def recv():
    line = proc.stdout.readline()
    return json.loads(line) if line else None


try:
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

    # 9. tools/call: CPU-bound tool with concurrency="process"
    send({
        "jsonrpc": "2.0", "id": 8, "method": "tools/call",
        "params": {"name": "count_primes", "arguments": {"n": 20000}},
    })
    result = recv()
    print("tools/call (count_primes, process mode) ->", result)
    worker_pid = json.loads(result["result"]["content"][0]["text"])["worker_pid"]
    same_or_different = "DIFFERENT process, as expected" if worker_pid != proc.pid else "SAME process -- unexpected!"
    print(f"server pid = {proc.pid}, tool ran in worker pid = {worker_pid} ({same_or_different})")

finally:
    # Kill the whole process group (server + any worker processes it spawned),
    # not just the server itself.
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
