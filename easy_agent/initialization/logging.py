"""Logging initialization isolated from environment loading."""

from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
import logging
from pathlib import Path


class _RunidFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "runid"):
            record.runid = "-"
        return True


class _MillisecondFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        ct = datetime.fromtimestamp(record.created)
        if datefmt:
            formatted_time = ct.strftime(datefmt)
        else:
            formatted_time = ct.strftime("%Y-%m-%d %H:%M:%S")
        return f"{formatted_time}.{int(record.msecs * 1000):06d}"


def _reset_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


def setup_logging(log_config: dict | None = None, log_dir: str | None = None) -> str:
    """Initialize process logging from explicit configuration values."""
    cfg = log_config or {}
    resolved_log_dir = log_dir or cfg.get("dir") or "./logs"
    log_file_name = cfg.get("file") or "easy_agent.log"
    fmt = cfg.get("format") or "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    level_name = (cfg.get("level") or "info").lower()
    level = getattr(logging, level_name.upper(), logging.INFO)

    root_logger = logging.getLogger()
    _reset_handlers(root_logger)

    log_dir_path = Path(resolved_log_dir)
    log_dir_path.mkdir(parents=True, exist_ok=True)
    log_file = log_dir_path / log_file_name

    formatter = _MillisecondFormatter(
        fmt=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        style="%",
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(_RunidFilter())

    file_handler = TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
        delay=True,
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.extMatch = r"^\.\d{4}-\d{2}-\d{2}$"
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(_RunidFilter())

    root_logger.setLevel(level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    for logger_name in ("uvicorn", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        _reset_handlers(logger)
        logger.setLevel(level)
        logger.addHandler(console_handler)
        logger.addHandler(file_handler)

    logging.getLogger("deepagents.middleware.skills").setLevel(logging.ERROR)
    return str(log_file)
