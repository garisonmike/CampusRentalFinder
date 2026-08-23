"""
structlog wiring.

Called from each settings module via :func:`configure_logging`. Development
gets a coloured console renderer; production emits one JSON object per line so
a log shipper can parse it without a regex.
"""

from __future__ import annotations

import logging
import logging.config
from typing import Any

import structlog

_SHARED_PROCESSORS: list[Any] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.UnicodeDecoder(),
]


def configure_logging(*, level: str = "INFO", json_output: bool = False) -> None:
    """Configure stdlib logging and structlog to share one output pipeline."""
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=not json_output)
    )

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "structured": {
                    "()": structlog.stdlib.ProcessorFormatter,
                    "processor": renderer,
                    "foreign_pre_chain": _SHARED_PROCESSORS,
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "structured",
                },
            },
            "root": {"handlers": ["console"], "level": level},
            "loggers": {
                "django": {"handlers": ["console"], "level": level, "propagate": False},
                "django.db.backends": {
                    "handlers": ["console"],
                    # SQL logging is opt-in; it is far too noisy by default.
                    "level": "WARNING",
                    "propagate": False,
                },
                "campusrental": {"handlers": ["console"], "level": level, "propagate": False},
            },
        }
    )

    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
