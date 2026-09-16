import json
import logging
import sys
from datetime import datetime, timezone

_SKIP_KEYS = {
    "args", "msg", "exc_info", "exc_text", "stack_info", "levelname", "levelno",
    "name", "pathname", "filename", "module", "lineno", "funcName", "created",
    "msecs", "relativeCreated", "thread", "threadName", "processName", "process",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _SKIP_KEYS or key in payload:
                continue
            payload[key] = value
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
