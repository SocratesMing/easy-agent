"""doc 懒转换器测试：纯逻辑跨平台，COM 转换仅 Windows。"""

import sys

import pytest

from easy_agent.knowledges import doc_converter


def test_cache_key_is_stable_and_distinct():
    a = doc_converter.docx_cache_key("doc-1", None, 100)
    b = doc_converter.docx_cache_key("doc-1", None, 100)
    assert a == b and len(a) == 32
    assert doc_converter.docx_cache_key("doc-1", "sha-x", 100) != a
    assert doc_converter.docx_cache_key("doc-2", None, 100) != a


def test_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_converter, "CACHE_DIR", tmp_path)
    doc_converter.docx_cache_put("k1", b"abc")
    assert doc_converter.docx_cache_get("k1") == b"abc"
    assert doc_converter.docx_cache_get("missing") is None


def test_safe_stem_strips_path_and_illegal_chars():
    assert doc_converter._safe_stem("a/b\\c:d*.doc") == "c_d_.doc"
    assert doc_converter._safe_stem("") == "document"
    assert doc_converter._safe_stem("   ") == "document"


def test_empty_payload_raises_without_engine():
    with pytest.raises(doc_converter.DocConverterError):
        doc_converter.convert_doc_to_docx_bytes("x.doc", b"")


@pytest.mark.skipif(sys.platform != "win32", reason="Word COM 仅 Windows 可用")
def test_convert_real_doc_via_com():
    """预置真实 .doc 夹具，验证转换为合法 docx（PK zip 头）。"""
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "sample.doc"
    data = fixture.read_bytes()
    assert data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # OLE2 魔数 = 真 .doc

    result = doc_converter.convert_doc_to_docx_bytes("sample.doc", data)
    assert result[:2] == b"PK"  # docx 为 zip 容器
