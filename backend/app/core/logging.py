import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

REDACTED_KEYS = {
    "authorization",
    "cookie",
    "database_url",
    "password",
    "secret",
    "token",
}


def _sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[redacted]"
            if any(part in str(key).lower() for part in REDACTED_KEYS)
            else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage().replace("\r", "\\r").replace("\n", "\\n"),
        }
        context = getattr(record, "context", None)
        if context:
            payload["context"] = _sanitize(context)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)
