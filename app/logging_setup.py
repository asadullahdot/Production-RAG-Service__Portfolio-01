"""JSON logging with request ids and secret redaction (PLAN.md §11)."""
from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import uuid

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_SECRET_RE = re.compile(
    r"((?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{8,}|(?<![A-Za-z0-9])pcsk_[A-Za-z0-9_\-]{8,}|eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"
    r"|(?i:bearer)\s+[A-Za-z0-9._\-]+|(?i:authorization)['\"]?\s*[:=]\s*['\"]?[^\s,'\"]+)"
)


def redact(text: str) -> str:
    return _SECRET_RE.sub("[REDACTED]", text)


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


class RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_var.get(),
            "msg": redact(record.getMessage()),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update({k: redact(v) if isinstance(v, str) else v for k, v in extra.items()})
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))  # server-side only
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if any(getattr(h, "_rag_handler", False) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactingJsonFormatter())
    handler._rag_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def log_event(logger: logging.Logger, msg: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, msg, extra={"extra_fields": fields})
