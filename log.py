# log.py
import logging
from logging.handlers import RotatingFileHandler
import sys
from pathlib import Path
from types import TracebackType
from typing import Optional, Type


class _LoggerProxy:
    """
    Class-based, importable singleton-like logger facade.

    Usage:
        from log import lg

        lg.setup_logging("logs/app.log")      # once at startup
        lg.info("message goes here")          # thereafter, just use lg like a logger
    """

    def __init__(self) -> None:
        self._logger: Optional[logging.Logger] = None

    # ---- Public API -----------------------------------------------------

    def setup_logging(
        self,
        log_file_path: str | Path,
        level: int = logging.INFO,
        max_bytes: int = 500_000,
        backup_count: int = 5,
        to_stderr: bool = True,
        logger_name: str = "app",
        datefmt: str = "%Y-%m-%d %H:%M:%S",
        fmt: str = "%(asctime)s %(levelname)s %(message)s",
    ) -> logging.Logger:
        """
        Configure logging and bind the underlying logger to this proxy.
        Safe to call more than once; it replaces previous handlers.
        """
        # Ensure file/dirs exist
        log_path = Path(log_file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch(exist_ok=True)

        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
        logger.propagate = False  # do not duplicate to root

        # Replace handlers idempotently
        logger.handlers.clear()

        formatter = logging.Formatter(fmt=fmt, datefmt=datefmt)

        file_handler = RotatingFileHandler(
            log_path, maxBytes=max_bytes, backupCount=backup_count
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        if to_stderr:
            stream_handler = logging.StreamHandler(sys.stderr)
            stream_handler.setFormatter(formatter)
            logger.addHandler(stream_handler)

        self._logger = logger

        # Make sure uncaught exceptions are logged
        sys.excepthook = self._handle_uncaught_exception

        logger.debug("Logger configured", extra={"log_file": str(log_path)})
        return logger

    # ---- Logger-like delegation ----------------------------------------

    def __getattr__(self, name: str):
        """
        Delegate attribute access (info, debug, warning, error, critical, etc.)
        to the underlying logging.Logger. If not configured yet, bootstrap a
        minimal stderr logger so calls won't crash.
        """
        logger = self._ensure_logger()
        return getattr(logger, name)

    # ---- Internals ------------------------------------------------------

    def _ensure_logger(self) -> logging.Logger:
        """
        Return the configured logger; if setup_logging hasn't been called yet,
        bootstrap a minimal stderr-only logger at WARNING level.
        """
        if self._logger is not None:
            return self._logger

        # Minimal bootstrap to avoid AttributeError before setup
        logger = logging.getLogger("app")
        logger.setLevel(logging.WARNING)
        logger.propagate = False
        if not logger.handlers:
            formatter = logging.Formatter(
                fmt="%(asctime)s %(levelname)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            sh = logging.StreamHandler(sys.stderr)
            sh.setFormatter(formatter)
            logger.addHandler(sh)
        self._logger = logger
        return logger

    def _handle_uncaught_exception(
        self,
        exc_type: Type[BaseException],
        exc_value: BaseException,
        exc_traceback: Optional[TracebackType],
    ) -> None:
        self._ensure_logger().critical(
            "uncaught exception, application will terminate.",
            exc_info=(exc_type, exc_value, exc_traceback),
        )


# Importable singleton
lg = _LoggerProxy()
