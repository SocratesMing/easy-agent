"""Reusable service logging initialization.

Copy this module into another service and adjust the constants below. It only
depends on the Python standard library and does not read application config.
"""

from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
import logging
import os
from pathlib import Path
import re
import socket
import time

# 服务日志硬编码配置。修改此处即可，不依赖 config.yaml 或环境变量。
LOG_DIR = "./logs"
LOG_SERVICE_NAME = ""
LOG_VERSION = "v01"
LOG_PROC_FILE = "proc-{version}-{service_name}.log"
LOG_ERR_FILE = "err-{version}-{service_name}.log"
LOG_COMM_FILE = "comm-{version}-{service_name}.log"
LOG_COMMUNICATION_LOGGERS = ("uvicorn.access",)
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_LEVEL = "info"
LOG_MAX_BYTES = 50 * 1024 * 1024
LOG_ROTATION_WHEN = "midnight"
LOG_ROTATION_INTERVAL = 1
LOG_BACKUP_COUNT = 30


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


class _CommunicationFilter(logging.Filter):
    def __init__(self, logger_names: list[str]):
        super().__init__()
        self._logger_names = tuple(logger_names)

    def filter(self, record):
        return any(
            record.name == logger_name
            or record.name.startswith(f"{logger_name}.")
            for logger_name in self._logger_names
        )


class _ProcessFilter(_CommunicationFilter):
    def filter(self, record):
        return not super().filter(record)


class _TimeSizeRotatingFileHandler(TimedRotatingFileHandler):
    def __init__(self, *args, max_bytes: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.maxBytes = max_bytes
        self._rollover_by_size = False

    def shouldRollover(self, record):
        if self.stream is None:
            self.stream = self._open()

        if self.maxBytes > 0:
            self.stream.flush()
            current_size = os.fstat(self.stream.fileno()).st_size
            message = self.format(record) + self.terminator
            message_size = len(message.encode(self.encoding or "utf-8"))
            if current_size + message_size >= self.maxBytes:
                self._rollover_by_size = True
                return True

        self._rollover_by_size = False
        return super().shouldRollover(record)

    def doRollover(self):
        original_suffix = self.suffix
        try:
            if self._rollover_by_size:
                self.suffix = self._unique_size_suffix()
            super().doRollover()
        finally:
            self.suffix = original_suffix
            self._rollover_by_size = False

    def _unique_size_suffix(self) -> str:
        current_time = int(time.time())
        time_tuple = (
            time.gmtime(current_time)
            if self.utc
            else time.localtime(current_time)
        )
        suffix = time.strftime("%Y-%m-%d_%H-%M-%S", time_tuple)
        candidate = suffix
        sequence = 1
        while os.path.exists(f"{self.baseFilename}.{candidate}"):
            candidate = f"{suffix}.{sequence}"
            sequence += 1
        return candidate


def _reset_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


def _build_file_handler(
    log_file: Path,
    level: int,
    formatter: logging.Formatter,
    record_filter: logging.Filter | None = None,
) -> TimedRotatingFileHandler:
    handler = _TimeSizeRotatingFileHandler(
        log_file,
        when=LOG_ROTATION_WHEN,
        interval=LOG_ROTATION_INTERVAL,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
        delay=False,
        max_bytes=LOG_MAX_BYTES,
    )
    handler.suffix = "%Y-%m-%d"
    handler.extMatch = re.compile(
        r"^\d{4}-\d{2}-\d{2}(?:_\d{2}-\d{2}-\d{2})?(?:\.\d+)?$"
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    handler.addFilter(_RunidFilter())
    if record_filter is not None:
        handler.addFilter(record_filter)
    return handler


def setup_logging() -> dict[str, str]:
    """Initialize process logging from explicit configuration values."""
    resolved_log_dir = LOG_DIR
    fmt = LOG_FORMAT
    level_name = LOG_LEVEL.lower()
    level = getattr(logging, level_name.upper(), logging.INFO)
    service_name = LOG_SERVICE_NAME or socket.gethostname()
    version = LOG_VERSION
    communication_loggers = LOG_COMMUNICATION_LOGGERS
    name_values = {"service_name": service_name, "version": version}

    root_logger = logging.getLogger()
    _reset_handlers(root_logger)

    log_dir_path = Path(resolved_log_dir)
    log_dir_path.mkdir(parents=True, exist_ok=True)
    formatter = _MillisecondFormatter(
        fmt=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        style="%",
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(_RunidFilter())

    log_files = {
        "proc": log_dir_path
        / LOG_PROC_FILE.format(**name_values),
        "err": log_dir_path / LOG_ERR_FILE.format(**name_values),
        "comm": log_dir_path
        / LOG_COMM_FILE.format(**name_values),
    }
    process_filter = _ProcessFilter(communication_loggers)
    communication_filter = _CommunicationFilter(communication_loggers)
    process_handler = _build_file_handler(
        log_files["proc"], level, formatter, process_filter
    )
    error_handler = _build_file_handler(log_files["err"], logging.ERROR, formatter)
    communication_handler = _build_file_handler(
        log_files["comm"], level, formatter, communication_filter
    )

    root_logger.setLevel(level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(process_handler)
    root_logger.addHandler(error_handler)
    root_logger.addHandler(communication_handler)

    for logger_name in (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "uvicorn.asgi",
        "fastapi",
    ):
        logger = logging.getLogger(logger_name)
        _reset_handlers(logger)
        logger.setLevel(level)
        logger.propagate = True

    logging.getLogger("deepagents.middleware.skills").setLevel(logging.ERROR)
    return {name: str(path) for name, path in log_files.items()}
