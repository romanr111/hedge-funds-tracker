from __future__ import annotations

import contextlib
import contextvars
import json
import logging
from collections.abc import Iterator
from datetime import datetime, timezone
from uuid import uuid4


TRACE_ID = "trace_id"

_trace_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(TRACE_ID, default="")

_STANDARD_LOG_RECORD_FIELDS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        TRACE_ID,
    }
)


def new_trace_id() -> str:
    return uuid4().hex


@contextlib.contextmanager
def log_context(
    *,
    trace_id: str | None = None,
) -> Iterator[None]:
    trace_token = _trace_id_ctx.set(trace_id) if trace_id is not None else None
    try:
        yield
    finally:
        if trace_token is not None:
            _trace_id_ctx.reset(trace_token)


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_id_ctx.get("")
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            TRACE_ID: getattr(record, TRACE_ID, ""),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOG_RECORD_FIELDS or key.startswith("_"):
                continue
            payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _has_context_filter(handler: logging.Handler) -> bool:
    return any(isinstance(existing_filter, _ContextFilter) for existing_filter in handler.filters)


def _configure_handler(handler: logging.Handler, *, level: int) -> None:
    handler.setLevel(level)
    handler.setFormatter(JsonFormatter())
    if not _has_context_filter(handler):
        handler.addFilter(_ContextFilter())
    setattr(handler, "_tracker_json_logging", True)


def configure_logging(*, level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(level)

    if root.handlers:
        # Keep existing sinks but normalize them to a single structured format.
        for handler in root.handlers:
            _configure_handler(handler, level=level)
        return

    handler = logging.StreamHandler()
    _configure_handler(handler, level=level)
    root.addHandler(handler)
