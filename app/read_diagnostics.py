"""Bounded local diagnostics. Never serialize exceptions, source lines or locals."""
import json
import re
import time
import traceback
from pathlib import Path


IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,79}\Z")


def safe_identifier(value):
    return value if isinstance(value, str) and IDENTIFIER.fullmatch(value) else "unknown"


class ReadDiagnostics:
    def __init__(self, path=None, notify=None):
        self.path = Path(path) if path else None
        self.notify = notify
        self.started = time.monotonic()
        self.phase = "initialize"
        self.events = []
        self.write_failed = False

    def event(self, phase, **counts):
        self.phase = safe_identifier(phase)
        event = {"phase": self.phase, "elapsed_ms": round((time.monotonic() - self.started) * 1000)}
        for name in ("rows", "items", "focused", "checkboxes", "lists", "parsed", "unknown", "skipped"):
            if type(counts.get(name)) is int and counts[name] >= 0:
                event[name] = counts[name]
        self.events.append(event)
        self.events = self.events[-100:]
        self.save()
        if self.notify:
            self.notify({"event": "progress", **event})

    def failure(self, exc):
        frames = []
        for frame in traceback.extract_tb(exc.__traceback__)[-12:]:
            # Explicitly omit frame.line and all frame locals.
            stem = safe_identifier(Path(frame.filename).stem)
            frames.append({"module": stem, "function": safe_identifier(frame.name), "line": frame.lineno})
        error = {"type": safe_identifier(type(exc).__name__), "phase": self.phase, "frames": frames}
        self.save(error)
        return error

    def save(self, error=None):
        if self.path:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                payload = {"schema": 1, "events": self.events}
                if error:
                    payload["failure"] = error
                temp = self.path.with_suffix(".tmp")
                temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                temp.replace(self.path)
            except OSError:
                # Diagnostics must not turn a usable read into a failed scan.
                self.write_failed = True
