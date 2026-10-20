"""Core Agent implementation using DeepAgents framework (deepagents 0.7).

``create_deep_agent`` adds these tools automatically:
- ls, read_file, write_file, edit_file, glob, grep, delete: filesystem operations
- execute: shell command execution (sandbox backends)
- task: subagent spawning

``write_todos`` is no longer built in since 0.7: ``agent/middleware.py`` adds
``TodoListMiddleware`` explicitly.
"""

import json
import logging
import shutil
import time
from pathlib import Path

from deepagents import create_deep_agent
from langchain_core.messages import AIMessageChunk, ToolMessage

from easy_agent.backends.shell import _PathTranslatingShell  # noqa: F401  旧导入路径兼容（测试/外部直接 import）
from easy_agent.backends.context import ContextBackend
from easy_agent.persistence.checkpoints import get_session_checkpointer, release_session_checkpointer
from easy_agent.bootstrap.config import Config
from easy_agent.agent.hitl import (
    EXECUTE_HITL_TOOL_DESCRIPTION,
    HITL_SYSTEM_NOTE,
    HITL_WHEN_PREDICATES,
    build_interrupt_config,
    execute_description_installed,
    register_execute_description_profiles,
)
from easy_agent.persistence.stores import get_agent_store
from easy_agent.agent.graph_cache import (
    SessionContext,
    build_graph_key,
    config_fingerprint,
    contextualize_tools,
    get_agent_graph_cache,
    graph_cache_enabled,
    model_fingerprint,
    summarization_fingerprint,
)
from easy_agent.agent.backend import _build_backend_for_context
from easy_agent.agent.middleware import build_agent_middleware, build_call_limit_middleware
from easy_agent.agent.workspace import (
    LONG_TERM_MEMORY_VIRTUAL_PATH,
    AgentWorkspaceMixin,
)
from easy_agent.agent.subagents import DEFAULT_SUBAGENTS, apply_skill_gated_tools, resolve_subagent_tools
from easy_agent.agent.summarization import build_summarization_middleware
from easy_agent.agent.memory import SessionMemoryMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from easy_agent.tools.registry import skill_gated_tool_names
from easy_agent.agent import builtin_skills
from easy_agent.agent.skills import declared_include_tools, skill_tool_resolver
from easy_agent.utils.mcp_tool_names import resolve_tool_names
from easy_agent.agent.builtin_skills import BUILTIN_VIRTUAL_ROOT
from easy_agent.bootstrap.logger import AgentLogger
from easy_agent.agent.model import (
    create_model,
    extract_reasoning,
    is_internal_model_call,
    resolve_context_length,
)
from easy_agent.security.prompt_guard import assemble_system_prompt
from easy_agent.tools.web_search import WEB_SEARCH_SYSTEM_PROMPT
from easy_agent.utils.prompt_probe import describe_prompt, prompt_fingerprint


logger = logging.getLogger(__name__)


def _deepagents_version() -> str:
    """当前 deepagents 版本（适配失效时的排查提示用）。"""
    try:
        from importlib.metadata import version

        return version("deepagents")
    except Exception:  # noqa: BLE001
        return "unknown"


def framework_patch_status() -> dict[str, bool]:
    """自检 execute 描述是否已通过官方 ``HarnessProfile`` 注册。

    FW-04 起不再 monkeypatch ``FilesystemMiddleware.__init__``；这里反映的是
    ``hitl.register_execute_description_profiles`` 是否已在当前进程生效。
    """
    return {"execute_tool": execute_description_installed()}


#: 注册表 ``hitl_predicate`` 名称 → 实际 ``when`` 谓词。
#: 谓词实现与绑定都在 ``hitl``（FW-12）；这里保留别名供既有自检/测试导入，
#: 两边不一致时启动期日志会报错（测试也会失败）。
_HITL_WHEN_PREDICATES = HITL_WHEN_PREDICATES


_patch_status_logged = False


def log_framework_patch_status(sid: str = "startup") -> None:
    """只打印一次框架适配体检结果；任一项失效按 ERROR 报出并说明降级后果。"""
    global _patch_status_logged
    if _patch_status_logged:
        return
    _patch_status_logged = True

    status = framework_patch_status()
    detail = " | ".join(f"{k}={'生效' if v else '失效'}" for k, v in status.items())
    if all(status.values()):
        logger.info(f"[{sid}] 🔧 deepagents {_deepagents_version()} 适配体检: {detail}")
        return

    failed = [k for k, v in status.items() if not v]
    logger.error(
        f"[{sid}] ❌ deepagents 适配失效: {failed} | {detail} | 降级后果——"
        f"execute_tool=execute 描述未通过 HarnessProfile.tool_description_overrides "
        f"注册（hitl.register_execute_description_profiles）。"
        f"当前 deepagents {_deepagents_version()}，请对照其 profiles API 修正适配代码。"
    )


