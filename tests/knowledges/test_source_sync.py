"""文档 SQL 同步与元数据过滤单元测试（不依赖外部 MySQL / RAGFlow）。"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from easy_agent.knowledges.core.config import KnowledgeConfig, SourceSyncConfig
from easy_agent.knowledges.core.models import DocumentMetadataFilter, KnowledgePrincipal
from easy_agent.knowledges.core.repository import KnowledgeRepository
from easy_agent.knowledges.runtime.source_sync import (
    SourceSyncEngine,
    _iter_windows,
    build_source_query,
    build_staged_name,
    build_upload_name,
    cleanup_stale_staging_files,
    copy_to_staging,
    date_folder_segments,
    ensure_staging_dir,
    fetch_source_rows,
    parse_publish_date,
    parse_source_row,
    resolve_batchid_file,
    resolve_staging_dir,
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


def test_parse_source_row_normalizes_fields():
    row = parse_source_row(
        {
            "id": 101,
            "publishDate": "2026-10-21 08:30:00",
            "batchid": " a.pdf ",
            "reportTitle": "报告",
            "reportTag": " 公告 ",
            "reportSecondSource": " 研究部 ",
        }
    )
    assert row.record_id == "101"
    assert row.publish_date == "2026-10-21"
    assert row.batch_id == "a.pdf"
    assert row.title == "报告"
    assert row.report_tag == "公告"
    assert row.second_source == "研究部"
    assert parse_source_row({"id": None}).record_id == ""


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


def test_source_table_required_and_validated():
    # 未配置表名：直接报错
    with pytest.raises(RuntimeError):
        resolve_table_name(SourceSyncConfig())

    config = SourceSyncConfig(mysql={"table": "my_reports"})
    assert resolve_table_name(config) == "my_reports"

    query = build_source_query("my_reports")
    assert "FROM `my_reports`" in query
    assert "batchid" in query and "attacUrl" not in query
    assert "%(start)s" in query and "%(end)s" in query


def test_source_sync_start_delay_validates_non_negative():
    assert SourceSyncConfig().start_delay_minutes == 0
    assert SourceSyncConfig(start_delay_minutes=5).start_delay_minutes == 5
    with pytest.raises(ValidationError):
        SourceSyncConfig(start_delay_minutes=-1)


def test_source_table_name_validated():
    config = SourceSyncConfig(mysql={"table": "my_reports"})
    assert resolve_table_name(config) == "my_reports"

    with pytest.raises(RuntimeError):
        resolve_table_name(SourceSyncConfig(mysql={"table": "bad-name;"}))


def test_resolve_batchid_file_matches_disk_name(tmp_path):
    config = SourceSyncConfig(file_root=str(tmp_path))
    # batchid 自带扩展名：直接命中
    (tmp_path / "batch-001.pdf").write_bytes(b"x")
    assert resolve_batchid_file(config, "batch-001.pdf") == tmp_path / "batch-001.pdf"
    # batchid 不带扩展名：补扩展名匹配
    assert resolve_batchid_file(config, "batch-001") == tmp_path / "batch-001.pdf"
    # batchid 唯一：匹配不到就是磁盘上没有，不用其它字段兜底
    assert resolve_batchid_file(config, "batch-404") is None
    assert resolve_batchid_file(config, "") is None
    assert resolve_batchid_file(SourceSyncConfig(), "batch-001") is None


def test_resolve_batchid_file_rejects_relative_root(tmp_path):
    config = SourceSyncConfig(file_root="relative/root")
    with pytest.raises(RuntimeError):
        resolve_batchid_file(config, "batch-001")


# ---------------------------------------------------------------------------
# 暂存目录：配置 / 创建 / 拷贝 / 清理
# ---------------------------------------------------------------------------
def _staging_config(tmp_path) -> SourceSyncConfig:
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    return SourceSyncConfig(
        file_root=str(source),
        staging_dir=str(tmp_path / "staging"),
        mysql={"table": "my_reports"},
    )


def test_resolve_staging_dir_is_optional_and_validated(tmp_path):
    assert resolve_staging_dir(SourceSyncConfig()) is None

    with pytest.raises(ValidationError):
        SourceSyncConfig(staging_dir="relative/staging")

    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValidationError):
        SourceSyncConfig(file_root=str(source), staging_dir=str(source))

    config = _staging_config(tmp_path)
    assert resolve_staging_dir(config) == tmp_path / "staging"


def test_ensure_and_copy_to_staging(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    source_file = source / "batch-001.pdf"
    source_file.write_bytes(b"report")
    staging = tmp_path / "staging" / "nested"
    staged_file = staging / build_staged_name("报告", "101", source_file)

    assert build_staged_name("报告", "101", source_file) == "报告_101.pdf"
    ensure_staging_dir(staging)
    assert staging.is_dir()

    # 源文件 mtime 很旧，但暂存副本的 mtime 必须是“现在”，否则 TTL 清理会误删
    old_time = time.time() - 48 * 3600
    os.utime(source_file, (old_time, old_time))
    assert copy_to_staging(source_file, staged_file) is True
    assert staged_file.read_bytes() == b"report"
    assert source_file.read_bytes() == b"report"  # 源文件不动
    assert time.time() - staged_file.stat().st_mtime < 60
    # 同尺寸副本已存在：下一轮直接复用，不再重复拷贝
    assert copy_to_staging(source_file, staged_file) is False


def test_cleanup_stale_staging_files_keeps_failure_names(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    old_file = staging / "old.pdf"
    kept_failure = staging / "报告_101.pdf"
    recent_file = staging / "recent.pdf"
    old_file.write_bytes(b"old")
    kept_failure.write_bytes(b"failed")
    recent_file.write_bytes(b"recent")
    old_time = time.time() - 25 * 3600
    os.utime(old_file, (old_time, old_time))
    os.utime(kept_failure, (old_time, old_time))

    removed = cleanup_stale_staging_files(
        staging, retention_hours=24, keep_names={kept_failure.name}
    )
    assert removed == 1
    assert not old_file.exists()
    assert kept_failure.exists()  # 失败文档的暂存副本保留
    assert recent_file.exists()


async def test_stage_rows_batch_copies_and_reports_missing(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "batch-001.pdf").write_bytes(b"report")
    config = _staging_config(tmp_path)
    engine = SourceSyncEngine(
        config=KnowledgeConfig(),
        sync_config=config,
        repository=object(),  # stage_rows 不访问仓储
        service=object(),
        principal=KnowledgePrincipal(
            user_id="u-admin", username="admin", department_id=None
        ),
    )

    rows = [
        parse_source_row(
            {"id": "101", "batchid": "batch-001", "reportTitle": "报告"}
        )
    ]

    await engine.prepare_staging()
    staged, errors, stage_stats = await engine.stage_rows(rows)
    assert not errors
    assert staged["101"].name == "报告_101.pdf"
    assert staged["101"].is_file()
    assert stage_stats == {"copied": 1, "reused": 0, "copy_failed": 0}

    staged_again, _, stage_stats_again = await engine.stage_rows(rows)
    assert staged_again["101"].is_file()
    assert stage_stats_again["reused"] == 1

    _, missing_errors, _ = await engine.stage_rows(
        [parse_source_row({"id": "102", "batchid": "batch-404", "reportTitle": "x"})]
    )
    assert "batchid 未匹配到磁盘文件" in missing_errors["102"]


class _FakeRefreshRepo:
    """refresh_inflight 用到的仓储方法：查文档 + 更新台账。"""

    def __init__(self, document: dict):
        self.document = document
        self.updates: list[tuple[str, dict]] = []

    def get_document(self, document_id: str):
        return dict(self.document)

    def update_sync_item(self, item_id: str, **fields):
        self.updates.append((item_id, fields))


def _refresh_engine(tmp_path, document: dict, *, retention_hours: int = 24):
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    config = SourceSyncConfig(
        file_root=str(tmp_path / "source"),
        staging_dir=str(staging),
        max_attempts=1,
        staging_retention_hours=retention_hours,
        mysql={"table": "my_reports"},
    )
    repo = _FakeRefreshRepo(document)
    engine = SourceSyncEngine(
        config=KnowledgeConfig(),
        sync_config=config,
        repository=repo,
        service=object(),
        principal=KnowledgePrincipal(
            user_id="u-admin", username="admin", department_id=None
        ),
    )
    return engine, repo, staging


async def test_refresh_inflight_ready_deletes_staged_file(tmp_path):
    engine, repo, staging = _refresh_engine(
        tmp_path,
        {
            "id": "doc-1",
            "base_id": "base-1",
            "status": "ready",
            "name": "报告_101.pdf",
            "remote_document_id": "remote-1",
        },
    )
    staged_file = staging / "报告_101.pdf"
    staged_file.write_bytes(b"report")

    await engine.prepare_staging()
    remaining, stats = await engine.refresh_inflight(
        [
            {
                "id": "item-1",
                "source_record_id": "101",
                "document_id": "doc-1",
                "attempts": 0,
            }
        ],
        base_id="base-1",
    )
    assert remaining == []
    assert stats["ready"] == 1
    assert stats["staged_deleted"] == 1
    assert not staged_file.exists()
    assert repo.updates[-1][1]["status"] == "ready"


async def test_refresh_inflight_failure_keeps_staged_file(tmp_path):
    engine, _, staging = _refresh_engine(
        tmp_path,
        {
            "id": "doc-1",
            "base_id": "base-1",
            "status": "failed",
            "name": "报告_101.pdf",
            "error_message": "parse failed",
        },
    )
    staged_file = staging / "报告_101.pdf"
    staged_file.write_bytes(b"report")

    await engine.prepare_staging()
    _, stats = await engine.refresh_inflight(
        [
            {
                "id": "item-1",
                "source_record_id": "101",
                "document_id": "doc-1",
                "attempts": 1,  # max_attempts=1，已用完，不再重试
            }
        ],
        base_id="base-1",
    )
    assert stats["failed"] == 1
    assert staged_file.exists()  # 失败保留，便于排查/后续人工处理


class _FakeUploadService:
    def __init__(self):
        self.uploaded_bytes: bytes | None = None
        self.filename: str | None = None

    async def upload_document(self, **kwargs):
        self.filename = kwargs["filename"]
        self.uploaded_bytes = kwargs["content"].read()
        return SimpleNamespace(
            document=SimpleNamespace(id="doc-1", name=self.filename, status="parsing")
        )


class _FakeUploadRepo:
    def __init__(self):
        self.items: dict[str, dict] = {}
        self.updates: list[tuple[str, dict]] = []

    def find_document_by_source_record(self, *args, **kwargs):
        return None

    def create_or_get_sync_item(
        self, *, source: str, source_record_id: str, publish_date: str | None = None
    ):
        item = self.items.setdefault(
            source_record_id,
            {
                "id": "item-1",
                "source_record_id": source_record_id,
                "status": "queued",
                "attempts": 0,
                "document_id": None,
            },
        )
        return dict(item)

    def update_sync_item(self, item_id: str, **fields):
        self.updates.append((item_id, fields))
        for item in self.items.values():
            if item["id"] == item_id:
                item.update(fields)

    def get_sync_item(self, source: str, source_record_id: str):
        item = self.items.get(source_record_id)
        return dict(item) if item is not None else None


async def test_upload_row_reads_from_staged_file(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "batch-001.pdf").write_bytes(b"source-bytes")
    staging = tmp_path / "staging"
    staging.mkdir()
    staged_file = staging / "报告_101.pdf"
    staged_file.write_bytes(b"staged-bytes")

    service = _FakeUploadService()
    repo = _FakeUploadRepo()
    config = SourceSyncConfig(
        file_root=str(source),
        staging_dir=str(staging),
        mysql={"table": "my_reports"},
    )
    engine = SourceSyncEngine(
        config=KnowledgeConfig(),
        sync_config=config,
        repository=repo,
        service=service,
        principal=KnowledgePrincipal(
            user_id="u-admin", username="admin", department_id=None
        ),
    )

    async def _fake_ensure_date_folder(base_id: str, publish_date: str):
        return "folder-1"

    engine.ensure_date_folder = _fake_ensure_date_folder  # type: ignore[method-assign]

    await engine.prepare_staging()
    item = await engine._upload_row(
        base_id="base-1",
        row=parse_source_row(
            {
                "id": "101",
                "publishDate": "2026-10-03",
                "batchid": "batch-001",
                "reportTitle": "报告",
            }
        ),
        staged_file=staged_file,
    )
    assert item is not None
    assert service.filename == "报告_101.pdf"
    assert service.uploaded_bytes == b"staged-bytes"
    assert item["status"] == "parsing"


def test_fetch_source_rows_requires_connection_config():
    now = datetime.now(UTC)
    # 表名必填：留空直接报错
    with pytest.raises(RuntimeError, match="表名未配置"):
        fetch_source_rows(SourceSyncConfig(), now, now)
    # 配了表名但缺 host/database：报配置不完整
    with pytest.raises(RuntimeError, match="配置不完整"):
        fetch_source_rows(
            SourceSyncConfig(mysql={"table": "my_reports"}), now, now
        )


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
        batch_id="b-101",
        publish_date="2026-10-01",
    )
    assert str(new_doc["base_id"]) == str(new_base["id"])
    assert new_doc["batch_id"] == "b-101"
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
