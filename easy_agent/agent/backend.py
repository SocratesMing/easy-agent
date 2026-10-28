"""运行期 CompositeBackend 装配（D2 共享图使用）。

按运行期 ``SessionContext`` 构建本会话的 workspace / user-skills / memories /
external_dirs 路由，以及 default：框架产物的隐藏目录 + 转发 execute 的会话 shell（DA-04，
见 ``backends/artifacts.py``）。``easy_agent.agent`` 继续 re-export。
"""

import logging
import re
from pathlib import Path

from deepagents.backends import CompositeBackend, LocalShellBackend

from easy_agent.agent.graph_cache import SessionContext
from easy_agent.agent.builtin_skills import BUILTIN_VIRTUAL_ROOT
from easy_agent.backends.artifacts import AGENT_ARTIFACTS_ROOT, AgentArtifactsBackend
from easy_agent.backends.builtin_skills import BuiltinSkillsBackend
from easy_agent.backends.langsmith_sandbox import create_langsmith_backend, sandbox_mode
from easy_agent.backends.scoped_skills import ScopedSkillsBackend
from easy_agent.backends.shell import _PathTranslatingShell
from easy_agent.bootstrap.config import Config
from easy_agent.sandboxing.fs_safety import _check_external_dir_safety
from easy_agent.sandboxing.sandbox import host_safe_env

# 纯移动：沿用拆分前的 logger 名称，保持日志路由/过滤行为不变。
logger = logging.getLogger("easy_agent.agent")

_UNSAFE_DIR_CHARS = re.compile(r"[^A-Za-z0-9_-]")


def _sandbox_network(config: Config) -> str:
    """``agent.sandbox_network``：``host``（默认）/ ``none``；无法识别的值按 host 并告警。"""
    value = str(getattr(config.agent, "sandbox_network", "host") or "host").strip().lower()
    if value not in ("host", "none"):
        logger.warning(f"agent.sandbox_network={value!r} 无法识别，按 host 处理（可选 host / none）")
        return "host"
    return value


def _protected_skill_mounts(
    skills_dir: Path, policy: str, virtual_root: str = "/user-skills"
) -> tuple[list[tuple[str, str]], list[str]]:
    """``agent.skill_write`` 在 execute 沙箱里的落实（DA-07）。

    改造前技能目录在沙箱里整体可写，``new-only`` / ``deny`` 只约束文件工具，命令照样能改写
    已有技能。返回 ``(bwrap 只读覆盖, Landlock 只读目录)``：

    - ``new-only``：已有的每个顶层条目（技能目录、隐藏文件）只读覆盖在可写的技能根上，
      仍可在根下新建技能；Landlock 无法在可写目录里再收紧，整个技能根改为只读；
    - ``deny``：整个技能根只读；
    - ``allow``：不限制。
    """
    if policy == "allow" or not skills_dir.is_dir():
        return [], []
    root = str(skills_dir.absolute())
    if policy == "deny":
        return [(root, virtual_root)], [root]
    entries = sorted(skills_dir.iterdir(), key=lambda entry: entry.name)
    return [(str(entry.absolute()), f"{virtual_root}/{entry.name}") for entry in entries], [root]


def agent_artifacts_dir(username: str, session_id: str, config: Config | None = None) -> Path:
    """会话的框架产物目录：``{workspace_dir}/{user}/.agent/{session_id}/``（DA-04）。

    在会话工作区之外：工作区文件面板、``/workspace/`` 路由、execute 沙箱都看不到它；
    按 session_id 而不是 workspace_name 命名，会话改名后不变；删除会话时一并删除。
    """
    key = _UNSAFE_DIR_CHARS.sub("_", str(session_id or "")) or "default"
    return Config.get_user_workspace_dir(username, config) / ".agent" / key


