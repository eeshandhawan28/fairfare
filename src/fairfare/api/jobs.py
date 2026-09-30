from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable


class JobStore:
    """Runs long pipelines in a thread pool; clients poll /jobs/{id}."""

    def __init__(self, workers: int = int(__import__('os').getenv('FAIRFARE_WORKERS', '2')), keep: int = 200) -> None:
        self.keep = keep
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.jobs: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def submit(self, fn: Callable[[], dict[str, Any]]) -> str:
        jid = uuid.uuid4().hex[:10]
        with self.lock:
            self.jobs[jid] = {"id": jid, "status": "running", "result": None, "error": None,
                              "started_at": time.time(), "elapsed_s": 0}
            while len(self.jobs) > self.keep:  # oldest first; dicts keep insertion order
                self.jobs.pop(next(iter(self.jobs)))

        def run() -> None:
            try:
                result = fn()
                with self.lock:
                    self.jobs[jid].update(status="done", result=result, elapsed_s=round(time.time() - self.jobs[jid]["started_at"]))
            except Exception as exc:  # surfaced to the client, full detail is in the trace
                with self.lock:
                    self.jobs[jid].update(status="error", error=f"{type(exc).__name__}: {exc}",
                                          elapsed_s=round(time.time() - self.jobs[jid]["started_at"]))

        self.pool.submit(run)
        return jid

    def get(self, jid: str) -> dict[str, Any] | None:
        with self.lock:
            if jid not in self.jobs:
                return None
            j = dict(self.jobs[jid])
            if j["status"] == "running":
                j["elapsed_s"] = round(time.time() - j["started_at"])
            return j
