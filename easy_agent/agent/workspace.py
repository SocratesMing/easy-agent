"""EasyAgent 的 workspace / skills / permissions 辅助方法。

从 ``easy_agent.agent`` 拆出的低耦合方法（纯移动，方法体逐字未改）。
它们只使用 ``EasyAgent`` 上已有的运行时属性（config / safe_username /
workspace_dir / user_skills_dir / sid 等），不引入额外状态；
``EasyAgent`` 通过继承 :class:`AgentWorkspaceMixin` 继续暴露同名方法。
"""

import logging
import platform
from pathlib import Path

from deepagents.middleware.filesystem import (
    FilesystemOperation,
    FilesystemPermission,
)

from easy_agent.sandboxing.fs_safety import _check_external_dir_safety

# 纯移动：沿用拆分前的 logger 名称，保持日志路由/过滤行为不变。
logger = logging.getLogger("easy_agent.agent")

# 长期记忆（跨会话 AGENTS.md）在虚拟文件系统中的挂载点：
# _build_backend 把 memories/{username}/ 挂成 /memories/ 路由，
# create_deep_agent(memory=[...]) 再经该虚拟路径加载（MemoryMiddleware 读 backend）。
LONG_TERM_MEMORY_VIRTUAL_PATH = "/memories/AGENTS.md"


