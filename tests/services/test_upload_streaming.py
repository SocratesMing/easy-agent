"""上传流式落盘/单文件上限回归测试（PERF-06）。"""

import io

import pytest
from starlette.datastructures import UploadFile

from easy_agent.services import session_uploads as su


def _upload(data: bytes, name: str = "a.txt") -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=name)


@pytest.mark.asyncio
async def test_stream_upload_writes_all_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(su, "get_max_file_size_bytes", lambda: 8192)
    dest = tmp_path / "out.bin"
    size = await su.save_upload_streaming(_upload(b"x" * 4096), dest, chunk_size=1024)
    assert size == 4096
    assert dest.read_bytes() == b"x" * 4096


@pytest.mark.asyncio
async def test_stream_upload_rejects_over_limit_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setattr(su, "get_max_file_size_bytes", lambda: 10)
    dest = tmp_path / "out.bin"
    with pytest.raises(su.UploadTooLargeError):
        await su.save_upload_streaming(_upload(b"x" * 32), dest, chunk_size=8)
    assert not dest.exists(), "超限时必须删除半成品文件"