def build_subagent_specs(
    session_id: str = "",
    result_log_truncate: int = 500,
    *,
    max_steps: int | None = None,
    skills_paths: list[str] | None = None,
) -> list[dict]:
    """构造传给 create_deep_agent 的 subagent specs。

    官方语义：自定义 subagent **不继承**主 agent 的 skills，且 middleware 栈
    也不会自动带上主代理的 middleware。这里显式补上：
    - skills：与主代理相同的技能源（DA-13 S2 起为 ``/skills/builtin/`` + ``/user-skills/``），
      子代理能读技能文档；缺省时只有 ``/user-skills/``；
    - ToolErrorMiddleware（DA-12）：子代理内可恢复的工具错误回灌给子代理，不中断 task；
    - 模型/工具调用上限（DA-10）：子代理图各自绑定 ``recursion_limit=9999``，
      主流程的递归上限与 call-limit 都管不到它们，只能在子代理自己的栈里限制。

    框架自动补的 ``general-purpose`` 子代理只继承「替换默认槽位」的同名中间件，
    拿不到上述 call-limit，因此按官方做法显式提供同名 spec 覆盖它（描述与提示词
    沿用 deepagents 的 ``GENERAL_PURPOSE_SUBAGENT``，工具/审批/权限照常继承主代理）。

    D2：``session_id`` 不再写进 spec（否则同一张编译图会被会话绑定、无法复用）；
    保留该参数仅为兼容既有调用方。``result_log_truncate`` 用于工具错误日志截断。
    """
    from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT

    from easy_agent.agent.tool_errors import build_tool_error_middleware

    specs: list[dict] = []
    for base in (GENERAL_PURPOSE_SUBAGENT, *DEFAULT_SUBAGENTS):
        item = dict(base)
        item.setdefault("skills", list(skills_paths or ["/user-skills/"]))
        item["middleware"] = [
            build_tool_error_middleware(log_truncate=result_log_truncate),
            *build_call_limit_middleware(max_steps, exit_behavior="end"),
        ]
        specs.append(item)
    return specs


def _shared_graph_checkpointer():
    """返回可跨会话共享的 checkpointer；legacy 按 session 的 saver 返回 None。

    D2 只复用「官方全局 saver」：``AsyncPostgresSaver`` / ``AsyncSqliteSaver``
    由进程共享，thread_id 已含 session 维度。MySQL 无官方 saver 时 checkpoints.py
    会按 session 创建 ``PersistentMemorySaver``，该对象不能塞进共享图，此时
    由调用方显式降级为按会话建图。
    """
    from easy_agent.persistence.checkpoints import postgres_checkpointer_active, sqlite_checkpointer_active

    if not (postgres_checkpointer_active() or sqlite_checkpointer_active()):
        return None
    return get_session_checkpointer("__graph_cache__")


