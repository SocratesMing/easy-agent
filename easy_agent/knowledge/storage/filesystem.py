"""Filesystem-backed original store for local development and mounted NAS."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from ..config import OriginalStorageConfig
from .base import OriginalObject, OriginalReadHandle, OriginalStorageError


_SAFE_ID = re.compile(r"^[A-Za-z0-9-]{1,255}$")
_SAFE_EXTENSION = re.compile(r"^[A-Za-z0-9]{1,16}$")


class FileSystemOriginalStore:
    provider = "filesystem"

    def __init__(self, config: OriginalStorageConfig):
        self.config = config
        filesystem = config.filesystem
        self.root = Path(filesystem.root_path).expanduser().resolve()
        self.base_root = (self.root / filesystem.path_prefix).resolve()
        self.chunk_size = filesystem.chunk_size_mb * 1024 * 1024
        self.dir_mode = int(filesystem.directory_mode, 8)
        self.file_mode = int(filesystem.file_mode, 8)
        self.use_fsync = filesystem.fsync
        self.base_root.mkdir(parents=True, exist_ok=True, mode=self.dir_mode)

    @staticmethod
    def _safe_id(value: str, label: str) -> str:
        if not _SAFE_ID.fullmatch(value):
            raise OriginalStorageError("ORIGINAL_STORAGE_INVALID_KEY", f"invalid {label}", retryable=False)
        return value

    def build_key(self, base_id: str, document_id: str, filename: str) -> str:
        base_id = self._safe_id(base_id, "base id")
        document_id = self._safe_id(document_id, "document id")
        suffix = Path(filename).suffix.lower().lstrip(".")
        extension = suffix if _SAFE_EXTENSION.fullmatch(suffix) else "bin"
        return str(PurePosixPath("v1", base_id[:2], base_id, document_id, f"original.{extension}"))

    def _path(self, storage_key: str) -> Path:
        pure = PurePosixPath(storage_key)
        if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
            raise OriginalStorageError("ORIGINAL_STORAGE_INVALID_KEY", "invalid storage key", retryable=False)
        candidate = (self.base_root / Path(*pure.parts)).resolve(strict=False)
        try:
            candidate.relative_to(self.base_root)
        except ValueError as exc:
            raise OriginalStorageError("ORIGINAL_STORAGE_INVALID_KEY", "storage key escapes root", retryable=False) from exc
        cursor = self.base_root
        for part in pure.parts[:-1]:
            cursor = cursor / part
            if cursor.is_symlink():
                raise OriginalStorageError("ORIGINAL_STORAGE_UNSAFE_PATH", "symbolic links are not allowed", retryable=False)
        if candidate.is_symlink():
            raise OriginalStorageError("ORIGINAL_STORAGE_UNSAFE_PATH", "symbolic links are not allowed", retryable=False)
        return candidate

    def put_atomic(self, storage_key: str, source: BinaryIO, expected_size: int) -> OriginalObject:
        target = self._path(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True, mode=self.dir_mode)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary.open("xb") as output:
                os.chmod(temporary, self.file_mode)
                while True:
                    chunk = source.read(self.chunk_size)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                output.flush()
                if self.use_fsync:
                    os.fsync(output.fileno())
            if size != expected_size:
                raise OriginalStorageError(
                    "ORIGINAL_STORAGE_SIZE_MISMATCH",
                    "stored original size does not match upload",
                    retryable=False,
                )
            if target.exists():
                existing = self.open(storage_key)
                existing_digest = hashlib.sha256()
                for chunk in existing.iter_bytes():
                    existing_digest.update(chunk)
                if existing.size_bytes != size or existing_digest.hexdigest() != digest.hexdigest():
                    raise OriginalStorageError(
                        "ORIGINAL_STORAGE_CONFLICT", "original object already exists", retryable=False
                    )
                temporary.unlink(missing_ok=True)
            else:
                os.replace(temporary, target)
                if self.use_fsync:
                    directory_fd = os.open(target.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
            return OriginalObject(storage_key=storage_key, size_bytes=size, sha256=digest.hexdigest())
        except OriginalStorageError:
            temporary.unlink(missing_ok=True)
            raise
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise OriginalStorageError(
                "ORIGINAL_STORAGE_WRITE_FAILED", "原文存储写入失败", retryable=True
            ) from exc

    def open(self, storage_key: str) -> OriginalReadHandle:
        path = self._path(storage_key)
        try:
            stat = path.stat()
        except OSError as exc:
            raise OriginalStorageError(
                "ORIGINAL_STORAGE_NOT_AVAILABLE", "原文暂不可用", retryable=True
            ) from exc
        if not path.is_file() or path.is_symlink():
            raise OriginalStorageError(
                "ORIGINAL_STORAGE_UNSAFE_PATH", "原文存储路径无效", retryable=False
            )
        return OriginalReadHandle(storage_key, path, stat.st_size, self.chunk_size)

    def quarantine(self, storage_key: str) -> str:
        source = self._path(storage_key)
        quarantine_key = str(PurePosixPath("quarantine", storage_key))
        target = self._path(quarantine_key)
        target.parent.mkdir(parents=True, exist_ok=True, mode=self.dir_mode)
        try:
            os.replace(source, target)
        except OSError as exc:
            raise OriginalStorageError(
                "ORIGINAL_STORAGE_DELETE_FAILED", "原文移入隔离区失败", retryable=True
            ) from exc
        return quarantine_key

    def restore(self, storage_key: str, target_key: str) -> str:
        source = self._path(storage_key)
        target = self._path(target_key)
        target.parent.mkdir(parents=True, exist_ok=True, mode=self.dir_mode)
        if target.exists():
            raise OriginalStorageError(
                "ORIGINAL_STORAGE_CONFLICT", "原文恢复目标已存在", retryable=False
            )
        try:
            os.replace(source, target)
        except OSError as exc:
            raise OriginalStorageError(
                "ORIGINAL_STORAGE_RESTORE_FAILED", "原文恢复失败", retryable=True
            ) from exc
        return target_key

    def purge(self, storage_key: str) -> None:
        try:
            self._path(storage_key).unlink(missing_ok=True)
        except OSError as exc:
            raise OriginalStorageError(
                "ORIGINAL_STORAGE_DELETE_FAILED", "原文删除失败", retryable=True
            ) from exc

    def health(self) -> bool:
        try:
            self.base_root.mkdir(parents=True, exist_ok=True, mode=self.dir_mode)
            probe = self.base_root / f".health-{uuid.uuid4().hex}"
            probe.write_bytes(b"ok")
            probe.unlink()
            return True
        except OSError:
            return False
