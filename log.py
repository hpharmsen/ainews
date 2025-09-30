import logging
from logging.handlers import RotatingFileHandler
import sys
from pathlib import Path

_logger: logging.Logger | None = None


def setup_logging(log_file_path:str|Path):
    global _logger

    # Ensure log file and parent directory exist
    log_path = Path(log_file_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.touch(exist_ok=True)

    # Minimal logging setup (one call)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            RotatingFileHandler(log_file_path, maxBytes=500_000, backupCount=5),
            logging.StreamHandler(sys.stderr),
        ],
        force=True,  # overwrite previous logging configs if any
    )

    # Initiate the global logger
    _logger = logging.getLogger("app")

    # Make sure uncaught exceptions are logged
    sys.excepthook = handle_uncaught_exception

    return _logger


def lg():
    """Return the configured logger instance."""
    return _logger


def handle_uncaught_exception(exc_type, exc_value, exc_traceback):
    _logger.critical(
        "uncaught exception, application will terminate.",
        exc_info=(exc_type, exc_value, exc_traceback),
    )
