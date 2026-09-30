"""Local-first tracing. One JSONL file per run under traces/ (gitignored).

Every LLM call and every graph node is a span with inputs, outputs, latency and
errors. Human feedback is appended to the same file. The files are plain JSON so
they can later be exported to Langfuse or OpenTelemetry without changing callers.
"""
from __future__ import annotations

import contextvars
import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

CLIP = 2000


def trace_dir() -> Path:
    return Path(os.getenv("FAIRFARE_TRACE_DIR", "traces"))


def clip(value: Any, n: int = CLIP) -> Any:
    if isinstance(value, str) and len(value) > n:
        return value[:n] + f"...[+{len(value) - n} chars]"
    return value


class Tracer:
    def __init__(self, run_id: Optional[str] = None, directory: Optional[Path] = None, **meta: Any):
        self.run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.dir = Path(directory) if directory else trace_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{self.run_id}.jsonl"
        self._depth = 0
        if not self.path.exists():  # reopening an existing run must not add a second start
            self._write({"type": "run_start", **meta})

    def _write(self, record: dict[str, Any]) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(), "run_id": self.run_id, **record}
        with self.path.open("a") as fh:
            fh.write(json.dumps(record, default=str) + "\n")

    def event(self, name: str, **data: Any) -> None:
        self._write({"type": "event", "name": name, **{k: clip(v) for k, v in data.items()}})

    @contextmanager
    def span(self, name: str, kind: str = "step", **attrs: Any) -> Iterator[dict[str, Any]]:
        rec: dict[str, Any] = {k: clip(v) for k, v in attrs.items()}
        t0 = time.perf_counter()
        self._depth += 1
        try:
            yield rec
        except Exception as exc:  # recorded, then re-raised: tracing never hides failures
            rec["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._depth -= 1
            out = {k: clip(v) for k, v in rec.items()}
            self._write({
                "type": "span", "name": name, "kind": kind, "depth": self._depth,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1), **out,
            })

    def feedback(self, rating: str, note: str = "") -> None:
        self._write({"type": "feedback", "rating": rating, "note": note})

    def end(self, **summary: Any) -> None:
        self._write({"type": "run_end", **{k: clip(v) for k, v in summary.items()}})


_current: contextvars.ContextVar[Optional[Tracer]] = contextvars.ContextVar("fairfare_tracer", default=None)


def current() -> Optional[Tracer]:
    return _current.get()


@contextmanager
def use(tracer: Tracer) -> Iterator[Tracer]:
    token = _current.set(tracer)
    try:
        yield tracer
    finally:
        _current.reset(token)


@contextmanager
def span(name: str, kind: str = "step", **attrs: Any) -> Iterator[dict[str, Any]]:
    tracer = current()
    if tracer is None:
        yield {}
        return
    with tracer.span(name, kind, **attrs) as rec:
        yield rec


def event(name: str, **data: Any) -> None:
    tracer = current()
    if tracer is not None:
        tracer.event(name, **data)


def load(run: str, directory: Optional[Path] = None) -> list[dict[str, Any]]:
    d = Path(directory) if directory else trace_dir()
    path = Path(run) if run.endswith(".jsonl") else d / f"{run}.jsonl"
    if not path.exists():
        matches = sorted(d.glob(f"{run}*.jsonl"))
        if not matches:
            raise FileNotFoundError(f"no trace for '{run}' in {d}")
        path = matches[-1]
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def list_runs(directory: Optional[Path] = None) -> list[Path]:
    d = Path(directory) if directory else trace_dir()
    return sorted(d.glob("*.jsonl")) if d.exists() else []


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    spans = [e for e in events if e["type"] == "span"]
    llm = [s for s in spans if s.get("kind") == "llm"]
    return {
        "run_id": events[0]["run_id"] if events else None,
        "spans": len(spans),
        "llm_calls": len(llm),
        "llm_ms": round(sum(s["latency_ms"] for s in llm), 1),
        "total_ms": round(sum(s["latency_ms"] for s in spans if s.get("depth") == 0), 1),
        "errors": [s["name"] for s in spans if s.get("error")],
        "feedback": [e for e in events if e["type"] == "feedback"],
    }
