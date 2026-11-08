"""JSON logs, one object per line, with the audit record under `audit`."""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import TextIO


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("audit", "reason"):
            if hasattr(record, key):
                out[key] = getattr(record, key)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def configure(level: str = "INFO", stream: TextIO = sys.stderr) -> None:
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger("mcpkit")
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False
