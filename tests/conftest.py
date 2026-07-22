"""pytest 公共夹具。

策略：
- 通过 EASY_CONFIG 指向 tests/test_config.yaml，并用环境变量将数据库/工作区/记忆/技能等
  路径重定向到临时目录，避免依赖宿主机真实配置与外部服务。
- 每个测试使用独立的内存 SQLite 数据库（:memory:），保证隔离。
- 固定当前用户名为 "testuser"，绕过 JWT 校验，方便接口测试；
  需要验证真实鉴权的用例可在测试内临时移除 get_current_username 的 override。
- 将 Config.get_user_workspace_dir 与 files 路由的模块级基目录重定向到临时目录，
  避免对仓库产生副作用。
"""

import os
import tempfile
import uuid
from pathlib import Path

import pytest

# 手动演示脚本 / 陈旧单测，不纳入接口与业务场景测试套件：
# - test_v3_streaming.py：需真实 config.yaml + 联网调用 LLM，模块级 test_* 协程会被收集并执行 sys.exit(1)
# - test_basic.py：引用了已移除的模块（easy_agent.web、Colors 等），在当前代码库下恒失败
collect_ignore = ["test_v3_streaming.py", "test_basic.py"]

TEST_ROOT = Path(__file__).parent
_TMP = Path(tempfile.mkdtemp(prefix="easy_agent_test_"))

# 在导入 app 之前设置环境变量，确保 lifespan 加载测试配置成功
os.environ["EASY_JWT_SECRET"] = "test-secret-for-easy-agent-tests"
os.environ["EASY_CONFIG"] = str(TEST_ROOT / "test_config.yaml")
os.environ["TEST_WORKSPACE_DIR"] = str(_TMP / "workspace")
os.environ["TEST_MEMORIES_DIR"] = str(_TMP / "memories")
os.environ["TEST_LOG_DIR"] = str(_TMP / "logs")
os.environ["TEST_SESSIONS_DIR"] = str(_TMP / "sessions")
os.environ["TEST_SKILLS_DIR"] = str(_TMP / "skills")
os.environ["TEST_PROMPTS_DIR"] = str(_TMP / "prompts")
os.environ["TEST_UPLOADS_DIR"] = str(_TMP / "uploads")
os.environ["TEST_DB_PATH"] = str(_TMP / "easy_agent.db")
os.environ["TEST_SYSTEM_PROMPT"] = str(TEST_ROOT / "system_prompt.md")

for _d in ("workspace", "memories", "logs", "sessions", "skills", "prompts", "uploads"):
    (_TMP / _d).mkdir(parents=True, exist_ok=True)

from fastapi.testclient import TestClient  # noqa: E402

from easy_agent.app import app  # noqa: E402
from easy_agent.db import get_database, init_database  # noqa: E402
from easy_agent.middleware import get_current_username  # noqa: E402
import easy_agent.db as _db_mod  # noqa: E402
import easy_agent.db.database as _db_impl  # noqa: E402
import easy_agent.config as _config_mod  # noqa: E402
import easy_agent.api.files as _files_mod  # noqa: E402


@pytest.fixture()
def db():
    """每个测试独立的临时文件 SQLite 数据库（:memory: 跨连接不共享，故用文件）。"""
    path = _TMP / f"test_{uuid.uuid4().hex}.db"
    return init_database({"type": "sqlite", "sqlite": {"path": str(path)}})


@pytest.fixture()
def client(db, monkeypatch):
    # 将工作区相关路径重定向到临时目录
    monkeypatch.setattr(
        _config_mod.Config,
        "get_user_workspace_dir",
        staticmethod(
            lambda username: Path(os.environ["TEST_WORKSPACE_DIR"])
            / "users"
            / username
        ),
    )
    monkeypatch.setattr(_files_mod, "BASE_UPLOAD_DIR", Path(os.environ["TEST_UPLOADS_DIR"]))
    monkeypatch.setattr(_files_mod, "BASE_WORKSPACE_DIR", Path(os.environ["TEST_WORKSPACE_DIR"]))

    # mock 模型创建，避免 lifespan / bloom / forex 等处的 LLM 调用发起真实网络请求。
    # 多个模块各自 import 了 create_model，需要逐个覆盖。
    class _FakeMsg:
        content = "mock-response"

    from langchain_openai import ChatOpenAI
    from langchain_core.messages import AIMessage, AIMessageChunk
    from langchain_core.outputs import ChatGeneration, ChatResult

    class _FakeLLM(ChatOpenAI):
        """避免联网的假 LLM：继承真实 ChatOpenAI，使其被 deepagents 识别为
        BaseChatModel 实例（resolve_model 对 BaseChatModel 直接返回，不会把实例当成
        模型 spec 字符串去查 ProviderProfile），同时覆写生成方法返回固定内容。"""

        def __init__(self, **kwargs):
            kwargs.setdefault("model_name", "mock-model")
            kwargs.setdefault("api_key", "dummy")
            kwargs.setdefault("base_url", "http://127.0.0.1:1")
            super().__init__(**kwargs)

        @property
        def _llm_type(self):
            return "fake"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content="mock-response"))]
            )

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content="mock-response"))]
            )

        def stream(self, messages, *args, **kwargs):
            yield AIMessageChunk(content="mock-response")

        async def astream(self, messages, *args, **kwargs):
            yield AIMessageChunk(content="mock-response")

        def count(self, *args, **kwargs):
            return 0

    import easy_agent.agent as _agent_mod
    import easy_agent.api.bloom as _bloom_mod
    import easy_agent.api.forex as _forex_mod
    import easy_agent.app as _app_mod
    import easy_agent.model as _model_mod
    import easy_agent.services.agent_manager as _am_mod
    import easy_agent.services.streaming as _stream_mod

    for _m in (
        _model_mod,
        _app_mod,
        _am_mod,
        _stream_mod,
        _agent_mod,
        _bloom_mod,
        _forex_mod,
    ):
        if hasattr(_m, "create_model"):
            monkeypatch.setattr(_m, "create_model", lambda *a, **k: _FakeLLM())

    with TestClient(app) as c:
        # lifespan 已加载配置并把全局 DB 指向文件库；此处重定向为临时文件库，保证隔离。
        # 注意：_db_instance 定义在 easy_agent.db.database 模块（easy_agent.db 只是 re-export），
        # 必须直接修改 database 模块的属性才对 get_database() 生效。
        _db_impl._db_instance = db
        app.dependency_overrides[get_database] = lambda: db
        app.dependency_overrides[get_current_username] = lambda: "testuser"
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def auth_client(client):
    """移除 get_current_username 的 override，使用真实 JWT 鉴权。"""
    app.dependency_overrides.pop(get_current_username, None)
    return client
