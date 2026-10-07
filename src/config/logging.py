import json
import logging
from datetime import UTC, datetime

from config.request_id import request_id


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        identifier = request_id.get()
        if identifier:
            payload["request_id"] = identifier
        for name in ("path", "status", "attempt_id", "operation"):
            if hasattr(record, name):
                payload[name] = getattr(record, name)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)
