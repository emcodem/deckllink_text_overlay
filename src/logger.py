"""Logging configuration for FFCapture."""

import logging
import sys
from pathlib import Path

from . import config


def setup_logging():
    """Configure logging for the application."""
    log_level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Capture all levels

    # Console handler
    if config.LOG_TO_CONSOLE:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    # File handler
    if config.LOG_TO_FILE:
        # Ensure log directory exists
        config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

        try:
            file_handler = logging.FileHandler(config.LOG_FILE, encoding='utf-8')
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
        except Exception as e:
            print(f"Warning: Could not setup file logging: {e}")

    # Suppress noisy loggers
    logging.getLogger('av').setLevel(logging.WARNING)
    logging.getLogger('PyQt6').setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info("Logging initialized")
    logger.debug(f"Log level: {config.LOG_LEVEL}")
    logger.debug(f"Log file: {config.LOG_FILE}")
