"""Word 文档正文提取和失败处理。"""

import pytest

from easy_agent.services import document_convert
from easy_agent.services.sessions.uploads import _is_parser_placeholder
from easy_agent.utils import file_parser as fp


@pytest.mark.parametrize("extension", ["doc", "docx"])
def test_word_extracts_without_python_docx(tmp_path, monkeypatch, extension):
    source = tmp_path / f"中文.{extension}"
    source.write_bytes(b"word")
    converted = tmp_path / "中文.html"
    converted.write_text("<head><style>ignored</style></head><p>中文正文</p><table><tr><td>表格内容</td></tr></table>", encoding="utf-8")

    def convert(path, target):
        assert path == source
        assert target == "html"
        return converted

    monkeypatch.setattr(document_convert, "convert_office_document", convert)
    assert fp.parse_file_content(str(source)) == "中文正文\n表格内容"


@pytest.mark.parametrize("extension", ["doc", "docx"])
@pytest.mark.parametrize("failure", ["未找到 LibreOffice", "文档转换超时", "加密或损坏"])
def test_word_conversion_failure_is_not_treated_as_text(tmp_path, monkeypatch, extension, failure):
    source = tmp_path / f"bad.{extension}"
    source.write_bytes(b"bad")

    def fail(*_):
        raise RuntimeError(failure)

    monkeypatch.setattr(document_convert, "convert_office_document", fail)
    result = fp.parse_file_content(str(source))
    assert _is_parser_placeholder(result)
    assert failure in result


def test_empty_converted_word_is_not_treated_as_text(tmp_path, monkeypatch):
    source = tmp_path / "empty.docx"
    source.write_bytes(b"word")
    converted = tmp_path / "empty.html"
    converted.write_text("  ", encoding="utf-8")
    monkeypatch.setattr(document_convert, "convert_office_document", lambda *_: converted)
    assert _is_parser_placeholder(fp.parse_file_content(str(source)))
