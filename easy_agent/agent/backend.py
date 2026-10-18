"""运行期 CompositeBackend 装配（D2 共享图使用）。

从 ``easy_agent.agent`` 拆出的顶层函数：按运行期 ``SessionContext`` 构建
workspace / user-skills / memories / external_dirs 路由与默认 shell backend。
本次仅移动代码（函数体逐字未改），``easy_agent.agent`` 继续 re-export。
"""

import logging
from pathlib import Path

from deepagents.backends import CompositeBackend, LocalShellBackend

from easy_agent.agent.graph_cache import SessionContext
from easy_agent.backends.langsmith_sandbox import create_langsmith_backend, sandbox_mode
from easy_agent.backends.shell import _PathTranslatingShell
from easy_agent.bootstrap.config import Config
from easy_agent.sandboxing.fs_safety import _check_external_dir_safety

# 纯移动：沿用拆分前的 logger 名称，保持日志路由/过滤行为不变。
logger = logging.getLogger("easy_agent.agent")


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
    if skills_paths or user_skills_dir.exists():
        routes["/user-skills/"] = LocalShellBackend(
            root_dir=str(user_skills_dir.absolute()),
            virtual_mode=True,
            env=shell_env,
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
        default_backend = create_langsmith_backend(
            workspace_dir=ws_real,
            path_mappings=path_mappings,
            workspace_virtual_path=workspace_virtual_path,
            timeout=120,
            env=shell_env,
            sid=sid,
        )
    else:
        default_backend = _PathTranslatingShell(
            path_mappings=path_mappings,
            root_dir=ws_str,
            virtual_mode=True,
            inherit_env=True,
            timeout=120,
            sandbox_enabled=config.agent.sandbox_enabled,
        )

    return CompositeBackend(default=default_backend, routes=routes)