class EasyAgent(AgentWorkspaceMixin):
    """EasyAgent wraps DeepAgents with per-user/per-session workspace isolation.

    EXECUTE_HITL_TOOL_DESCRIPTION is the public execute-tool copy injected via
    the official ``HarnessProfile.tool_description_overrides`` (FW-04).

    Args:
        config: Application configuration.
        system_prompt: Base project prompt, augmented only with runtime context.
        skills_root: Path to the skills root directory. Skills are discovered automatically.
        username: Username for workspace isolation.
        session_id: Session ID for workspace isolation.
        workspace_dir: Override workspace directory. If provided, this takes
            priority over the auto-generated path from config + username + session_id.
        workspace_name: Custom workspace directory name (e.g. '20260501_143022_a1b2c').
            If provided, used instead of session_id as the directory name.
    """

    EXECUTE_HITL_TOOL_DESCRIPTION = EXECUTE_HITL_TOOL_DESCRIPTION

    def __init__(
        self,
        config: Config,
        system_prompt: str,
        skills_root: str = "",
        username: str = "default",
        session_id: str = None,
        workspace_dir: str | Path | None = None,
        workspace_name: str = "",
        mcp_tools: list | None = None,
        organization_id: str = "",
       enable_hitl: bool = True,
       model_name: str | None = None,
        enable_web_search: bool = False,
        system_prompt_extra: str = "",
   ):
        """初始化 EasyAgent 实例，配置工作区隔离和运行时上下文。

        根据用户名和会话 ID 构建隔离的工作区目录，设置记忆文件路径，
        拼接 DeepAgents 无法自动得知的运行时上下文，最终调用
        _create_agent() 创建 DeepAgents 智能体实例。

        Args:
            config: 应用配置对象，包含 LLM、agent、tools 等配置。
            system_prompt: 基础系统提示词，将追加用户、机构、工作区与系统信息。
            skills_root: 公共技能根目录路径，当前版本仅用于参考，不自动加载。
            username: 用户名，用于工作区路径隔离和记忆文件定位。
            session_id: 会话 ID，用作工作区子目录名。
            workspace_dir: 自定义工作区目录路径。提供时优先使用，覆盖自动生成的路径。
            workspace_name: 自定义工作区目录名（如 '20260501_143022_a1b2c'）。
                提供时替代 session_id 作为目录名。
            mcp_tools: MCP 工具列表，作为额外工具注入智能体。
            organization_id: 用户所属机构ID，注册后不可更改，将注入系统提示词。
            enable_web_search: 是否开启联网搜索。为 True 时在系统提示词中加入
                联网搜索行为约束（web_search 工具本身由 agent_manager 注入，
                仅在 web_search 配置了 api_key 时才为 True）。
        """
        self.config = config
        self.username = username
        self.session_id = session_id
        # 日志用的短前缀（取 sid 末 5 位），与 chat_service 等模块保持一致。
        self.sid = (session_id or "new")[-5:]
        self.skills_root = skills_root
        self.mcp_tools = mcp_tools or []
        self.organization_id = organization_id or ""
        self.enable_hitl = enable_hitl
        self.enable_web_search = enable_web_search
        self.safe_username = Config.sanitize_username(username)
        # 注入给 shell 执行环境的变量（LocalShellBackend 默认 inherit_env=False，
        # 环境为空 dict，这里只追加用户名，不继承宿主环境以免泄露密钥等变量）
        self.shell_env = {"EASY_USERNAME": self.safe_username}
        self.model_name = model_name or config.active_model
        # D2：workspace_name 进运行期 context（知识库/工具按它解析会话目录）
        self.workspace_name = ""

        if workspace_dir:
            self.workspace_dir = Path(workspace_dir)
            dir_name = self.workspace_dir.name
        else:
            base = Path(config.agent.workspace_dir)
            if session_id:
                dir_name = workspace_name if workspace_name else session_id
                self.workspace_dir = base / self.safe_username / "session" / dir_name
            else:
                self.workspace_dir = base / self.safe_username
                dir_name = self.safe_username

        # Don't create directory eagerly - LocalShellBackend.write will create it on first write
        self.workspace_virtual_path = "/workspace"
        self._workspace_renamed = False

        # 会话级记忆文件：workspace/{username}/session/{workspace_name}/memory.md
        # 首轮对话时不创建，第一轮回复完成后自动生成
        # 第二轮及后续对话时自动加载，提供当前会话的上下文连续性
        self.memory_file = self.workspace_dir / "memory.md"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

        # 用户长期记忆文件：memories/{username}/AGENTS.md
        # 跨会话持久化，记录用户偏好与可复用经验，首轮对话时加载
        memories_base = Path(config.agent.memories_dir)
        self.long_term_memory_file = memories_base / self.safe_username / "AGENTS.md"

        # 用户技能目录: workspace/{username}/skills/（用户级，非 session 级）
        self.user_skills_dir = Path(config.agent.workspace_dir) / self.safe_username / "skills"
        # D2：技能目录是否存在于「建图时」会影响 permissions 指纹；先建出来，
        # 避免首个请求调用 backend factory 后才创建，导致同一用户第二次建图键变化。
        try:
            self.user_skills_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # noqa: BLE001
            logger.warning(f"[{self.sid}] 创建用户技能目录失败: {exc}")
        # DA-13 S2：公共技能只读共享（/skills/builtin/），只挂用户已添加且启用的那些。
        # 启用集合只进运行期 SessionContext（不进编译图键）；启停后 invalidate_user_agents 重建。
        self.builtin_skills_dir = builtin_skills.builtin_root(config)
        self.builtin_skills = (
            builtin_skills.enabled_names(self.username, self.builtin_skills_dir, config)
            if self.builtin_skills_dir
            else ()
        )
        # 目录名即会话工作区的真实名字（rename_workspace 会更新它）
        self.workspace_name = dir_name

        # D2：会话差异集中到运行期 context；编译图缓存键不包含这些字段。
        self.session_context = self._make_session_context()

        user_info = (
            f"## 当前用户\n"
            f"- 用户名: `{self.safe_username}`（技能文档与脚本中的 `{{userId}}` / `{{username}}` 均指它）\n"
            + (f"- 机构ID: `{self.organization_id}`（注册后不可更改）\n" if self.organization_id else "")
            + f"- 工作区: `{self.workspace_virtual_path}/`\n"
            + f"{self._get_os_info()}\n"
        )

        # 组装顺序由 prompt_guard 统一保证：
        # 基础规则 → 运行时上下文 → 联网附加 → 从属补充内容 → HITL 说明（最后）。
        self.system_prompt = assemble_system_prompt(
            base_prompt=system_prompt,
            user_info=user_info,
            web_search_prompt=WEB_SEARCH_SYSTEM_PROMPT
            if self.enable_web_search
            else "",
            extra=system_prompt_extra,
            hitl_note=HITL_SYSTEM_NOTE if self.enable_hitl else "",
        )

        # ── 系统提示词可观测日志（专家 Agent 提示词链路校验，可观测点 ③） ──
        # 记录终稿的「总字符数 + 指纹 + 各分段字符数」，用于验证：
        #   · 专家提示词确实进了模型终稿（extra 非空且语法长度吻合）；
        #   · 指纹能与 api/expert.py / agent_manager.py 记录的指纹串成一条链；
        #   · 未挂专家（通用 Agent）时明确显示 extra 为空，避免误判。
        logger.info(
            f"[{self.sid}] 🧾 系统提示词已组装 | 终稿={len(self.system_prompt)} 字符 | "
            f"指纹={prompt_fingerprint(self.system_prompt)} | "
            f"基础={len(system_prompt.strip())} 字符 | "
            f"联网搜索附加={len(WEB_SEARCH_SYSTEM_PROMPT.strip()) if self.enable_web_search else 0} 字符 | "
            f"动态附加={len(system_prompt_extra.strip())} 字符"
        )
        if system_prompt_extra.strip():
            logger.info(
                f"[{self.sid}] ✅ 动态附加内容已注入系统提示词 | "
                f"{describe_prompt('extra（含专家提示词）', system_prompt_extra)}"
            )
        else:
            logger.info(
                f"[{self.sid}] ➖ 无动态附加内容（通用 Agent，未注入专家提示词）"
            )

        self.max_steps = config.agent.max_steps
        self.logger = AgentLogger()
        self.agent_graph_key: str | None = None
        self.agent = self._create_agent()

        logger.info(
            f"EasyAgent initialized | workspace: {self.workspace_dir.absolute()} | user: {username} | session: {session_id}"
        )

    def rename_workspace(self, new_name: str) -> bool:
        """Rename workspace directory after streaming completes.

        Moves the directory and rebuilds the agent with the new path.
        Should only be called AFTER streaming completes to avoid
        breaking in-progress tool calls.
        """
        parent = self.workspace_dir.parent
        new_workspace = parent / new_name

        if new_workspace.resolve() == self.workspace_dir.resolve():
            return False

        if new_workspace.exists():
            for item in self.workspace_dir.iterdir():
                dest = new_workspace / item.name
                if dest.exists():
                    if item.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                shutil.move(str(item), str(dest))
            self.workspace_dir.rmdir()
        else:
            self.workspace_dir.rename(new_workspace)

        old_path = str(self.workspace_dir.absolute())
        self.workspace_dir = new_workspace
        # 扁平化后虚拟路径恒为 /workspace，重命名只改真实目录，虚拟路径不变。

        # 记忆文件基于 workspace_dir 推导，重命名后必须同步更新，
        # 否则 update_memory_after_session 会写回旧目录（且 mkdir 会重建旧目录）。
        self.memory_file = self.workspace_dir / "memory.md"
        self.workspace_name = new_workspace.name
        self.session_context = self._make_session_context()

        self.system_prompt = self.system_prompt.replace(
            old_path, str(new_workspace.absolute())
        )

        self.agent = self._create_agent()

        logger.info(f"Workspace renamed | {old_path} -> {new_workspace.absolute()}")
        return True

    def _make_session_context(self) -> SessionContext:
        """把当前 EasyAgent 的会话差异冻结成运行期 context（不进缓存键）。"""
        return SessionContext(
            session_id=self.session_id or "",
            username=self.username,
            safe_username=self.safe_username,
            workspace_dir=str(self.workspace_dir.absolute()),
            workspace_virtual_path=self.workspace_virtual_path,
            user_skills_dir=str(self.user_skills_dir.absolute()),
            memory_file=str(self.memory_file.absolute()),
            long_term_memory_file=str(self.long_term_memory_file.absolute()),
            organization_id=self.organization_id or "",
            shell_env=dict(self.shell_env),
            workspace_name=self.workspace_name,
            builtin_skills_dir=str(self.builtin_skills_dir or ""),
            builtin_skills=tuple(self.builtin_skills),
        )

    def _build_interrupt_config(self):
        """构造 HITL interrupt_on 配置（FW-12：按实际注入工具动态构造）。

        改造前无论工具是否注入，都会把 ``execute`` / ``save_expert`` /
        ``create_scheduled_task`` / ``access_knowledge_original`` 以及 MCP 写工具
        写进 ``interrupt_on``；未注入的名字会被 DeepAgents 静默忽略（T1-14）。
        现在交给 :func:`easy_agent.agent.hitl.build_interrupt_config` 过滤：注册表只提供
        声明（决策 / 条件谓词 / 顺序），是否进入配置取决于本会话实际工具集。

        ``self.mcp_tools`` 实际承载「MCP + 应用注入工具」全集（历史命名），
        因此这里直接把它作为实际工具来源；``execute`` 等框架内置工具由
        ``build_interrupt_config`` 内部补入。
        """
        return build_interrupt_config(
            getattr(self, "mcp_tools", None) or [],
            enable_hitl=bool(getattr(self, "enable_hitl", True)),
            sid=getattr(self, "sid", ""),
        )

    def _graph_cache_block_reason(
        self, llm_cfg, shared_checkpointer
    ) -> str:
        """返回不可复用原因（"" = 可以复用）。"""
        if not graph_cache_enabled():
            return "EASY_AGENT_GRAPH_CACHE=0（显式关闭）"
        bound = [
            key
            for key, value in (getattr(llm_cfg, "headers", {}) or {}).items()
            if "{session_id}" in str(value)
        ]
        if bound:
            return f"模型请求头绑定 session_id: {bound}"
        if shared_checkpointer is None:
            return (
                "checkpointer 为按 session 创建的 legacy saver"
                "（非官方全局 Postgres/SQLite saver），无法跨会话共享"
            )
        return ""

    def _make_backend_factory(self, skills_paths: list[str], fallback_context=None):
        """返回 DeepAgents 0.7 兼容的 BackendProtocol 实例。

        0.7 移除了 callable backend；这里用 ContextBackend 实例在运行期按
        ToolRuntime.context 解析 SessionContext，再构建 CompositeBackend。
        """
        config = self.config
        expected_user = self.safe_username

        def _builder(ctx):
            return _build_backend_for_context(config, ctx, skills_paths)

        # 供 FilesystemMiddleware 启动期权限门禁检查：所有 permission 路径必须
        # 落在某个 route 前缀下。真实路由仍由 _builder 在运行期按 context 构建。
        prefixes = {
            f"{str(self.workspace_virtual_path).rstrip('/')}/",
            "/memories/",
            "/user-skills/",
        }
        if getattr(self, "builtin_skills_dir", None):
            prefixes.add(f"{BUILTIN_VIRTUAL_ROOT}/")
        for virtual_path in (config.agent.external_dirs or {}):
            value = str(virtual_path)
            prefixes.add(value if value.endswith("/") else value + "/")

        return ContextBackend(
            _builder,
            expected_user=expected_user,
            fallback_context=fallback_context,
            route_prefixes=sorted(prefixes),
        )

    def _create_agent(self):
        """Create the DeepAgents agent, reusing a process-wide compiled graph when safe.

        D2：编译期输入（模型 / 工具集 / 系统提示词 / middleware / HITL /
        subagent specs / skills / permissions / memory 源）用哈希做键；会话差异
        （workspace、session_id、thread_id）通过 ``context=SessionContext`` 传入
        运行期。无法安全共享时（模型头绑定 session、legacy checkpointer）显式
        降级为按会话建图。
        """
        from easy_agent.agent.model import resolve_llm_config

        actual_llm_cfg = resolve_llm_config(self.config, self.model_name)
        logger.info(
            f"[{self.sid}] 🤖 当前使用模型 | "
            f"model_key: {self.model_name} | "
            f"context_length: {actual_llm_cfg.context_length}"
        )
        logger.info(f"[{self.sid}] 📋 系统提示词 | 预览(前100字符): {self.system_prompt[:100]}")

        skills_paths = self._resolve_skills_paths()
        safe_external = self._safe_external_dirs()
        permissions = self._build_permissions(skills_paths, safe_external)
        self._log_user_skills()
        middleware = self._build_middleware()
        interrupt_on_config = self._build_interrupt_config()
        memory_list = self._memory_sources()
        raw_tools = list(self.mcp_tools) if self.mcp_tools else []
        subagents = build_subagent_specs(
            result_log_truncate=getattr(self.config.tools, "result_log_truncate", 500),
            max_steps=getattr(self, "max_steps", None),
            skills_paths=skills_paths,
        )

        store = None
        try:
            store = get_agent_store()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[{self.sid}] LangGraph Store 不可用，跨线程记忆仅走文件: {exc}")

        # FW-04：启动期由 app.py 注册；这里兜底非 Web 入口（调度器/脚本/测试）
        # 首次建图前也能拿到 execute 描述（注册幂等）。
        register_execute_description_profiles(self.config)
        log_framework_patch_status(self.sid)

        logger.info(
            f"[{self.sid}] 🏗️ 创建智能体参数 | "
            f"model: {actual_llm_cfg.model} | "
            f"provider: {actual_llm_cfg.provider} | "
            f"protocol: {actual_llm_cfg.protocol} | "
            f"max_steps: {self.max_steps} | "
            f"workspace: {self.workspace_dir.absolute()} | "
            f"skills: {skills_paths or []} | "
            f"mcp_tools: {len(raw_tools)} | "
            f"middleware: {[type(m).__name__ for m in middleware]} | "
            f"memory_file: {self.memory_file} | "
            f"summarization: {self.config.summarization.enabled} | "
            f"permissions(denied): {len(permissions)} 条"
        )

        memory_path = f"{self.workspace_virtual_path}/memory.md"
        logger.info(
            f"[{self.sid}] 🧠 记忆文件 | "
            f"会话: {memory_path}（存在则运行期加载） | "
            f"长期: {LONG_TERM_MEMORY_VIRTUAL_PATH}（存在则运行期加载） | "
            f"上传清单: {'启用' if self._uploads_manifest_enabled() else '关闭'} | "
            f"实际路径: {self.memory_file} / {self.long_term_memory_file} | "
            f"记忆源: {memory_list}"
        )

        shared_checkpointer = _shared_graph_checkpointer()
        block_reason = self._graph_cache_block_reason(actual_llm_cfg, shared_checkpointer)

        def _build(checkpointer, model, *, fallback_context: bool = False):
            # 共享图不带任何会话 fallback；降级路径才把当前会话作为兜底。
            backend = self._make_backend_factory(
                skills_paths,
                fallback_context=self.session_context if fallback_context else None,
            )
            graph_tools = contextualize_tools(raw_tools)

            # DA-06：同名实例原位替换框架默认的摘要中间件（主代理与每个子代理各一份）。
            # DA-03：主代理的摘要先渲染本轮上下文再计 token；记忆每次运行都重新加载。
            def summarization(*, render_turn: bool = False):
                return build_summarization_middleware(
                    self.config, model, backend, render_turn=render_turn
                )

            main_middleware = [*middleware, summarization(render_turn=True)]
            # DA-13：由技能带出的工具（save_expert）不进 agent 工具列表，交给同名替换默认项的
            # SkillsMiddleware，模型读到 / pin 了在 include_tools 里声明它的技能后才出现
            # DA-13：注册表里标记为 skill_gated 的工具 + 任意 SKILL.md 通过
            # metadata.include_tools 声明的工具（官方约定为空格分隔字符串）
            gated_names = set(skill_gated_tool_names()) | declared_include_tools(
                [self.builtin_skills_dir, self.user_skills_dir]
            )
            # D4：开启 MCP 前缀后，技能里写的仍是原始工具名，这里按后缀唯一匹配解析
            gated_resolved, gated_unresolved = resolve_tool_names(
                gated_names, [getattr(t, "name", "") for t in graph_tools]
            )
            for missing in gated_unresolved:
                logger.warning(
                    "[%s] 技能声明的工具 %s 未注入或存在歧义，门控已跳过", self.sid, missing
                )
            gated_actual = set(gated_resolved.values())
            gated_tools = [t for t in graph_tools if getattr(t, "name", "") in gated_actual]
            graph_tools = [t for t in graph_tools if getattr(t, "name", "") not in gated_actual]
            if gated_tools and skills_paths:
                main_middleware.append(
                    # 传 resolver 而不是列表：开启 MCP 前缀后技能里写的是原名（D4）
                    SkillsMiddleware(
                        backend=backend,
                        sources=skills_paths,
                        tools=skill_tool_resolver(gated_tools),
                    )
                )
            if memory_list:
                main_middleware.append(
                    SessionMemoryMiddleware(
                        backend=backend, sources=memory_list, add_cache_control=True
                    )
                )

            return create_deep_agent(
                name="easy-agent",
                model=model,
                system_prompt=self.system_prompt,
                backend=backend,
                skills=skills_paths or None,
                middleware=main_middleware,
                tools=graph_tools or None,
                memory=memory_list or None,
                permissions=permissions or None,
                checkpointer=checkpointer,
                interrupt_on=interrupt_on_config,
                store=store,
                # DA-11：子代理工具按名字解析成本图的工具实例（缺工具的子代理不声明）
                # DA-13：子代理用到 include_tools 工具时，摘出来挂到它自己的 SkillsMiddleware
                subagents=[
                    {**spec, "middleware": [*spec.get("middleware", []), summarization()]}
                    for spec in apply_skill_gated_tools(
                        resolve_subagent_tools(subagents, [*graph_tools, *gated_tools]),
                        gated_tools,
                        skills_paths,
                        backend,
                    )
                ],
                context_schema=SessionContext,
            )

        if block_reason:
            get_agent_graph_cache().record_disabled()
            logger.info(f"[{self.sid}] D2 graph-cache 未启用 | {block_reason}")
            model = create_model(
                self.config, model_name=self.model_name, session_id=self.session_id
            )
            self.model = model
            self.agent_graph_key = None
            return _build(
                get_session_checkpointer(self.session_id or "default"),
                model,
                fallback_context=True,
            )

        # 门控工具集合参与编译图指纹：技能 frontmatter 的 include_tools 变了要重编译
        gated_names = set(skill_gated_tool_names()) | declared_include_tools(
            [self.builtin_skills_dir, self.user_skills_dir]
        )
        key = build_graph_key(
            model=model_fingerprint(actual_llm_cfg),
            tools=raw_tools,
            system_prompt=self.system_prompt,
            middleware=middleware,
            hitl={
                "enabled": bool(interrupt_on_config),
                "tools": sorted((interrupt_on_config or {}).keys()),
            },
            subagents=subagents,
            skills=skills_paths,
            skill_tools=sorted(gated_names),
            permissions=permissions,
            memory=memory_list,
            summarization=summarization_fingerprint(self.config),
            config=config_fingerprint(self.config),
            checkpointer_id=f"{type(shared_checkpointer).__name__}:{id(shared_checkpointer)}",
            store_id=f"{type(store).__name__}:{id(store)}",
        )

        cache = get_agent_graph_cache()
        entry = cache.get(key.digest)
        if entry is not None:
            self.model = entry.model
            self.agent_graph_key = key.digest
            return entry.graph

        logger.info(
            f"[{self.sid}] D2 graph-cache 未命中，编译新图 | "
            f"key={key.digest[:12]} | {key.label}"
        )
        model = create_model(
            self.config, model_name=self.model_name, session_id=self.session_id
        )
        self.model = model
        graph = _build(shared_checkpointer, model)
        cache.put(
            key.digest,
            graph,
            model=model,
            username=self.safe_username,
            label=key.label,
        )
        self.agent_graph_key = key.digest
        return graph

    def _build_backend(self, skills_paths: list[str], safe_external=None):
        """构建 CompositeBackend（兼容旧调用；实际逻辑按 context 解析）。

        旧测试/旧调用会用 ``EasyAgent.__new__`` 只设置少量属性，再直接调用本方法；
        此时没有 ``session_context``，按现有属性构造一个等价 context，保持兼容。
        """
        context = getattr(self, "session_context", None)
        if context is None:
            workspace_dir = Path(getattr(self, "workspace_dir", Path(".")))
            safe_username = getattr(self, "safe_username", "default")
            config_agent = getattr(getattr(self, "config", None), "agent", None)
            memories_dir = Path(
                getattr(config_agent, "memories_dir", "./memories") or "./memories"
            )
            user_skills_dir = Path(
                getattr(self, "user_skills_dir", workspace_dir / "skills")
            )
            context = SessionContext(
                session_id=getattr(self, "session_id", "") or "",
                username=getattr(self, "username", safe_username),
                safe_username=safe_username,
                workspace_dir=str(workspace_dir.absolute()),
                workspace_virtual_path=getattr(self, "workspace_virtual_path", "/workspace"),
                user_skills_dir=str(user_skills_dir.absolute()),
                memory_file=str((workspace_dir / "memory.md").absolute()),
                long_term_memory_file=str(
                    (memories_dir / safe_username / "AGENTS.md").absolute()
                ),
                organization_id=getattr(self, "organization_id", "") or "",
                shell_env=dict(getattr(self, "shell_env", {}) or {}),
                workspace_name=getattr(self, "workspace_name", "") or "",
                builtin_skills_dir=str(getattr(self, "builtin_skills_dir", "") or ""),
                builtin_skills=tuple(getattr(self, "builtin_skills", ()) or ()),
            )
        return _build_backend_for_context(
            self.config,
            context,
            skills_paths,
            safe_external=safe_external,
            sid=self.sid,
        )

    def _build_middleware(self) -> list:
        """构建自定义中间件列表（实现见 :func:`easy_agent.agent.middleware.build_agent_middleware`）。"""
        return build_agent_middleware(self.config, self.max_steps)

    async def run(self, user_input: str) -> str:
        """Execute agent with streaming output (CLI mode).

        Uses agent.astream(stream_mode='messages') for raw message-level streaming,
        which correctly captures DeepSeek's reasoning_content as per-token chunks.
        """
        self.logger.start_new_run()
        self.logger.log_request(
            messages=[{"role": "user", "content": user_input}], tools=None
        )

        start_time = time.time()
        full_response = ""
        thinking_shown = False
        response_shown = False
        tool_call_accumulated_args = {}

        try:
            async for event in self.agent.astream(
                {"messages": [{"role": "user", "content": user_input}]},
                stream_mode="messages",
                context=self.session_context,
            ):
                chunk, metadata = event
                if is_internal_model_call(metadata):
                    continue  # 摘要等中间件内部调用的 token 不是回复

                if isinstance(chunk, AIMessageChunk):
                    rc = (
                        extract_reasoning(chunk.additional_kwargs)
                        if hasattr(chunk, "additional_kwargs")
                        else ""
                    )
                    content = chunk.content or ""
                    tcc = getattr(chunk, "tool_call_chunks", None) or []

                    if rc:
                        if not thinking_shown:
                            logger.info("🧠 Thinking:")
                            thinking_shown = True
                        print(rc, end="", flush=True)

                    if content and not tcc:
                        if thinking_shown:
                            thinking_shown = False
                            print()  # newline separator
                        if not response_shown:
                            logger.info("🤖 Assistant:")
                            response_shown = True
                        print(content, end="", flush=True)
                        full_response += content

                    for tc in tcc:
                        name = tc.get("name", "") or ""
                        args_str = str(tc.get("args", "") or "")
                        tid = tc.get("id", "") or ""
                        if name:
                            args_data = args_str
                            if args_str:
                                try:
                                    parsed = json.loads(args_str)
                                    args_data = (
                                        parsed
                                        if isinstance(parsed, dict)
                                        else {"value": parsed}
                                    )
                                except json.JSONDecodeError:
                                    args_data = {}
                            else:
                                args_data = {}
                            tool_call_accumulated_args[name] = args_data
                            logger.info(f"🔧 Tool: {name} | Args: {args_data}")
                        elif tid and tid in tool_call_accumulated_args:
                            if args_str:
                                try:
                                    parsed = json.loads(args_str)
                                    if isinstance(parsed, dict):
                                        tool_call_accumulated_args[
                                            list(tool_call_accumulated_args.keys())[-1]
                                        ] = parsed
                                except json.JSONDecodeError:
                                    pass

                elif isinstance(chunk, ToolMessage):
                    from easy_agent.services.mcp import _parse_mcp_content

                    tool_name = getattr(chunk, "name", "") or ""
                    result = _parse_mcp_content(chunk.content) if chunk.content else ""
                    truncate_len = getattr(
                        self.config.tools, "result_log_truncate", 200
                    )
                    logger.info(f"📊 Result [{tool_name}]: {result[:truncate_len]}")

            if thinking_shown or response_shown:
                print()  # final newline

            elapsed = time.time() - start_time
            logger.info(f"⏱️  Completed in {elapsed:.2f}s")

        except Exception as e:
            error_msg = f"Agent execution failed: {e}"
            logger.error(f"❌ {error_msg}")
            self.logger.log_response(content="", finish_reason="error")
            return error_msg

        self.logger.log_response(content=full_response, finish_reason="stop")
        return full_response
