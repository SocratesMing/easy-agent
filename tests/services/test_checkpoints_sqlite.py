"""官方 AsyncSqliteSaver 接入测试（FW-09 单机默认路径）。"""

import pytest

from easy_agent.persistence import checkpoints as ck


@pytest.mark.asyncio
async def test_open_and_close_official_sqlite_saver(tmp_path):
    await ck.close_sqlite_checkpointer()
    assert ck.sqlite_checkpointer_active() is False
    try:
        saver = await ck.open_sqlite_checkpointer(str(tmp_path / "checkpoints.db"))
        assert ck.sqlite_checkpointer_active() is True
        assert ck.active_checkpoint_backend() == ck.BACKEND_SQLITE
        assert ck.get_session_checkpointer("session-1") is saver
        assert ck.get_session_checkpointer("session-2") is saver
    finally:
        await ck.close_sqlite_checkpointer()
    assert ck.sqlite_checkpointer_active() is False
    assert ck.active_checkpoint_backend() == ck.BACKEND_LEGACY


@pytest.mark.asyncio
async def test_sqlite_saver_is_reused_until_closed(tmp_path):
    path = tmp_path / "reused.db"
    first = await ck.open_sqlite_checkpointer(str(path))
    try:
        assert await ck.open_sqlite_checkpointer(str(path)) is first
        assert path.exists()
    finally:
        await ck.shutdown_checkpointers()
    assert ck.sqlite_checkpointer_active() is False
    assert ck.active_checkpoint_backend() == ck.BACKEND_LEGACY


@pytest.mark.asyncio
async def test_sqlite_saver_uses_wal_with_normal_sync(tmp_path):
    """WAL + synchronous=NORMAL：提交不再每次等磁盘落盘（单文件压测吞吐约 +70%），崩溃时库不损坏。"""
    await ck.close_sqlite_checkpointer()
    try:
        saver = await ck.open_sqlite_checkpointer(str(tmp_path / "sync.db"))
        async with saver.conn.execute("PRAGMA journal_mode") as cur:
            assert (await cur.fetchone())[0] == "wal"
        async with saver.conn.execute("PRAGMA synchronous") as cur:
            assert (await cur.fetchone())[0] == 1  # 0=OFF 1=NORMAL 2=FULL（SQLite 默认 FULL）
    finally:
        await ck.close_sqlite_checkpointer()


@pytest.mark.asyncio
async def test_shutdown_clears_legacy_session_cache():
    first = ck.get_session_checkpointer("legacy-session")
    assert isinstance(first, ck.PersistentMemorySaver)
    try:
        await ck.shutdown_checkpointers()
        second = ck.get_session_checkpointer("legacy-session")
        assert second is not first
        assert isinstance(second, ck.PersistentMemorySaver)
    finally:
        await ck.shutdown_checkpointers()


def test_relative_sqlite_path_is_resolved_against_project_root():
    resolved = ck.resolve_sqlite_path(ck.DEFAULT_SQLITE_PATH)
    assert resolved.is_absolute()
    assert resolved.name == "checkpoints.db"
    assert resolved.parent.name == "data"
