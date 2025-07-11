"""CLARITY Digital Twin Platform - Logging Configuration.

HIPAA-compliant structured logging with audit trail support and
configurable log levels for development and production environments.
"""

# removed - breaks FastAPI

import logging
import logging.config
import sys
from typing import Any

from clarity.core.config_aws import get_settings

# Configure logger
logger = logging.getLogger(__name__)

# Track if logging has been configured
_logging_configured = False


def setup_logging(force: bool = False) -> None:
    """Configure logging for the application based on environment settings.

    Args:
        force: If True, force reconfiguration even if already configured.
               Default is False to prevent duplicate handlers.
    """
    global _logging_configured

    # Prevent duplicate configuration unless forced
    if _logging_configured and not force:
        return

    # Clear existing handlers to prevent duplicates when forced
    if force:
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

    settings = get_settings()

    # Base configuration for structured logging
    logging_config: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "detailed": {
                "format": (
                    "%(asctime)s | %(name)s | %(levelname)s | "
                    "%(filename)s:%(lineno)d | %(funcName)s | %(message)s"
                ),
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "simple": {
                "format": "%(asctime)s | %(levelname)s | %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "json": {
                "format": (
                    "%(asctime)s | %(name)s | %(levelname)s | "
                    "%(filename)s:%(lineno)d | %(funcName)s | %(message)s"
                ),
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": settings.log_level,
                "formatter": "detailed" if settings.debug else "simple",
                "stream": sys.stdout,
            },
        },
        "loggers": {
            "clarity": {
                "level": settings.log_level,
                "handlers": ["console"],
                "propagate": True,
            },
            "uvicorn": {
                "level": "INFO",
                "handlers": ["console"],
                "propagate": False,
            },
            "uvicorn.access": {
                "level": "INFO",
                "handlers": ["console"],
                "propagate": False,
            },
        },
        "root": {
            "level": settings.log_level,
            "handlers": ["console"],
        },
    }

    # Track if this is the first configuration
    first_config = not _logging_configured

    # Apply configuration
    logging.config.dictConfig(logging_config)

    # Mark as configured
    _logging_configured = True

    # Log successful configuration only on first setup
    if first_config:
        logger = logging.getLogger(__name__)
        environment_msg = f"Logging configured for {settings.environment} environment"
        level_msg = f"with level {settings.log_level}"
        logger.info("%s %s", environment_msg, level_msg)


def configure_basic_logging(
    level: str | int = logging.INFO, format: str | None = None, **kwargs: Any  # noqa: A002, ARG001
) -> None:
    """Configure basic logging with duplicate prevention.

    This is a drop-in replacement for logging.basicConfig() that prevents
    duplicate handlers and uses our centralized configuration.

    Args:
        level: Logging level (default: INFO)
        format: Log format string (ignored - uses centralized format)
        **kwargs: Additional arguments (ignored for compatibility)
    """
    global _logging_configured

    # Check if logging is already configured by checking for handlers
    root_logger = logging.getLogger()
    if root_logger.handlers and _logging_configured:
        # Just update the level if needed
        if isinstance(level, str):
            level = getattr(logging, level.upper(), logging.INFO)
        root_logger.setLevel(level)
        # Also update all existing handlers
        for handler in root_logger.handlers:
            handler.setLevel(level)
        return

    # Use our centralized setup
    setup_logging()

    # Update level after setup if different from default
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)
    if level != logging.INFO:
        root_logger.setLevel(level)
        for handler in root_logger.handlers:
            handler.setLevel(level)
