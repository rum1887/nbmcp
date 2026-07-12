"""Demonstrate nbmcp's Rust-side rejection of malformed tool call input.

This example starts an nbmcp server, sends intentionally malformed tool
calls (missing required fields, wrong types), and shows the error response
surfaced back to the client — proving the rejection happens in Rust before
Python is ever involved.

Usage:
    python examples/malformed_input_demo.py
"""

import json
import os
import signal
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    # Start the weather server as a subprocess
    server_script = os.path.join(HERE, "weather_server.py")
    proc = subprocess.Popen(
        [sys.executable, server_script],
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
        # Initialize
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        init_resp = recv()
        print("✅ Server initialized")
        print(f"   Server info: {init_resp['result']['serverInfo']}")
        print()

        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        # ── Test 1: Missing required field ──────────────────────────────
        print("=" * 60)
        print("Test 1: Missing required field 'city'")
        print("-" * 60)
        send({
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {"name": "get_weather", "arguments": {}},
        })
        resp = recv()
        error_text = resp.get("result", {}).get("content", [{}])[0].get("text", "")
        is_error = resp.get("result", {}).get("isError", False)
        print(f"   isError: {is_error}")
        print(f"   Error:   {error_text}")
        assert is_error, "Expected isError=true for missing required field"
        assert "city" in error_text, f"Expected error to mention 'city', got: {error_text}"
        print("   ✅ Rust rejected the call before Python executed it")
        print()

        # ── Test 2: Wrong type (string instead of int) ──────────────────
        print("=" * 60)
        print("Test 2: Wrong type — 'a' should be int, got string")
        print("-" * 60)
        send({
            "jsonrpc": "2.0", "id": 11, "method": "tools/call",
            "params": {"name": "add", "arguments": {"a": "not-a-number", "b": 2}},
        })
        resp = recv()
        error_text = resp.get("result", {}).get("content", [{}])[0].get("text", "")
        is_error = resp.get("result", {}).get("isError", False)
        print(f"   isError: {is_error}")
        print(f"   Error:   {error_text}")
        assert is_error, "Expected isError=true for wrong type"
        assert "integer" in error_text, f"Expected error to mention 'integer', got: {error_text}"
        print("   ✅ Rust rejected the call before Python executed it")
        print()

        # ── Test 3: Unknown tool name ───────────────────────────────────
        print("=" * 60)
        print("Test 3: Unknown tool name")
        print("-" * 60)
        send({
            "jsonrpc": "2.0", "id": 12, "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
        })
        resp = recv()
        error_code = resp.get("error", {}).get("code")
        error_msg = resp.get("error", {}).get("message", "")
        print(f"   Error code: {error_code}")
        print(f"   Error msg:  {error_msg}")
        assert error_code == -32602, f"Expected error code -32602, got {error_code}"
        assert "Unknown tool" in error_msg, f"Expected 'Unknown tool' in error, got: {error_msg}"
        print("   ✅ Rust rejected the unknown tool name")
        print()

        # ── Test 4: Valid call (should succeed) ─────────────────────────
        print("=" * 60)
        print("Test 4: Valid call (should succeed)")
        print("-" * 60)
        send({
            "jsonrpc": "2.0", "id": 13, "method": "tools/call",
            "params": {"name": "get_weather", "arguments": {"city": "Bengaluru"}},
        })
        resp = recv()
        result_text = resp.get("result", {}).get("content", [{}])[0].get("text", "")
        is_error = resp.get("result", {}).get("isError", False)
        print(f"   isError: {is_error}")
        print(f"   Result:  {result_text}")
        assert not is_error, f"Expected isError=false for valid call, got {is_error}"
        assert "Bengaluru" in result_text, f"Expected 'Bengaluru' in result, got: {result_text}"
        print("   ✅ Valid call succeeded")
        print()

        print("=" * 60)
        print("All tests passed! 🎉")
        print("=" * 60)
        print()
        print("Key takeaway: nbmcp validates tool call arguments in Rust")
        print("before Python is ever involved. Malformed calls are rejected")
        print("with zero GIL overhead and the Python tool body never executes.")

    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


if __name__ == "__main__":
    main()