def _build_backend_for_context(
    config: Config,
    context: SessionContext,
    skills_paths: list[str],
    *,
    safe_external: dict[str, str] | None = None,
    sid: str = "",
) -> CompositeBackend:
    """按运行期 ``SessionContext`` 构建 CompositeBackend（D2 共享图使用）。

    与旧 ``EasyAgent._build_backend`` 的路由/权限语义逐项一致，唯一区别是
    workspace / user-skills / memories 路径来自 context，而不是编译时的
    ``EasyAgent`` 实例。backend callable 每次请求都会调用它，因此同一张图可
    服务多个 session。
    """
    workspace_virtual_path = context.workspace_virtual_path or "/workspace"
    workspace_dir = Path(context.workspace_dir)
    user_skills_dir = Path(context.user_skills_dir)
    long_term_memory_file = Path(context.long_term_memory_file)
    shell_env = dict(getattr(context, "shell_env", {}) or {})
    sid = sid or getattr(context, "sid", "") or "graph"

    if safe_external is None:
        external_dirs = config.agent.external_dirs or {}
        workspace_root = Path(config.agent.workspace_dir)
        safe_external = {}
        for vpath, real_path in external_dirs.items():
            real = Path(real_path)
            ok, reason = _check_external_dir_safety(
                real, workspace_root, context.safe_username
            )
            if not ok:
                logger.warning(
                    f"[{sid}] 🚫 拒绝挂载外部目录 {vpath} -> {real_path}：{reason}。"
                    f"该挂载已跳过，以防止越权访问其他用户的会话数据。"
                )
                continue
            vp = vpath if vpath.endswith("/") else vpath + "/"
            safe_external[vp] = str(real.absolute())

    workspace_backend = LocalShellBackend(
        root_dir=str(workspace_dir.absolute()),
        virtual_mode=True,
        env=shell_env,
    )
    routes = {f"{workspace_virtual_path}/": workspace_backend}

    # 长期记忆（跨会话 AGENTS.md）挂载为 /memories/ 路由，供
    # create_deep_agent(memory=[LONG_TERM_MEMORY_VIRTUAL_PATH]) 经虚拟路径读取。
    # D2 记忆源固定（文件不存在时 MemoryMiddleware 自动跳过），因此路由总是挂载，
    # 避免「首轮无记忆 / 二轮有记忆」改变编译图结构。
    try:
        long_term_memory_file.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(f"[{sid}] 创建长期记忆目录失败: {e}")
    routes["/memories/"] = LocalShellBackend(
        root_dir=str(long_term_memory_file.parent.absolute()),
        virtual_mode=True,
        env=shell_env,
    )

    # 技能库路由总是挂载（即使当前一个技能都没有）：否则模型没有可写路径来创建
    # 第一个技能。目录不存在时先建出来，保证 route 与 path_mappings 保持一致。
    try:
        user_skills_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(f"[{sid}] 创建用户技能目录失败: {e}")
    # 技能作用域（专家技能硬隔离）：None = 全量；非 None 时只暴露白名单内的技能目录。
    # 这一层同时管住「技能清单被谁发现」（SkillsMiddleware 走 backend.ls）与
    # 「能不能直接读到未选中技能的 SKILL.md」（read / grep / glob / download）。
    skill_whitelist = getattr(context, "skill_whitelist", None)
    if skills_paths or user_skills_dir.exists():
        routes["/user-skills/"] = ScopedSkillsBackend(
            user_skills_dir,
            skill_whitelist,
            env=shell_env,
        )
    if skill_whitelist is None:
        logger.info(f"[{sid}] 🔓 用户技能库全量可见（无技能作用域）")
    else:
        logger.info(
            f"[{sid}] 🔒 用户技能库已按作用域过滤 | 白名单 {len(skill_whitelist)} 个: "
            f"{list(skill_whitelist) or '（空 → 一个技能都不可见）'} | "
            f"其余目录不进技能清单、read_file 也读不到"
        )

    # 公共技能（DA-13 S2）：canonical 目录只读挂载，只看得到本会话用户已添加且启用的
    builtin_dir = str(getattr(context, "builtin_skills_dir", "") or "")
    builtin_enabled = tuple(getattr(context, "builtin_skills", ()) or ())
    if skill_whitelist is not None:
        # 公共技能同样受作用域约束：专家只绑定了某几个公共技能时，其余连清单都不出现
        scoped_enabled = tuple(name for name in builtin_enabled if name in skill_whitelist)
        if scoped_enabled != builtin_enabled:
            logger.info(
                f"[{sid}] 🔒 公共技能作用域收窄 | 启用 {len(builtin_enabled)} 个 -> "
                f"白名单内 {len(scoped_enabled)} 个 | {list(scoped_enabled)}"
            )
        builtin_enabled = scoped_enabled
    if builtin_dir:
        routes[f"{BUILTIN_VIRTUAL_ROOT}/"] = BuiltinSkillsBackend(
            builtin_dir, builtin_enabled, virtual_root=BUILTIN_VIRTUAL_ROOT
        )

    # 挂载外部目录（skill 需要访问的宿主机目录）为虚拟路径路由。
    # 仅挂载通过安全校验的目录（safe_external），防止把工作区根或其他用户
    # 子树挂载出来导致越权访问其他用户的会话数据。
    for vp, real_abs in (safe_external or {}).items():
        real = Path(real_abs)
        real.mkdir(parents=True, exist_ok=True)
        routes[vp] = LocalShellBackend(
            root_dir=str(real.absolute()),
            virtual_mode=True,
            env=shell_env,
        )

    logger.info(
        f"[{sid}] 🗺️ CompositeBackend routes:\n"
        + "\n".join(f"    {vp:40s} → {b.cwd}" for vp, b in sorted(routes.items()))
    )

    ws_real = workspace_dir.absolute()
    ws_real.mkdir(parents=True, exist_ok=True)
    ws_str = str(ws_real)

    path_mappings = {f"{workspace_virtual_path}/": ws_str + "/"}
    if "/user-skills/" in routes:
        path_mappings["/user-skills/"] = str(user_skills_dir.absolute()) + "/"
    for vp, real_abs in (safe_external or {}).items():
        path_mappings[vp] = real_abs + "/"

    if sandbox_mode() == "langsmith":
        # opt-in：仅 execute 走远端 LangSmith Sandbox；文件工具仍走上面的本地路由。
        # 该 backend 由 ContextBackend 按 context 缓存，不会每次工具调用新建。
        shell = create_langsmith_backend(
            workspace_dir=ws_real,
            path_mappings=path_mappings,
            workspace_virtual_path=workspace_virtual_path,
            timeout=120,
            env=shell_env,
            sid=sid,
        )
    else:
        readonly_binds: list[tuple[str, str]] = []
        readonly_dirs: list[str] = []
        readonly_mappings: dict[str, str] = {}
        if "/user-skills/" in routes:
            policy = str(getattr(config.agent, "skill_write", "new-only") or "new-only").lower()
            readonly_binds, readonly_dirs = _protected_skill_mounts(user_skills_dir, policy)
        if builtin_dir:
            # 公共技能在沙箱里只读：bwrap 只挂已启用的那几个；Landlock 无法按子目录放行，
            # 整个公共技能目录只读可见；路径翻译（Landlock / 无沙箱时）走 readonly_mappings
            readonly_binds = [
                *readonly_binds,
                *((str(Path(builtin_dir) / name), f"{BUILTIN_VIRTUAL_ROOT}/{name}") for name in builtin_enabled),
            ]
            readonly_dirs = [*readonly_dirs, builtin_dir]
            readonly_mappings[f"{BUILTIN_VIRTUAL_ROOT}/"] = builtin_dir.rstrip("/") + "/"
        shell = _PathTranslatingShell(
            path_mappings=path_mappings,
            root_dir=ws_str,
            virtual_mode=True,
            # 只交白名单变量：sandbox_enabled=false 时命令直接用这份 env 运行，
            # 继承整个宿主环境会把 .env 里的密钥暴露给 agent 的命令（DA-16）。
            inherit_env=False,
            env=host_safe_env(),
            timeout=120,
            sandbox_enabled=config.agent.sandbox_enabled,
            sandbox_network=_sandbox_network(config),
            readonly_binds=readonly_binds,
            readonly_dirs=readonly_dirs,
            readonly_mappings=readonly_mappings,
        )

    # DA-04：没命中路由的路径（含框架产物 /.agent/...）落在会话隐藏目录，不再是工作区的别名；
    # execute 仍由上面的 shell 在会话工作区里跑。
    artifacts_dir = agent_artifacts_dir(
        getattr(context, "username", "") or getattr(context, "safe_username", ""),
        getattr(context, "session_id", "") or getattr(context, "workspace_name", ""),
        config,
    )
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    default_backend = AgentArtifactsBackend(
        artifacts_dir,
        shell,
        artifacts_root=AGENT_ARTIFACTS_ROOT,
        workspace_virtual_path=workspace_virtual_path,
        max_timeout=getattr(config.agent, "execute_max_timeout", 600),
    )

    return CompositeBackend(
        default=default_backend, routes=routes, artifacts_root=AGENT_ARTIFACTS_ROOT
    )
