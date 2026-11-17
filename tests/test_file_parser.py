"""DOC 正文解析：转换后的段落/表格、系统工具兜底及失败占位。"""

import subprocess
from pathlib import Path

import pytest

from easy_agent.services import document_convert
from easy_agent.services.sessions.uploads import _is_parser_placeholder
from easy_agent.utils import file_parser as fp


@pytest.fixture
def doc_source(tmp_path, monkeypatch):
    source = tmp_path / "旧版.doc"
    source.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"fixture")
    monkeypatch.setattr(fp.sys, "platform", "linux")

    def missing(command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(fp.subprocess, "run", missing)
    return source


def test_doc_converts_to_docx_and_extracts_chinese_table(doc_source, tmp_path, monkeypatch):
    docx = pytest.importorskip("docx")
    converted = tmp_path / "converted.docx"
    document = docx.Document()
    document.add_paragraph("中文归档需求")
    document.add_table(rows=1, cols=2).rows[0].cells[0].text = "条件"
    document.tables[0].cell(0, 1).text = "手动归档"
    document.save(converted)
    calls = []

    def convert(source, target):
        calls.append((source, target))
        return converted

    monkeypatch.setattr(document_convert, "convert_office_document", convert)

    text = fp.parse_file_content(str(doc_source))

    assert "中文归档需求" in text
    assert "条件 | 手动归档" in text
    assert not _is_parser_placeholder(text)
    assert calls == [(doc_source, "docx")]


def test_legacy_converter_still_works(doc_source, monkeypatch):
    def run(command, **kwargs):
        assert command[0] == "antiword"
        return subprocess.CompletedProcess(command, 0, stdout="旧版中文正文", stderr="")

    monkeypatch.setattr(fp.subprocess, "run", run)
    assert fp.parse_file_content(str(doc_source)) == "旧版中文正文"


def test_macos_textutil_recovers_when_libreoffice_is_unavailable(doc_source, monkeypatch):
    monkeypatch.setattr(fp.sys, "platform", "darwin")

    def convert(source, target):
        raise RuntimeError("未找到 LibreOffice")

    def run(command, **kwargs):
        if Path(command[0]).name != "textutil":
            raise FileNotFoundError(command[0])
        assert "-stdout" in command and "UTF-8" in command
        return subprocess.CompletedProcess(command, 0, stdout="系统提取的中文正文\n", stderr="")

    monkeypatch.setattr(document_convert, "convert_office_document", convert)
    monkeypatch.setattr(fp.subprocess, "run", run)
    assert fp.parse_file_content(str(doc_source)).strip() == "系统提取的中文正文"


@pytest.mark.parametrize("failure", ["未找到 LibreOffice", "文档转换超时", "加密或损坏"])
def test_doc_failure_is_reported_as_failure(doc_source, monkeypatch, failure):
    def convert(source, target):
        raise RuntimeError(failure)

    monkeypatch.setattr(document_convert, "convert_office_document", convert)
    text = fp.parse_file_content(str(doc_source))

    assert _is_parser_placeholder(text)
    assert failure in text


def test_invalid_converted_docx_is_not_returned_as_body(doc_source, tmp_path, monkeypatch):
    invalid = tmp_path / "invalid.docx"
    invalid.write_bytes(b"not a Word document")
    monkeypatch.setattr(document_convert, "convert_office_document", lambda *_: invalid)

    assert _is_parser_placeholder(fp.parse_file_content(str(doc_source)))