class AgentWorkspaceMixin:
    """workspace / skills / permissions 相关方法（``EasyAgent`` 的纯移动部分）。"""

    def _load_memory_file(self, memory_path: Path, label: str) -> str:
        """读取记忆文件内容（不存在或读取失败时返回空字符串）。

        Args:
            memory_path: 记忆文件路径。
            label: 日志中使用的记忆名称（如 "会话记忆"/"长期记忆"）。
        """
        if not memory_path or not memory_path.exists():
            return ""
        try:
            content = memory_path.read_text(encoding="utf-8").strip()
            if content:
                logger.info(
                    f"[{self.session_id[-5:] if self.session_id else '?'}] "
                    f"📥 加载{label} | 文件: {memory_path} | 长度: {len(content)} 字符"
                )
            return content
        except Exception as e:
            logger.warning(f"加载{label}失败: {e}")
        return ""

    def load_session_memory(self) -> str:
        """加载当前会话的记忆文件（workspace/.../memory.md）。

        在后续用户输入时调用，将历史会话记忆作为上下文注入。
        若文件不存在（如首轮对话），返回空字符串。
        """
        return self._load_memory_file(self.memory_file, "会话记忆")

    def load_long_term_memory(self) -> str:
        """加载用户长期记忆文件（memories/{username}/AGENTS.md）。

        跨会话持久化，记录用户偏好与可复用经验。
        在新会话首轮对话时加载，提供跨会话上下文。
        """
        return self._load_memory_file(self.long_term_memory_file, "长期记忆")

    def _get_os_info(self) -> str:
        """获取当前操作系统信息，返回格式化的系统提示词片段。

        检测运行环境并将系统名称统一映射为友好显示名
        （如 Darwin → macOS），用于拼接到系统提示词中。

        Returns:
            格式化的操作系统信息字符串，如 '## OS: Linux'。
        """
        system = platform.system()
        os_name = {"Windows": "Windows", "Linux": "Linux", "Darwin": "macOS"}.get(
            system, system
        )
        os_info = f"## OS: {os_name}"
        if system == "Windows":
            os_info += (
                "\n- Shell 为 cmd.exe；请使用 dir/type/copy/mkdir 等命令。"
                "\n- Python 命令使用 python，不要使用 python3。"
            )
        return os_info

    def _uploads_manifest_enabled(self) -> bool:
        cfg = getattr(self.config, "file_context", None)
        return cfg is not None and bool(getattr(cfg, "enabled", True))

    def _memory_sources(self) -> list[str]:
        """固定的记忆源列表（文件不存在时 MemoryMiddleware 自动跳过）。

        D2 之前 memory 列表按「文件当前是否存在」变化，导致同一会话首轮/二轮
        编译出不同图。改为固定列表后，运行期按需加载，既减少重复编译，也让
        记忆在写入后下一轮立即可见。
        """
        sources = [
            f"{self.workspace_virtual_path}/memory.md",
            LONG_TERM_MEMORY_VIRTUAL_PATH,
        ]
        if self._uploads_manifest_enabled():
            from easy_agent.services.session_uploads import MANIFEST_FILENAME, UPLOADS_DIR_NAME

            sources.append(
                f"{self.workspace_virtual_path}/{UPLOADS_DIR_NAME}/{MANIFEST_FILENAME}"
            )
        return sources

    def _safe_external_dirs(self) -> dict[str, str]:
        """返回通过安全校验的外部目录映射 {虚拟路径(带/): 实际绝对路径}。

        跳过任何可能越权暴露其他用户会话数据的外部目录：工作区根目录本身、
        其祖先、或工作区根下不属于当前用户的子树。被跳过的目录会记录安全告警，
        确保即使配置失误也不会把其他用户的会话目录挂载给当前会话的智能体。
        """
        external_dirs = self.config.agent.external_dirs or {}
        workspace_root = Path(self.config.agent.workspace_dir)
        result: dict[str, str] = {}
        for vpath, real_path in external_dirs.items():
            real = Path(real_path)
            ok, reason = _check_external_dir_safety(
                real, workspace_root, self.safe_username
            )
            if not ok:
                logger.warning(
                    f"[{self.sid}] 🚫 拒绝挂载外部目录 {vpath} -> {real_path}：{reason}。"
                    f"该挂载已跳过，以防止越权访问其他用户的会话数据。"
                )
                continue
            vp = vpath if vpath.endswith("/") else vpath + "/"
            result[vp] = str(real.absolute())
        return result

    def _build_permissions(self, skills_paths: list[str] | None = None, safe_external: dict[str, str] | None = None) -> list[FilesystemPermission]:
        """根据配置构建文件系统权限规则。

        读取 config.agent.denied_dirs 中配置的虚拟路径目录列表，
        为每个目录生成一条 deny 权限规则。

        支持两种配置格式：
        - 字符串 ``"/user-skills"``：默认禁止 read+write
        - 字典 ``{path: "/user-skills", operations: ["write"]}``：只禁止指定操作

        operations 可选值：``read``、``write``，默认两者都禁止。
        paths 使用 glob 模式：自动为目录路径补充 /** 后缀以递归匹配子目录。
        注意：使用 CompositeBackend + sandbox default 时，permission path
        必须限定在已知 route 前缀下（如 /memories/、/user-skills/），
        否则会抛出 NotImplementedError。

        ``/user-skills`` 的 write 权限额外由 ``agent.skill_write`` 接管（默认
        new-only：可新建技能、不可改写已有技能），见 ``_build_skill_write_permissions``。
        规则顺序即优先级（框架取第一条命中），因此技能库规则排在最前。

        Args:
            skills_paths: 已解析的技能虚拟路径列表，用于判断 /user-skills/
                路由是否已挂载。未挂载时跳过对应 deny 规则，避免
                FilesystemMiddleware NotImplementedError。

        Returns:
            FilesystemPermission 列表，无配置且技能库未挂载时返回空列表。
        """
        denied = self.config.agent.denied_dirs or []
        policy = self._skill_write_policy()
        # 与 _build_backend 的挂载条件保持一致：技能目录存在即已挂载路由
        skills_mounted = bool(skills_paths) or self.user_skills_dir.exists()

        existing_prefixes = [
            f"{self.workspace_virtual_path}/",
        ]
        if skills_mounted:
            existing_prefixes.append("/user-skills/")
        for vp in (safe_external or {}):
            existing_prefixes.append(vp)

        permissions: list[FilesystemPermission] = []

        # 技能库写权限规则排在最先：框架按列表顺序取第一条命中，细规则必须先于通用规则
        if skills_mounted and policy != "allow":
            permissions.extend(self._build_skill_write_permissions(policy))
            logger.info(
                f"[{self.sid}] 🔒 技能库写权限 | skill_write={policy} | "
                f"已有技能 {len(self._discover_user_skill_names())} 个（其内容禁止改写）"
            )

        for item in denied:
            if isinstance(item, dict):
                path = str(item.get("path", "")).rstrip("/")
                raw_ops = item.get("operations", ["read", "write"])
            else:
                path = str(item).rstrip("/")
                raw_ops = ["read", "write"]
            if not path:
                continue
            if path == "/user-skills" and "write" in raw_ops:
                # /user-skills 的写权限由 agent.skill_write 接管：new-only 下需要允许
                # 新建技能目录，整体禁写请改用 skill_write=deny，避免两套规则互相冲突
                raw_ops = [op for op in raw_ops if op != "write"]
                logger.info(
                    f"[{self.sid}] ℹ️ /user-skills 的 write 权限由 "
                    f"agent.skill_write={policy} 接管，已忽略 denied_dirs 中的同名禁令"
                )
            glob_path = f"{path}/**"
            if not any(glob_path.startswith(prefix) for prefix in existing_prefixes):
                logger.info(
                    f"[{self.sid}] ⏭️ 跳过 deny 规则 | "
                    f"路径 {path} 未挂载路由，避免 NotImplementedError"
                )
                continue
            ops: list[FilesystemOperation] = [
                op for op in raw_ops if op in ("read", "write")
            ]
            if not ops:
                continue
            permissions.append(
                FilesystemPermission(
                    operations=ops,
                    paths=[glob_path],
                    mode="deny",
                )
            )

        logger.info(
            f"[{self.sid}] 🔒 文件系统权限 | "
            f"已配置 {len(permissions)} 条 deny 规则: {denied}"
        )
        return permissions

    def _skill_write_policy(self) -> str:
        """技能库写权限策略：new-only（默认）/ deny / allow，非法值回落 new-only。"""
        policy = str(getattr(self.config.agent, "skill_write", "") or "").strip().lower()
        return policy if policy in ("new-only", "deny", "allow") else "new-only"

    def _build_skill_write_permissions(
        self, policy: str
    ) -> list[FilesystemPermission]:
        """生成技能库写权限规则（第一条命中即生效，顺序有意义）。

        目的：让模型能在对话里**新建**技能目录并写入 ``SKILL.md``，同时改不动、
        删不掉用户已有的技能。

        - ``deny``：整库禁写（含隐藏路径），旧行为
        - ``new-only``：
          1. 隐藏路径禁写：wcmatch 未开 DOTMATCH，``/user-skills/**`` 匹配不到点开头的
             名字，必须显式列出才堵得住绕过；
          2. 已有技能目录内容禁写：只放行"新建"，不允许改写/删除既有技能；
          3. 技能库根下的一级条目禁写：挡住散落的单文件（``/user-skills/xxx.md``），
             正常写入路径 ``/user-skills/<新技能>/SKILL.md`` 是两级，不受影响。
        """
        if policy == "deny":
            return [
                FilesystemPermission(
                    operations=["write"],
                    paths=[
                        "/user-skills/**",
                        "/user-skills/.**",
                        "/user-skills/.*/**",
                    ],
                    mode="deny",
                )
            ]

        rules = [
            FilesystemPermission(
                operations=["write"],
                paths=["/user-skills/.**", "/user-skills/.*/**"],
                mode="deny",
            )
        ]
        for name in self._discover_user_skill_names():
            rules.append(
                FilesystemPermission(
                    operations=["write"],
                    paths=[f"/user-skills/{name}/**"],
                    mode="deny",
                )
            )
        rules.append(
            FilesystemPermission(
                operations=["write"],
                paths=["/user-skills/*"],
                mode="deny",
            )
        )
        return rules

    def _discover_user_skill_names(self) -> list[str]:
        """发现用户 workspace/{username}/skills/ 目录下的技能名称列表"""
        if not self.user_skills_dir.exists():
            return []
        names = []
        for skill_dir in sorted(self.user_skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            if (skill_dir / "SKILL.md").exists():
                names.append(skill_dir.name)
        return names

    def _log_user_skills(self) -> None:
        """打印当前用户技能目录及其中已添加的所有技能。

        在每次智能体加载时调用，便于排查技能中心添加的技能是否正确加载。
        """
        names = self._discover_user_skill_names()
        user_dir = str(self.user_skills_dir.absolute())

        if not names:
            logger.info(
                f"[{self.sid}] 🧩 用户技能 | 用户 '{self.username}' 未添加任何技能 | "
                f"目录: {user_dir}"
            )
            return

        lines = [
            f"[{self.sid}] 🧩 用户技能 | 用户 '{self.username}' 已添加 {len(names)} 个技能",
            f"    📁 技能目录: {user_dir}",
        ]
        for name in names:
            skill_dir = self.user_skills_dir / name
            desc = self._read_skill_description(skill_dir)
            lines.append(f"    - {name}" + (f"  ({desc})" if desc else ""))
        logger.info("\n".join(lines))

    @staticmethod
    def _read_skill_description(skill_dir: Path) -> str:
        """从 SKILL.md 的 frontmatter 中读取 description 字段。

        仅取第一行描述，超长截断到 60 字符。
        """
        for md_name in ("SKILL.md",):
            md_path = skill_dir / md_name
            if not md_path.exists():
                continue
            try:
                text = md_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            # YAML frontmatter: ---\n key: value \n ---
            if text.startswith("---"):
                end = text.find("\n---", 3)
                if end != -1:
                    front = text[3:end]
                    for raw in front.splitlines():
                        line = raw.strip()
                        if line.lower().startswith("description:"):
                            desc = line.split(":", 1)[1].strip().strip('"').strip("'")
                            return desc[:60]
            return ""
        return ""

    def _resolve_skills_paths(self) -> list[str]:
        """返回用户技能的父级目录虚拟路径。

        SkillsMiddleware 会自动扫描父级目录下所有含 SKILL.md 的子目录，
        因此只需传入 /user-skills/ 即可，无需逐个列出每个技能路径。
        仅当用户技能目录存在且有技能时才返回。
        """
        if not self.user_skills_dir.exists():
            return []
        if not self._discover_user_skill_names():
            return []
        return ["/user-skills/"]
