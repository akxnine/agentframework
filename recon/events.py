"""Progress events: the pipeline emits them, the GUI (dashboard_app.py) reads them. The CLI is unaffected.

Events go to a JSONL file per run (output/runs/<run_id>.jsonl), so any process can tail them and past runs can be
reopened. Emitting is a no-op when no run is active.
"""
import json
import logging
import re
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_sink: Path | None = None
_MARKUP = re.compile(r"\[/?[a-z][^\]]*\]|\[/\]", re.I)


def start_run(runs_dir: str, run_id: str) -> Path:
    global _sink
    Path(runs_dir).mkdir(parents=True, exist_ok=True)
    with _lock:
        _sink = Path(runs_dir) / f"{run_id}.jsonl"
        _sink.write_text("")
    return _sink


def stop_run() -> None:
    global _sink
    with _lock:
        _sink = None


def emit(kind: str, **data) -> None:
    with _lock:
        if _sink is None:
            return
        with _sink.open("a") as f:
            f.write(json.dumps({"ts": time.time(), "kind": kind, **data}, default=str) + "\n")


def read_events(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass  # partially written last line
    return out


class EventLogHandler(logging.Handler):
    """Forwards log records (rich markup stripped) to the event stream as 'log' events."""

    def emit(self, record: logging.LogRecord) -> None:
        emit("log", level=record.levelname, msg=_MARKUP.sub("", record.getMessage()).replace("\\[", "["))
