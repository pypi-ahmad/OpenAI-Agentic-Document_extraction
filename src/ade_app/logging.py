"""Application logging without document content or credentials.

Must not log extracted field values, raw page text, or API keys/secrets — only the
known structured fields in _FIELDS. Configures the single process root logger; there
is no log file, everything goes to stdout/stderr. See cli.py for the caller that
invokes configure_logging before running the pipeline.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from ade_app.config import LoggingSettings

# Allowlist of extra LogRecord attributes the JSON formatter will emit. Anything not
# named here (e.g. document text, field values, credentials) is never included, even
# if a caller attaches it via `extra=`.
_FIELDS = (
    "event",
    "job_id",
    "stage",
    "source_page",
    "model",
    "attempt",
    "device",
    "status",
    "elapsed_ms",
    "error_code",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update({name: getattr(record, name) for name in _FIELDS if hasattr(record, name)})
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(settings: LoggingSettings) -> None:
    """Configure the process root logger; libraries otherwise stay silent."""

    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter()
        if settings.format == "json"
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]  # replace, not append: avoids duplicate lines on reconfigure
    root.setLevel(settings.level)
