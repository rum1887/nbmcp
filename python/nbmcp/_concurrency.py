"""Per-tool concurrency modes.

nbmcp's Rust core holds the GIL only for the duration of a tool call
(see src/lib.rs: call_python_tool). That's sufficient for I/O-bound tools:
a real `requests.get(...)` or DB query releases the GIL for the wait the
same way it does in plain Python, so multiple in-flight calls overlap.

It is NOT sufficient for CPU-bound tools (image processing, heavy parsing,
crypto, etc.) — those hold the GIL for the entire computation and calls
still serialize. This module gives tool authors two ways out of that:

  - concurrency="process": run the tool body in a worker process
    (concurrent.futures.ProcessPoolExecutor). Real, tested, works today.
    Requirement: the function and its arguments/return value must be
    picklable (define tools at module level, not as closures/lambdas).

  - concurrency="cpu": run the tool body in a genuinely separate
    interpreter (PEP 684 per-interpreter GIL) via the `concurrent.interpreters`
    stdlib module. This module only exists on Python 3.14+. On earlier
    versions there is no safe public API for this: the private
    `_xxsubinterpreters` module available on 3.12/3.13 has no channel/queue
    mechanism to pass results back, and C extensions -- including nbmcp's
    own PyO3-built core -- are not guaranteed subinterpreter-safe. Rather
    than hack around that with a fragile private API, "cpu" mode falls
    back to the process pool (with a one-time warning) on Python < 3.14,
    and uses real subinterpreters when the stdlib support is present.
"""

from __future__ import annotations

import atexit
import functools
import multiprocessing
import signal
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor

_process_pool: ProcessPoolExecutor | None = None
_warned_no_subinterpreters = False


def _shutdown_process_pool():
    # This runs on normal interpreter exit and also on SIGTERM, where Python's
    # default disposition would otherwise terminate the process without
    # unwinding atexit hooks. A process killed by an external supervisor can
    # leave workers as orphans holding inherited file descriptors (notably
    # stdout/stderr) open, which may hang a managed server shutdown.
    global _process_pool
    if _process_pool is not None:
        _process_pool.shutdown(wait=False, cancel_futures=True)
        _process_pool = None


@atexit.register
def _shutdown_process_pool_atexit():
    _shutdown_process_pool()


def _handle_sigterm(signum, frame):
    _shutdown_process_pool()
    # Re-raise the signal using the default handler so the process exits with
    # the expected status and any supervising runtime sees the normal SIGTERM.
    signal.signal(signum, signal.SIG_DFL)
    signal.raise_signal(signum)


signal.signal(signal.SIGTERM, _handle_sigterm)


def _get_process_pool() -> ProcessPoolExecutor:
    global _process_pool
    if _process_pool is None:
        # IMPORTANT: the default "fork" start method is unsafe here. By the
        # time a tool call happens, the Rust core's tokio runtime has already
        # spawned several OS threads; fork()-ing a multi-threaded process only
        # copies the calling thread, so locks held by the other threads (GIL,
        # malloc arena, etc.) can stay permanently locked in the child and
        # deadlock it. "spawn" starts each worker as a clean fresh process,
        # which is slower to start but doesn't have this failure mode.
        _process_pool = ProcessPoolExecutor(mp_context=multiprocessing.get_context("spawn"))
    return _process_pool


def _subinterpreters_available() -> bool:
    # concurrent.interpreters lands in the stdlib in Python 3.14 (PEP 734).
    return sys.version_info >= (3, 14)


def wrap(func, concurrency: str):
    """Return a callable with the same call signature as `func`, dispatched
    according to `concurrency`. This is what gets registered with the Rust
    core -- the core doesn't know or care which mode a tool uses."""

    if concurrency == "io":
        return func

    if concurrency == "process":
        return _wrap_process(func)

    if concurrency == "cpu":
        if _subinterpreters_available():
            return _wrap_subinterpreter(func)
        _warn_no_subinterpreters(func)
        return _wrap_process(func)

    raise ValueError(
        f"nbmcp: unknown concurrency mode {concurrency!r} for tool "
        f"'{func.__name__}'. Expected 'io', 'process', or 'cpu'."
    )


def _wrap_process(func):
    @functools.wraps(func)
    def wrapper(**kwargs):
        pool = _get_process_pool()
        future = pool.submit(func, **kwargs)
        # .result() blocks with the GIL released while waiting, so other
        # in-flight tool calls on other threads still make progress.
        return future.result()

    return wrapper


def _wrap_subinterpreter(func):
    # Only imported/used on Python 3.14+, where this is a public, stable API.
    import concurrent.interpreters as interpreters  # type: ignore[import-not-found]

    @functools.wraps(func)
    def wrapper(**kwargs):
        interp = interpreters.create()
        try:
            result_queue = interpreters.create_queue()
            interp.prepare_main(func=func, kwargs=kwargs, result_queue=result_queue)
            interp.exec(
                "result_queue.put(func(**kwargs))"
            )
            return result_queue.get()
        finally:
            interp.close()

    return wrapper


def _warn_no_subinterpreters(func):
    global _warned_no_subinterpreters
    if not _warned_no_subinterpreters:
        warnings.warn(
            "nbmcp: concurrency='cpu' requires Python 3.14+ "
            "(concurrent.interpreters). Falling back to concurrency='process' "
            f"for tool '{func.__name__}' on this Python "
            f"({sys.version_info.major}.{sys.version_info.minor}). "
            "Set concurrency='process' explicitly to silence this warning.",
            stacklevel=3,
        )
        _warned_no_subinterpreters = True
