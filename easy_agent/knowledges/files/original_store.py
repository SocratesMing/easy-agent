"""知识库原文本地磁盘存储（纯存储适配）。

负责原文对象的原子写入、只读分段句柄与路径穿越/符号链接防护；
不依赖知识库业务服务（``KnowledgeService``），便于独立测试与复用。
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import BinaryIO


# 本地原文根目录（运行期目录，gitignored）
DEFAULT_ORIGINALS_ROOT = Path("data") / "knowledge_originals"

# ---------------------------------------------------------------------------
# 本地原文存储
# ---------------------------------------------------------------------------
class OriginalStorageError(RuntimeError):
    """原文存储异常：携带错误码与可重试标记。"""

    def __init__(self, code: str, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class OriginalReadHandle:
    """本地原文只读句柄：支持分段读取与二进制打开。"""

    def __init__(self, path: Path, size_bytes: int, *, chunk_size: int = 1024 * 1024):
        self.path = path
        self.size_bytes = size_bytes
        self._chunk_size = chunk_size

    def open_binary(self) -> BinaryIO:
        return self.path.open("rb")

    def iter_bytes(self, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        """按 [start, end] 闭区间分段读取文件字节。"""

        remaining_end = self.size_bytes - 1 if end is None else min(end, self.size_bytes - 1)
        if start < 0 or remaining_end < start:
            return
        with self.path.open("rb") as source:
            source.seek(start)
            position = start
            while position <= remaining_end:
                chunk = source.read(min(self._chunk_size, remaining_end - position + 1))
                if not chunk:
                    break
                position += len(chunk)
                yield chunk


class LocalOriginalStore:
    """本地磁盘原文存储：原子写入与路径穿越防护。"""

    provider = "filesystem"
    _SAFE_ID = re.compile(r"^[A-Za-z0-9-]{1,255}$")
    _SAFE_EXTENSION = re.compile(r"^[A-Za-z0-9]{1,16}$")

    def __init__(self, root: str | Path = DEFAULT_ORIGINALS_ROOT):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def _safe_id(cls, value: str, label: str) -> str:
        if not cls._SAFE_ID.fullmatch(value):
            raise OriginalStorageError(
                "ORIGINAL_STORAGE_INVALID_KEY", f"invalid {label}", retryable=False
            )
        return value

    def build_key(self, base_id: str, document_id: str, filename: str) -> str:
        """构造原文存储键：v1/{库 ID 前两位}/{库 ID}/{文档 ID}/original.{后缀}。"""

        base_id = self._safe_id(base_id, "base id")
        document_id = self._safe_id(document_id, "document id")
        suffix = Path(filename).suffix.lower().lstrip(".")
        extension = suffix if self._SAFE_EXTENSION.fullmatch(suffix) else "bin"
        return str(PurePosixPath("v1", base_id[:2], base_id, document_id, f"original.{extension}"))

    def _path(self, storage_key: str) -> Path:
        """校验存储键并解析为根目录内安全路径（防穿越与符号链接）。"""

        pure = PurePosixPath(storage_key)
        if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
            raise OriginalStorageError(
                "ORIGINAL_STORAGE_INVALID_KEY", "invalid storage key", retryable=False
            )
        candidate = (self.root / Path(*pure.parts)).resolve(strict=False)
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise OriginalStorageError(
                "ORIGINAL_STORAGE_INVALID_KEY", "storage key escapes root", retryable=False
            ) from exc
        if candidate.is_symlink() or any(
            (self.root / Path(*pure.parts[: index + 1])).is_symlink()
            for index in range(len(pure.parts) - 1)
        ):
            raise OriginalStorageError(
                "ORIGINAL_STORAGE_UNSAFE_PATH", "symbolic links are not allowed", retryable=False
            )
        return candidate

    def put_atomic(self, storage_key: str, source: BinaryIO, expected_size: int) -> tuple[int, str]:
        """原子写入原文并返回 (字节数, sha256)。"""
        target = self._path(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary.open("xb") as output:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                output.flush()
            if size != expected_size:
                raise OriginalStorageError(
                    "ORIGINAL_STORAGE_SIZE_MISMATCH",
                    "stored original size does not match upload",
                    retryable=False,
                )
            if target.exists():
                # 同一文档重复上传：内容一致则幂等成功，否则拒绝覆盖
                existing = self.open(storage_key)
                existing_digest = hashlib.sha256()
                for chunk in existing.iter_bytes():
                    existing_digest.update(chunk)
                if existing.size_bytes != size or existing_digest.hexdigest() != digest.hexdigest():
                    raise OriginalStorageError(
                        "ORIGINAL_STORAGE_CONFLICT",
                        "original object already exists",
                        retryable=False,
                    )
                temporary.unlink(missing_ok=True)
            else:
                temporary.replace(target)
            return size, digest.hexdigest()
        except OriginalStorageError:
            temporary.unlink(missing_ok=True)
            raise
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise OriginalStorageError(
                "ORIGINAL_STORAGE_WRITE_FAILED", "原文存储写入失败", retryable=True
            ) from exc

    def open(self, storage_key: str) -> OriginalReadHandle:
        """打开原文只读句柄；文件缺失或路径异常时抛存储异常。"""

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
        return OriginalReadHandle(path, stat.st_size)

    def health(self) -> bool:
        """探测根目录可写性：写入并删除一个探测文件。"""

        try:
            probe = self.root / f".health-{uuid.uuid4().hex}"
            probe.write_bytes(b"ok")
            probe.unlink()
            return True
        except OSError:
            return False


__all__ = [
    "DEFAULT_ORIGINALS_ROOT",
    "LocalOriginalStore",
    "OriginalReadHandle",
    "OriginalStorageError",
]
