"""Phase 1 spike：官方 StoreBackend 作为 /memories/ 目标后端。

不切换默认路由（默认仍 LocalShellBackend/RWX）；只验证 0.7 的
StoreBackend + BaseStore 能完成文件读写/列表/删除，为后续外置记忆铺路。
"""

from langgraph.store.memory import InMemoryStore
from deepagents.backends import StoreBackend


def _backend():
    return StoreBackend(
        store=InMemoryStore(),
        namespace=lambda _runtime: ("u1", "memories"),
    )


def test_store_backend_roundtrip_outside_graph():
    backend = _backend()

    result = backend.write("/AGENTS.md", "# 长期记忆\n\n- 用户偏好中文\n")
    assert result.error is None

    read = backend.read("/AGENTS.md")
    assert read.error is None
    assert read.file_data is not None
    assert "用户偏好中文" in read.file_data["content"]

    listed = backend.ls("/")
    paths = [item["path"] for item in listed.entries]
    assert "/AGENTS.md" in paths

    edited = backend.edit("/AGENTS.md", "用户偏好中文", "用户偏好英文")
    assert edited.error is None
    assert "用户偏好英文" in backend.read("/AGENTS.md").file_data["content"]

    deleted = backend.delete("/AGENTS.md")
    assert deleted.error is None
    assert backend.read("/AGENTS.md").error is not None
