"""Logging estructurado con structlog, desde el día uno.

Regla de seguridad: en INFO no se loguean prompts completos ni contenido de
mensajes. Se loguean identificadores, duraciones y conteos.
"""

import logging
import sys
import uuid
from contextvars import ContextVar

import structlog

_request_id: ContextVar[str | None] = ContextVar("byte_request_id", default=None)


def new_request_id() -> str:
    """Id corto y legible para correlacionar logs y respuestas de error."""
    return f"req_{uuid.uuid4().hex[:8]}"


def set_request_id(request_id: str) -> None:
    _request_id.set(request_id)


def get_request_id() -> str | None:
    return _request_id.get()


def _add_request_id(_logger: object, _name: str, event_dict: dict) -> dict:
    request_id = _request_id.get()
    if request_id is not None:
        event_dict.setdefault("request_id", request_id)
    return event_dict


def configure_logging(env: str = "dev", level: int = logging.INFO) -> None:
    """En dev sale coloreado y legible; en prod sale JSON para la plataforma."""
    renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer() if env == "dev" else structlog.processors.JSONRenderer()
    )
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level, force=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _add_request_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
