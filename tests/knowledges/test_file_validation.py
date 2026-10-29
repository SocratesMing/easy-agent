"""上传文件编码校验：文本类只接受 UTF-8。"""

import codecs
import io

import pytest

from easy_agent.knowledges.core.config import UploadSecurityConfig
from easy_agent.knowledges.core.file_validation import (
    UploadValidationError,
    validate_upload,
)


def _validate(
    data: bytes,
    *,
    filename: str = "a.txt",
    content_type: str = "text/plain",
    position: int = 0,
) -> io.BytesIO:
    source = io.BytesIO(data)
    source.seek(position)
    validate_upload(
        source,
        filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        config=UploadSecurityConfig(),
    )
    return source


def test_utf8_text_is_accepted_and_stream_position_restored():
    source = _validate("UTF-8 中文".encode("utf-8"), position=2)
    assert source.tell() == 2


def test_utf8_bom_text_is_accepted():
    _validate(codecs.BOM_UTF8 + "带 BOM".encode("utf-8"))


def test_gbk_text_is_rejected():
    with pytest.raises(UploadValidationError) as exc_info:
        _validate("GBK 中文".encode("gbk"))

    assert exc_info.value.code == "KNOWLEDGE_FILE_ENCODING_NOT_UTF8"
    assert "UTF-8" in exc_info.value.message


def test_gbk_csv_is_rejected():
    with pytest.raises(UploadValidationError) as exc_info:
        _validate(
            "名称,数量\n中文,1".encode("gbk"),
            filename="a.csv",
            content_type="text/csv",
        )

    assert exc_info.value.code == "KNOWLEDGE_FILE_ENCODING_NOT_UTF8"


def test_multibyte_character_split_across_chunks_is_accepted():
    data = b"a" * (1024 * 1024 - 1) + "中".encode("utf-8")
    _validate(data)


def test_invalid_utf8_in_later_chunk_is_rejected():
    data = b"a" * (1024 * 1024) + b"\xff\xfe"
    with pytest.raises(UploadValidationError) as exc_info:
        _validate(data)

    assert exc_info.value.code == "KNOWLEDGE_FILE_ENCODING_NOT_UTF8"


def test_binary_office_file_is_not_encoding_checked():
    # verify_magic 默认关闭，这里只确认非文本扩展名不会触发 UTF-8 校验
    _validate(
        b"\xff\xfe\x00\x01binary",
        filename="a.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
