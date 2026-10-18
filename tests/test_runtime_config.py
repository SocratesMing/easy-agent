"""运行期配置持有者测试（ARCH-03：config 不再反向依赖 services）。"""

from types import SimpleNamespace

from easy_agent.bootstrap import runtime_config
from easy_agent.bootstrap.config import Config


def _cfg(tmp_path):
    return SimpleNamespace(
        agent=SimpleNamespace(
            workspace_dir=str(tmp_path / "ws"),
            sessions_dir=str(tmp_path / "sessions"),
            memories_dir=str(tmp_path / "memories"),
        )
    )


def test_config_helpers_use_runtime_config(tmp_path):
    runtime_config.set_runtime_agent_config({"config": _cfg(tmp_path)})
    try:
        assert Config.get_user_workspace_dir("alice") == tmp_path / "ws" / "alice"
        assert Config.get_user_sessions_dir("alice") == tmp_path / "sessions" / "alice"
        assert Config.get_user_memories_dir("alice") == tmp_path / "memories" / "alice"
        assert Config.get_user_mcp_path("alice").name == "mcp.json"
    finally:
        runtime_config.reset_runtime_agent_config()


def test_explicit_config_still_wins(tmp_path):
    runtime_config.set_runtime_agent_config({"config": _cfg(tmp_path / "runtime")})
    try:
        explicit = _cfg(tmp_path / "explicit")
        assert (
            Config.get_user_workspace_dir("bob", explicit)
            == tmp_path / "explicit" / "ws" / "bob"
        )
    finally:
        runtime_config.reset_runtime_agent_config()


def test_agent_invalidator_registry():
    from easy_agent.bootstrap import runtime_config

    calls = []
    runtime_config.set_agent_invalidator(lambda username: calls.append(username) or 7)
    try:
        assert runtime_config.invalidate_user_agents("alice") == 7
        assert calls == ["alice"]
    finally:
        runtime_config.set_agent_invalidator(None)
        assert runtime_config.invalidate_user_agents("bob") == 0
