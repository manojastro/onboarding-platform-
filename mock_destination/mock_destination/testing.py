"""Run the mock destination on a real socket in a background thread.

``TestClient`` cannot exercise client-side timeouts, so any test that needs a
genuine "the write landed but the reply never arrived" case uses this.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn
from fastapi import FastAPI


@contextmanager
def serve(app: FastAPI, host: str = "127.0.0.1", startup_timeout_s: float = 10.0) -> Iterator[str]:
    """Serve ``app`` on an ephemeral port and yield its base URL."""
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=0, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, name="mock-destination", daemon=True)
    thread.start()
    deadline = time.monotonic() + startup_timeout_s
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("mock destination failed to start")
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=startup_timeout_s)
