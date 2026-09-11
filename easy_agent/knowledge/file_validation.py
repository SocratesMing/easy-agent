"""Content-based upload validation kept outside the HTTP route."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path
from typing import BinaryIO
from zipfile import BadZipFile, ZipFile

from .config import UploadSecurityConfig

logger = logging.getLogger(__name__)


class UploadValidationError(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


_OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
_OFFICE_MEMBER = {
    "docx": "word/document.xml",
    "xlsx": "xl/workbook.xml",
    "pptx": "ppt/presentation.xml",
}
_DECLARED_MIME = {
    "pdf": {"application/pdf"},
    "doc": {"application/msword"},
    "docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
    },
    "xls": {"application/vnd.ms-excel"},
    "xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "application/zip",
    },
    "ppt": {"application/vnd.ms-powerpoint"},
    "pptx": {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/zip",
    },
    "txt": {"text/plain"},
    "md": {"text/plain", "text/markdown"},
    "csv": {"text/plain", "text/csv", "application/vnd.ms-excel"},
}


def _read_prefix(source: BinaryIO, length: int = 8192) -> bytes:
    position = source.tell()
    try:
        source.seek(0)
        return source.read(length)
    finally:
        source.seek(position)


def _verify_declared_mime(extension: str, content_type: str) -> None:
    declared = (content_type or "").split(";", 1)[0].strip().lower()
    if not declared or declared == "application/octet-stream":
        return
    allowed = _DECLARED_MIME.get(extension, set())
    if allowed and declared not in allowed:
        raise UploadValidationError(
            "KNOWLEDGE_CONTENT_TYPE_MISMATCH",
            "文件扩展名与声明类型不一致",
        )


def _verify_magic(source: BinaryIO, extension: str, reject_macros: bool) -> None:
    prefix = _read_prefix(source)
    if extension == "pdf":
        if not prefix.startswith(b"%PDF-"):
            raise UploadValidationError(
                "KNOWLEDGE_FILE_SIGNATURE_MISMATCH", "PDF 文件签名无效"
            )
        return
    if extension in {"doc", "xls", "ppt"}:
        if not prefix.startswith(_OLE_MAGIC):
            raise UploadValidationError(
                "KNOWLEDGE_FILE_SIGNATURE_MISMATCH", "Office 文件签名无效"
            )
        return
    if extension in _OFFICE_MEMBER:
        position = source.tell()
        try:
            source.seek(0)
            with ZipFile(source) as archive:
                names = set(archive.namelist())
                if _OFFICE_MEMBER[extension] not in names:
                    raise UploadValidationError(
                        "KNOWLEDGE_FILE_SIGNATURE_MISMATCH",
                        "Office 文件结构与扩展名不一致",
                    )
                if reject_macros and any(
                    name.lower().endswith("vbaproject.bin") for name in names
                ):
                    raise UploadValidationError(
                        "KNOWLEDGE_OFFICE_MACRO_REJECTED",
                        "不允许上传包含宏的 Office 文件",
                    )
        except BadZipFile as exc:
            raise UploadValidationError(
                "KNOWLEDGE_FILE_SIGNATURE_MISMATCH", "Office 文件容器无效"
            ) from exc
        finally:
            source.seek(position)
        return
    if extension in {"txt", "md", "csv"} and b"\x00" in prefix:
        raise UploadValidationError(
            "KNOWLEDGE_TEXT_BINARY_REJECTED", "文本文件包含二进制内容"
        )


def _scan_for_virus(source: BinaryIO, config: UploadSecurityConfig) -> None:
    scanner = config.virus_scan
    if not scanner.enabled:
        return
    executable = shutil.which(scanner.command)
    if not executable:
        if scanner.required:
            raise UploadValidationError(
                "KNOWLEDGE_VIRUS_SCANNER_UNAVAILABLE",
                "病毒扫描服务不可用",
                retryable=True,
            )
        logger.warning("病毒扫描客户端不可用，已按非强制策略跳过")
        return
    position = source.tell()
    temporary_path: str | None = None
    try:
        source.seek(0)
        with tempfile.NamedTemporaryFile(prefix="easyagent-scan-", delete=False) as tmp:
            temporary_path = tmp.name
            while chunk := source.read(1024 * 1024):
                tmp.write(chunk)
        result = subprocess.run(
            [executable, "--no-summary", temporary_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=scanner.timeout_seconds,
            check=False,
        )
        if result.returncode == 1:
            raise UploadValidationError(
                "KNOWLEDGE_MALWARE_DETECTED", "文件未通过安全扫描"
            )
        if result.returncode != 0:
            raise UploadValidationError(
                "KNOWLEDGE_VIRUS_SCANNER_UNAVAILABLE",
                "病毒扫描服务不可用",
                retryable=True,
            )
    except subprocess.TimeoutExpired as exc:
        raise UploadValidationError(
            "KNOWLEDGE_VIRUS_SCANNER_TIMEOUT",
            "病毒扫描超时",
            retryable=True,
        ) from exc
    finally:
        source.seek(position)
        if temporary_path:
            Path(temporary_path).unlink(missing_ok=True)


def validate_upload(
    source: BinaryIO,
    *,
    filename: str,
    content_type: str,
    size_bytes: int,
    config: UploadSecurityConfig,
) -> None:
    """Validate an upload without consuming the caller's stream."""

    if config.reject_empty_files and size_bytes <= 0:
        raise UploadValidationError(
            "KNOWLEDGE_EMPTY_FILE", "不能上传空文件"
        )
    extension = Path(filename).suffix.lower().lstrip(".")
    _verify_declared_mime(extension, content_type)
    if config.verify_magic:
        _verify_magic(source, extension, config.reject_office_macros)
    _scan_for_virus(source, config)


__all__ = ["UploadValidationError", "validate_upload"]
