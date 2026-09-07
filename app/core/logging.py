"""Centralized logging configuration."""

import logging
import sys


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structured console logging for the application.
    
    Ensures standard timestamps, level names, and logger context.
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    
    log_format = "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # Configure root logger
    logging.basicConfig(
        level=numeric_level,
        format=log_format,
        datefmt=date_format,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )

    # Adjust external noisy loggers if necessary
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
