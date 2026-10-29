"""知识库轮询 worker 的 leader 选举测试（PERF-14）。"""

from types import SimpleNamespace

from easy_agent.knowledges.runtime import worker as worker_mod
from easy_agent.knowledges.runtime.worker import KnowledgeParsePollWorker


class _Handle:
    def __init__(self, acquired: bool):
        self.acquired = acquired
        self.released = False

    def release(self):
        self.released = True


class _Lock:
    def __init__(self, acquired: bool):
        self.handle = _Handle(acquired)
        self.calls = []

    def acquire(self, name, ttl_seconds=None):
        self.calls.append((name, ttl_seconds))
        return self.handle


class _Repo:
    def __init__(self, db):
        self.db = db

    def get_base(self, base_id):
        return SimpleNamespace(base_id=base_id)


class _Service:
    def __init__(self, *args, **kwargs):
        self.refreshed = []

    async def refresh_base_documents(self, base):
        self.refreshed.append(base.base_id)


def _make_worker(lock):
    service = _Service()

    def fake_repo(db):
        return _Repo(db)

    def fake_service(*args, **kwargs):
        return service

    worker = KnowledgeParsePollWorker(
        config=SimpleNamespace(),
        ragflow=object(),
        db_provider=lambda: object(),
        poll_interval_seconds=5.0,
        lock_provider=lambda: lock,
    )
    worker._pending_base_ids = lambda db: ["b1"]
    worker_mod.KnowledgeRepository = fake_repo
    worker_mod.KnowledgeService = fake_service
    return worker, service


async def test_non_leader_skips_poll(monkeypatch):
    lock = _Lock(acquired=False)
    worker, service = _make_worker(lock)
    monkeypatch.setattr(worker_mod, "KnowledgeRepository", lambda db: _Repo(db))
    monkeypatch.setattr(worker_mod, "KnowledgeService", lambda *a, **k: service)
    assert await worker.poll_once() is False
    assert service.refreshed == []
    assert lock.handle.released is True
    assert lock.calls and lock.calls[0][0] == "knowledge:parse-poll"


async def test_leader_polls_and_releases(monkeypatch):
    lock = _Lock(acquired=True)
    worker, service = _make_worker(lock)
    monkeypatch.setattr(worker_mod, "KnowledgeRepository", lambda db: _Repo(db))
    monkeypatch.setattr(worker_mod, "KnowledgeService", lambda *a, **k: service)
    assert await worker.poll_once() is True
    assert service.refreshed == ["b1"]
    assert lock.handle.released is True
