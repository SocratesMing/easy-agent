"""T14.2：公共技能清单的按用户文件锁（并发读改写不丢更新）。

``builtin-skills.json`` 是「读 → 改 → 原子写」的 JSON 文件；并发 ``add`` / ``set_enabled``
/ ``remove`` 不加锁会丢更新（两个请求读到同一份旧清单，后写覆盖先写）。这里用线程 /
协程并发 + 人为放大读改写窗口来验证临界区互斥，并验证锁文件落盘、同线程可重入
（``add`` 里的首次迁移会嵌套取锁，不能死锁）。
"""

from __future__ import annotations

import asyncio
import threading
import time
import types
from pathlib import Path

import pytest

from easy_agent.agent import builtin_skills as store


def _skill(root: Path, name: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} skill\n---\n", encoding="utf-8"
    )
    return skill_dir


@pytest.fixture()
def env(tmp_path):
    canonical = tmp_path / "skills"
    for name in ("docx", "pdf", "xlsx"):
        _skill(canonical, name)
    config = types.SimpleNamespace(
        agent=types.SimpleNamespace(workspace_dir=str(tmp_path / "workspace")),
        tools=types.SimpleNamespace(skills_dir=str(canonical)),
    )
    return types.SimpleNamespace(
        tmp=tmp_path,
        root=canonical,
        config=config,
        user=tmp_path / "workspace" / "alice",
    )


def _slow_load(monkeypatch):
    """放大「读 → 改 → 写」窗口：没有锁时所有线程都会基于同一份旧清单写回。"""
    original = store.load_store

    def slow(username, config=None):
        result = original(username, config)
        time.sleep(0.05)
        return result

    monkeypatch.setattr(store, "load_store", slow)


def _run_threads(*targets) -> list[BaseException]:
    errors: list[BaseException] = []

    def wrap(target):
        def runner():
            try:
                target()
            except BaseException as exc:  # noqa: BLE001 - 收集后统一断言
                errors.append(exc)

        return runner

    threads = [threading.Thread(target=wrap(target)) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    return errors


def test_store_lock_file_is_created_and_nested(env):
    """临界区落锁文件；同一线程嵌套持锁不自我阻塞（add 内部的迁移路径）。"""
    with store.store_lock("alice", env.config):
        assert (env.user / f".{store.STORE_FILE_NAME}.lock").is_file()
        with store.store_lock("alice", env.config):
            store.add("alice", "docx", env.root, env.config)

    assert store.enabled_names("alice", env.root, env.config) == ("docx",)


def test_concurrent_adds_do_not_lose_updates(env, monkeypatch):
    """三个线程同时添加不同技能：最终清单三个都在（没有丢更新）。"""
    _slow_load(monkeypatch)

    errors = _run_threads(
        *(lambda name=name: store.add("alice", name, env.root, env.config)
          for name in ("docx", "pdf", "xlsx"))
    )

    assert errors == []
    assert store.enabled_names("alice", env.root, env.config) == ("docx", "pdf", "xlsx")


def test_concurrent_enable_and_remove_do_not_lose_updates(env, monkeypatch):
    """添加后并发「停用 docx」与「移除 pdf」：两个修改都生效，不互相覆盖。"""
    for name in ("docx", "pdf", "xlsx"):
        store.add("alice", name, env.root, env.config)
    _slow_load(monkeypatch)

    errors = _run_threads(
        lambda: store.set_enabled("alice", "docx", False, env.config),
        lambda: store.remove("alice", "pdf", env.config),
    )

    assert errors == []
    state = {
        item["dir_name"]: item["enabled"]
        for item in store.list_added("alice", env.root, env.config)
    }
    assert state == {"docx": False, "xlsx": True}


async def test_concurrent_adds_from_coroutines_do_not_lose_updates(env):
    """协程经 ``asyncio.to_thread`` 并发添加：多副本/流式请求的真实并发形态，同样不丢更新。"""
    await asyncio.gather(
        *(
            asyncio.to_thread(store.add, "alice", name, env.root, env.config)
            for name in ("docx", "pdf", "xlsx")
        )
    )

    assert store.enabled_names("alice", env.root, env.config) == ("docx", "pdf", "xlsx")
