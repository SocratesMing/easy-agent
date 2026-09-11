"""Contracts for independently stored knowledge-document originals."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator, Protocol


class OriginalStorageError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class OriginalObject:
    storage_key: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class OriginalReadHandle:
    storage_key: str
    path: Path
    size_bytes: int
    chunk_size: int

    def iter_bytes(self, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        final_end = self.size_bytes - 1 if end is None else min(end, self.size_bytes - 1)
        remaining = max(0, final_end - start + 1)
        with self.path.open("rb") as stream:
            stream.seek(start)
            while remaining:
                chunk = stream.read(min(self.chunk_size, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    def open_binary(self) -> BinaryIO:
        return self.path.open("rb")


class OriginalDocumentStore(Protocol):
    provider: str

    def build_key(self, base_id: str, document_id: str, filename: str) -> str: ...
    def put_atomic(
        self, storage_key: str, source: BinaryIO, expected_size: int
    ) -> OriginalObject: ...
    def open(self, storage_key: str) -> OriginalReadHandle: ...
    def quarantine(self, storage_key: str) -> str: ...
    def restore(self, storage_key: str, target_key: str) -> str: ...
    def purge(self, storage_key: str) -> None: ...
    def health(self) -> bool: ...
