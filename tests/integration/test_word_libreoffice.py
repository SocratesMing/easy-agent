"""真实 LibreOffice 的 Word 解析回归；没有安装 Writer 时明确跳过。

可在只装 Python、pytest 和 LibreOffice 的容器中单独运行：
PYTHONPATH=. python -m pytest --confcutdir=tests/integration tests/integration/test_word_libreoffice.py -v
"""

import subprocess
import sys
from zipfile import ZipFile

import pytest

from easy_agent.services import document_convert as dc
from easy_agent.utils.file_parser import parse_file_content


@pytest.fixture
def word_document(tmp_path, monkeypatch):
    soffice = dc._find_soffice()
    if not soffice:
        pytest.skip("需要安装 LibreOffice Writer 才能运行实际转换测试")
    monkeypatch.setattr(dc, "CACHE_DIR", tmp_path / "转换 缓存")
    monkeypatch.setitem(sys.modules, "docx", None)
    source = tmp_path / "中文 表格.docx"
    # 标准库创建最小 OOXML，测试环境无需安装 python-docx。
    with ZipFile(source, "w") as archive:
        archive.writestr("[Content_Types].xml", '''
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Override PartName="/word/document.xml"
ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>''')
        archive.writestr("_rels/.rels", '''
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Target="word/document.xml"
Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"/>
</Relationships>''')
        archive.writestr("word/document.xml", '''
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body><w:p><w:r><w:t>中文正文：归档需求</w:t></w:r></w:p>
<w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/></w:tblGrid>
<w:tr><w:tc><w:tcPr><w:tcW w:w="3000" w:type="dxa"/></w:tcPr>
<w:p><w:r><w:t>归档条件</w:t></w:r></w:p></w:tc>
<w:tc><w:tcPr><w:tcW w:w="3000" w:type="dxa"/></w:tcPr>
<w:p><w:r><w:t>手动归档</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
<w:p><w:r><w:t>正文末尾</w:t></w:r></w:p><w:sectPr/></w:body></w:document>''')
    return source, soffice


@pytest.mark.parametrize("extension", ["docx", "doc"])
def test_word_body_and_table_use_real_libreoffice(word_document, tmp_path, caplog, extension):
    source, soffice = word_document
    if extension == "doc":
        subprocess.run(
            [soffice, "--headless", f"-env:UserInstallation={(tmp_path / 'fixture_profile').as_uri()}",
             "--convert-to", "doc:MS Word 97", "--outdir", str(tmp_path), str(source)],
            check=True, capture_output=True, timeout=60,
        )
        source = source.with_suffix(".doc")
        assert source.is_file(), "LibreOffice 未生成旧版 DOC 测试文件"
        assert source.read_bytes().startswith(bytes.fromhex("d0cf11e0a1b11ae1"))

    caplog.set_level("INFO", logger="easy_agent.services.document_convert")
    text = parse_file_content(str(source))

    for expected in ("中文正文：归档需求", "归档条件", "手动归档", "正文末尾"):
        assert expected in text
    assert "filter=html:HTML (StarWriter)" in caplog.text
    assert "LibreOffice 转换成功" in caplog.text
    assert parse_file_content(str(source)) == text  # 同时验证缓存中的 HTML 可独立解析。
