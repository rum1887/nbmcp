"""Proves two CPU-bound tool calls (concurrency='process') actually overlap
in wall-clock time instead of the transport serializing them."""

import json
import os
import signal
import subprocess
import sys
import threading
import time

proc = subprocess.Popen(
    [sys.executable, "examples/weather_server.py"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, bufsize=1, start_new_session=True,
)

lock = threading.Lock()


def send(msg):
    with lock:
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()


def recv_one():
    line = proc.stdout.readline()
    return json.loads(line) if line else None


results = {}


def call(call_id, n):
    t0 = time.monotonic()
    send({"jsonrpc": "2.0", "id": call_id, "method": "tools/call",
          "params": {"name": "count_primes", "arguments": {"n": n}}})


try:
    send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    recv_one()
    send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    t_start = time.monotonic()
    # Fire two heavy calls back-to-back without waiting for the first
    # response, so both are genuinely in flight at once.
    call(10, 400000)
    call(11, 400000)

    r1 = recv_one()
    t1 = time.monotonic()
    r2 = recv_one()
    t2 = time.monotonic()

    print(f"first response after {t1 - t_start:.2f}s, second after {t2 - t_start:.2f}s")
    print("If these run sequentially, the second finishes roughly 2x the first.")
    print("If they overlap, both should finish around the same time.")
finally:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
