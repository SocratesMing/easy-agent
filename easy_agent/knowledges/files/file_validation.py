"""知识库上传文件校验（类型/魔数/宏/病毒扫描），独立于 HTTP 路由。"""

from __future__ import annotations

import codecs
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import BinaryIO
from zipfile import BadZipFile, ZipFile

from easy_agent.knowledges.core.config import UploadSecurityConfig

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
# 浏览器/Windows 的 MIME 映射并不总与扩展名严格对应：WPS、旧版 Office
# 或企业镜像可能把 .pptx 报成 application/vnd.ms-powerpoint，反之亦然。
# Office 类型按「同族新旧 MIME + zip 容器」宽松接受，真正的一致性由扩展名
# 与可选的魔数校验保证。
_CANONICAL_MIME = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
_ZIP_MIME = frozenset({"application/zip", "application/x-zip-compressed"})
_POWERPOINT_ALIASES = frozenset(
    {"application/mspowerpoint", "application/x-mspowerpoint"}
)


def _office_mime_set(*extensions: str) -> frozenset[str]:
    """同一 Office 族的扩展名（如 doc/docx）共享彼此 MIME 与 zip 容器。"""

    return frozenset(_CANONICAL_MIME[ext] for ext in extensions) | _ZIP_MIME


_DECLARED_MIME = {
    "pdf": frozenset({_CANONICAL_MIME["pdf"]}),
    "doc": _office_mime_set("doc", "docx"),
    "docx": _office_mime_set("doc", "docx"),
    "xls": _office_mime_set("xls", "xlsx"),
    "xlsx": _office_mime_set("xls", "xlsx"),
    "ppt": _office_mime_set("ppt", "pptx") | _POWERPOINT_ALIASES,
    "pptx": _office_mime_set("ppt", "pptx") | _POWERPOINT_ALIASES,
    "txt": frozenset({"text/plain"}),
    "md": frozenset({"text/plain", "text/markdown"}),
    "csv": frozenset({"text/plain", "text/csv", "application/vnd.ms-excel"}),
}


def canonical_content_type(filename: str, content_type: str) -> str:
    """按文件扩展名归一化已知 Office / PDF 的 MIME；其它类型保持原值。"""

    extension = Path(filename).suffix.lower().lstrip(".")
    return _CANONICAL_MIME.get(extension, content_type or "application/octet-stream")


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
            "KNOWLEDGE_CONTENT_TYPE_MISMATCH", "文件扩展名与声明类型不一致"
        )


def _verify_magic(source: BinaryIO, extension: str, reject_macros: bool) -> None:
    prefix = _read_prefix(source)
    if extension == "pdf":
        if not prefix.startswith(b"%PDF-"):
            raise UploadValidationError("KNOWLEDGE_FILE_SIGNATURE_MISMATCH", "PDF 文件签名无效")
        return
    if extension in {"doc", "xls", "ppt"}:
        if not prefix.startswith(_OLE_MAGIC):
            raise UploadValidationError("KNOWLEDGE_FILE_SIGNATURE_MISMATCH", "Office 文件签名无效")
        return
    if extension in _OFFICE_MEMBER:
        position = source.tell()
        try:
            source.seek(0)
            with ZipFile(source) as archive:
                names = set(archive.namelist())
                if _OFFICE_MEMBER[extension] not in names:
                    raise UploadValidationError(
                        "KNOWLEDGE_FILE_SIGNATURE_MISMATCH", "Office 文件结构与扩展名不一致"
                    )
                if reject_macros and any(
                    name.lower().endswith("vbaproject.bin") for name in names
                ):
                    raise UploadValidationError(
                        "KNOWLEDGE_OFFICE_MACRO_REJECTED", "不允许上传包含宏的 Office 文件"
                    )
        except BadZipFile as exc:
            raise UploadValidationError(
                "KNOWLEDGE_FILE_SIGNATURE_MISMATCH", "Office 文件容器无效"
            ) from exc
        finally:
            source.seek(position)
        return
    if extension in {"txt", "md", "csv"} and b"\x00" in prefix:
        raise UploadValidationError("KNOWLEDGE_TEXT_BINARY_REJECTED", "文本文件包含二进制内容")


_TEXT_ENCODING_EXTENSIONS = {
    "txt",
    "md",
    "markdown",
    "csv",
    "tsv",
    "json",
    "log",
    "xml",
    "yaml",
    "yml",
    "html",
    "htm",
}


def _verify_utf8(source: BinaryIO, extension: str) -> None:
    """文本类文件只接受 UTF-8；流式校验后恢复调用方的流位置。"""

    if extension not in _TEXT_ENCODING_EXTENSIONS:
        return
    position = source.tell()
    try:
        source.seek(0)
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        while chunk := source.read(1024 * 1024):
            decoder.decode(chunk)
        decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise UploadValidationError(
            "KNOWLEDGE_FILE_ENCODING_NOT_UTF8",
            "只支持 UTF-8 编码的文本文件，请转换编码后重新上传",
        ) from exc
    finally:
        source.seek(position)


def _scan_for_virus(source: BinaryIO, config: UploadSecurityConfig) -> None:
    scanner = config.virus_scan
    if not scanner.enabled:
        return
    executable = shutil.which(scanner.command)
    if not executable:
        if scanner.required:
            raise UploadValidationError(
                "KNOWLEDGE_VIRUS_SCANNER_UNAVAILABLE", "病毒扫描服务不可用", retryable=True
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
            raise UploadValidationError("KNOWLEDGE_MALWARE_DETECTED", "文件未通过安全扫描")
        if result.returncode != 0:
            raise UploadValidationError(
                "KNOWLEDGE_VIRUS_SCANNER_UNAVAILABLE", "病毒扫描服务不可用", retryable=True
            )
    except subprocess.TimeoutExpired as exc:
        raise UploadValidationError(
            "KNOWLEDGE_VIRUS_SCANNER_TIMEOUT", "病毒扫描超时", retryable=True
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
    """校验上传文件且不消费调用方的流位置。"""

    if config.reject_empty_files and size_bytes <= 0:
        raise UploadValidationError("KNOWLEDGE_EMPTY_FILE", "不能上传空文件")
    extension = Path(filename).suffix.lower().lstrip(".")
    _verify_declared_mime(extension, content_type)
    if config.verify_magic:
        _verify_magic(source, extension, config.reject_office_macros)
    _verify_utf8(source, extension)
    _scan_for_virus(source, config)


__all__ = ["UploadValidationError", "canonical_content_type", "validate_upload"]
