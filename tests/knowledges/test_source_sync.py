"""文档 SQL 同步与元数据过滤单元测试（不依赖外部 MySQL / RAGFlow）。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from easy_agent.knowledges.core.config import SourceSyncConfig
from easy_agent.knowledges.core.models import DocumentMetadataFilter
from easy_agent.knowledges.core.repository import KnowledgeRepository
from easy_agent.knowledges.runtime.source_sync import (
    _iter_windows,
    build_source_query,
    build_upload_name,
    date_folder_segments,
    fetch_source_rows,
    parse_publish_date,
    resolve_source_file,
    resolve_table_name,
    sanitize_title,
)


# ---------------------------------------------------------------------------
# 纯函数：日期 / 命名 / 窗口
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (datetime(2026, 10, 21, 8, 30), "2026-10-21"),
        ("2026-10-21 08:30:00", "2026-10-21"),
        ("2026-10-21", "2026-10-21"),
        ("2026/10/21", "2026-10-21"),
        ("20261021", "2026-10-21"),
        ("2026-10-21T08:30:00", "2026-10-21"),
        (None, None),
        ("", None),
        ("not-a-date", None),
    ],
)
def test_parse_publish_date(value, expected):
    assert parse_publish_date(value) == expected


def test_build_upload_name_keeps_extension_and_sanitizes_title():
    name = build_upload_name("长安银行/2011年:年度报告", "123", r"D:\reports\a.PDF")
    assert name == "长安银行_2011年_年度报告_123.PDF"


def test_build_upload_name_falls_back_to_id_when_title_empty():
    assert build_upload_name("", "123", "/data/a.pdf") == "123.pdf"
    assert build_upload_name(None, "123", "/data/a.pdf") == "123.pdf"


def test_build_upload_name_truncates_long_title_by_utf8_bytes():
    name = build_upload_name("汉" * 300, "123", "/data/a.pdf")
    stem = name.rsplit(".", 1)[0]
    assert stem.endswith("_123")
    # 知识库文件名上限 127 字节；这里留 120 字节预算，中文按字节裁剪
    assert len(name.encode("utf-8")) <= 120


def test_date_folder_segments_use_chinese_units():
    assert date_folder_segments("2026-10-23") == ["2026年", "10月", "23日"]
    assert date_folder_segments("2026-01-05") == ["2026年", "1月", "5日"]


def test_sanitize_title_strips_illegal_chars():
    assert sanitize_title('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"


def test_iter_windows_splits_by_days():
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = datetime(2026, 10, 16, tzinfo=UTC)
    windows = list(_iter_windows(start, end, 7))
    assert [item[0].day for item in windows] == [1, 8, 15]
    assert windows[-1][1] == end


# ---------------------------------------------------------------------------
# 过滤器模型
# ---------------------------------------------------------------------------
def test_metadata_filter_validates_date_and_normalizes_lists():
    model = DocumentMetadataFilter(
        publish_date_from="2026-10-01",
        publish_date_to="2026-10-05",
        report_tags=[" 公告 ", "公告", ""],
        report_second_sources=[],
        report_title_contains="  ",
    )
    assert model.publish_date_from == "2026-10-01"
    assert model.report_tags == ["公告"]
    assert model.report_title_contains is None


def test_metadata_filter_rejects_invalid_range():
    with pytest.raises(ValidationError):
        DocumentMetadataFilter(publish_date_from="2026-10-05", publish_date_to="2026-10-01")
    with pytest.raises(ValidationError):
        DocumentMetadataFilter(publish_date_from="2026/10/01")


def test_source_table_defaults_to_builtin_and_auto_creates():
    table, create = resolve_table_name(SourceSyncConfig())
    assert table == "easy_agent_report"
    assert create is True
    query = build_source_query(table)
    assert "FROM `easy_agent_report`" in query
    assert "%(start)s" in query and "%(end)s" in query


def test_source_table_can_be_overridden_and_validated():
    config = SourceSyncConfig(mysql={"table": "my_reports"})
    table, create = resolve_table_name(config)
    assert (table, create) == ("my_reports", False)

    with pytest.raises(RuntimeError):
        resolve_table_name(SourceSyncConfig(mysql={"table": "bad-name;"}))


def test_resolve_source_file_requires_absolute_without_root(tmp_path):
    config = SourceSyncConfig()
    absolute = tmp_path / "a.pdf"
    assert resolve_source_file(config, str(absolute)) == absolute
    assert resolve_source_file(config, "relative/a.pdf") is None
    assert resolve_source_file(config, "") is None


def test_resolve_source_file_remaps_to_configured_root(tmp_path):
    config = SourceSyncConfig(file_root=str(tmp_path))
    # 数据库存的是别处的绝对路径：只取文件名落到配置根目录
    assert resolve_source_file(config, str(tmp_path / "other" / "a.pdf")) == tmp_path / "a.pdf"
    # 相对路径：保留相对目录
    assert resolve_source_file(config, "sub/a.pdf") == tmp_path / "sub" / "a.pdf"


def test_resolve_source_file_rejects_relative_root(tmp_path):
    config = SourceSyncConfig(file_root="relative/root")
    with pytest.raises(RuntimeError):
        resolve_source_file(config, str(tmp_path / "a.pdf"))


def test_fetch_source_rows_requires_host_and_database():
    now = datetime.now(UTC)
    with pytest.raises(RuntimeError, match="配置不完整"):
        fetch_source_rows(SourceSyncConfig(), now, now)


# ---------------------------------------------------------------------------
# 仓储层：元数据硬过滤与同步台账幂等
# ---------------------------------------------------------------------------
def _create_base_and_documents(repo: KnowledgeRepository) -> tuple[str, dict[str, str]]:
    base = repo.create_base(
        name="报告库",
        description="test",
        space_type="team",
        owner_user_id="u-admin",
        department_id=None,
        embedding_model="",
        status="active",
    )
    docs: dict[str, str] = {}
    for record_id, publish_date, tag, source in (
        ("101", "2026-10-01", "公告", "公文系统"),
        ("102", "2026-10-03", "研报", "公文系统"),
        ("103", "2026-10-05", "公告", "邮件"),
        ("104", "2026-10-06", "公告", "公文系统"),
        ("105", None, "公告", "公文系统"),
    ):
        row = repo.create_document(
            base_id=str(base["id"]),
            folder_id=None,
            name=f"{record_id}.pdf",
            content_type="application/pdf",
            size_bytes=10,
            created_by="u-admin",
            status="ready",
            source="source_sync",
            source_record_id=record_id,
            report_title=f"报告{record_id}",
            report_tag=tag,
            report_second_source=source,
            publish_date=publish_date,
        )
        docs[record_id] = str(row["id"])
    return str(base["id"]), docs


def test_list_documents_applies_metadata_filters(db):
    repo = KnowledgeRepository(db)
    base_id, _ = _create_base_and_documents(repo)

    rows = repo.list_documents(
        base_id,
        publish_date_from="2026-10-01",
        publish_date_to="2026-10-05",
        limit=50,
    )
    assert sorted(item["source_record_id"] for item in rows) == ["101", "102", "103"]

    rows = repo.list_documents(base_id, report_tags=["公告"], limit=50)
    assert sorted(item["source_record_id"] for item in rows) == ["101", "103", "104", "105"]

    rows = repo.list_documents(base_id, report_second_sources=["邮件"], limit=50)
    assert [item["source_record_id"] for item in rows] == ["103"]

    rows = repo.list_documents(base_id, report_title_contains="报告10", limit=50)
    assert len(rows) == 5


def test_publish_date_filter_excludes_null_dates(db):
    repo = KnowledgeRepository(db)
    base_id, _ = _create_base_and_documents(repo)
    rows = repo.list_documents(
        base_id,
        publish_date_from="2026-01-01",
        publish_date_to="2026-12-31",
        limit=50,
    )
    assert "105" not in {item["source_record_id"] for item in rows}


def test_source_record_lookup_and_sync_item_idempotent(db):
    repo = KnowledgeRepository(db)
    _, docs = _create_base_and_documents(repo)
    found = repo.find_document_by_source_record("source_sync", "102")
    assert found is not None and str(found["id"]) == docs["102"]

    first = repo.create_or_get_sync_item(
        source="source_sync", source_record_id="102", publish_date="2026-10-03"
    )
    second = repo.create_or_get_sync_item(
        source="source_sync", source_record_id="102", publish_date="2026-10-03"
    )
    assert first["id"] == second["id"]
    assert repo.count_sync_items(source="source_sync") == {"queued": 1}


def test_source_record_unique_allows_reupload_into_new_base(db):
    """软删除行仍占同库幂等键，但不能阻止新库重新入库（旧库删库后重跑同步）。"""
    repo = KnowledgeRepository(db)
    old_base_id, docs = _create_base_and_documents(repo)
    repo.update_document(
        docs["101"], status="deleted", deleted_at="2026-10-23T00:00:00+00:00"
    )

    # 默认查询不出软删除行；同库内显式包含软删除时才能查到
    assert repo.find_document_by_source_record("source_sync", "101") is None
    found = repo.find_document_by_source_record(
        "source_sync", "101", base_id=old_base_id, include_deleted=True
    )
    assert found is not None and str(found["id"]) == docs["101"]

    new_base = repo.create_base(
        name="报告库2",
        description="test",
        space_type="team",
        owner_user_id="u-admin",
        department_id=None,
        embedding_model="",
        status="active",
    )
    # 幂等键含水 base_id：同一业务记录可重新入库到新库
    new_doc = repo.create_document(
        base_id=str(new_base["id"]),
        folder_id=None,
        name="101.pdf",
        content_type="application/pdf",
        size_bytes=10,
        created_by="u-admin",
        status="pending",
        source="source_sync",
        source_record_id="101",
        publish_date="2026-10-01",
    )
    assert str(new_doc["base_id"]) == str(new_base["id"])
    assert (
        repo.find_document_by_source_record(
            "source_sync", "101", base_id=str(new_base["id"])
        )
        is not None
    )
    # 旧库的软删除行仍保留（溯源 + 同库跳过判定）
    assert (
        repo.find_document_by_source_record(
            "source_sync", "101", base_id=old_base_id, include_deleted=True
        )
        is not None
    )
