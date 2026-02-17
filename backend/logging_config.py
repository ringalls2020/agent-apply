from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
from logging import LogRecord
from typing import Any

_REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)
_HTTP_METHOD: contextvars.ContextVar[str] = contextvars.ContextVar(
    "http_method", default="-"
)
_HTTP_PATH: contextvars.ContextVar[str] = contextvars.ContextVar("http_path", default="-")

_DEFAULT_LOG_LEVEL = "INFO"
_BOOL_TRUE_VALUES = {"1", "true", "yes", "on"}
_LOG_RECORD_BUILTIN_ATTRS = set(
    vars(LogRecord("", logging.INFO, "", 0, "", (), None))
)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in _BOOL_TRUE_VALUES


def bind_request_logging_context(
    *,
    request_id: str,
    http_method: str,
    http_path: str,
) -> tuple[
    contextvars.Token[str],
    contextvars.Token[str],
    contextvars.Token[str],
]:
    return (
        _REQUEST_ID.set(request_id),
        _HTTP_METHOD.set(http_method),
        _HTTP_PATH.set(http_path),
    )


def reset_request_logging_context(
    tokens: tuple[
        contextvars.Token[str],
        contextvars.Token[str],
        contextvars.Token[str],
    ],
) -> None:
    request_token, method_token, path_token = tokens
    _REQUEST_ID.reset(request_token)
    _HTTP_METHOD.reset(method_token)
    _HTTP_PATH.reset(path_token)


class RequestContextFilter(logging.Filter):
    def filter(self, record: LogRecord) -> bool:
        record.request_id = _REQUEST_ID.get()
        record.http_method = _HTTP_METHOD.get()
        record.http_path = _HTTP_PATH.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "http_method": getattr(record, "http_method", "-"),
            "http_path": getattr(record, "http_path", "-"),
        }

        for key, value in record.__dict__.items():
            if key in _LOG_RECORD_BUILTIN_ATTRS:
                continue
            if key in payload:
                continue
            payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=True, default=str)


def configure_logging() -> None:
    if getattr(configure_logging, "_configured", False):
        return

    log_level_name = os.getenv("LOG_LEVEL", _DEFAULT_LOG_LEVEL).upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    sqlalchemy_queries_enabled = _env_flag("SQLALCHEMY_LOG_QUERIES")

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setLevel(log_level)
    handler.addFilter(RequestContextFilter())
    handler.setFormatter(JsonFormatter())
    root_logger.addHandler(handler)

    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if sqlalchemy_queries_enabled else logging.WARNING
    )

    configure_logging._configured = True

    logging.getLogger(__name__).info(
        "logging_configured",
        extra={
            "log_level": logging.getLevelName(log_level),
            "sqlalchemy_log_queries": sqlalchemy_queries_enabled,
        },
    )
