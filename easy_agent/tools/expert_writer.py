"""LangChain tool：把「在对话里创建 / 修改专家」落成结构化专家资产。

背景
----
专家原先只能通过前端弹窗（``POST /agent/add``）创建：弹窗里调一次 LLM 生成配置，
用户在等待期间看不到任何进展（弹窗无法展示模型的思考过程）。本工具把同一件事搬到
对话里——由 ``expert-creator`` 技能引导 Agent 产出结构化配置，再调用本工具落库 +
落版本目录，用户在对话框里就能看到工具调用的实时进展。

两条生成路径（按可靠性排序）
---------------------------
1. **结构化直传（快路径，默认）**：Agent 已按技能要求写出 ``system_prompt`` /
   ``capabilities`` / ``description`` / ``skills_names`` 等字段，本工具只做规整 +
   落盘，**不再额外发起一次 LLM 调用**（这正是原先"创建专家很慢"的主因之一）；
2. **兜底生成（慢路径）**：Agent 只给了中文名与用户描述、没给 ``system_prompt`` 时，
   复用 ``services/expert_service.generate_expert_config`` 调一次模型补齐配置，
   保证「模型没按技能输出」时仍然能创建成功。

落盘口径与 ``POST /agent/add`` / ``POST /agent/update`` **完全一致**：版本目录
``workspace/{username}/experts/{id}/v{n}/``、``active`` 指针、库记录字段、
修改时新建 ``v{n+1}`` 而不覆盖 ``v{n}``（见 ``services/expert_service.py``）。

安全约束
--------
- 归属校验统一走 ``ExpertStore.get_owned(id, user_id)``：改不了、也删不了别人的专家；
- 中文名唯一（忽略空白与大小写，软删除的专家不占名），与接口层同一口径；
- 修改不会触碰 ``active`` 之外的历史版本，运行中的旧会话不会被半成品影响。
"""

import logging
from typing import Any, Literal, Optional

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, SkipValidation

from easy_agent.db import Database, get_database
from easy_agent.db.expert_store import (
    STATUS_ACTIVE,
    STATUS_BUILDING,
    STATUS_FAILED,
    ExpertStore,
)
from easy_agent.services.expert_service import (
    MAX_DESCRIPTION_LEN,
    build_expert_ids,
    generate_expert_config,
    normalize_config,
    pick_tag_id,
    slugify,
    validate_create_params,
    write_expert_version,
)

logger = logging.getLogger("easy_agent.tools.expert_writer")

#: 结构化直传路径要求的最小字段集合（缺 system_prompt 时改走兜底生成）
_REQUIRED_STRUCTURED = ("system_prompt",)

#: 创建/修改专家时**不算专家技能包**的技能目录名。
#: ``expert-creator`` 是"造专家"的元技能：专家面板会把它预选进输入框（否则
#: ``save_expert`` 由 include_tools 带不出来），它只是本轮的执行手段，绝不能
#: 落进专家的 ``export_required_skills``——否则专家会把"造专家"当成自己的本事。
_META_SKILL_DIRS = frozenset({"expert-creator"})


class SaveExpertArgs(BaseModel):
    """创建 / 修改专家所需的全部参数。"""

    action: Literal["create", "update"] = Field(
        ...,
        description="create=新建专家；update=修改已有专家（必须同时给 expert_agent_id）",
    )
    expert_agent_id: str = Field(
        default="",
        description="action=update 时必填：要修改的专家 ID（形如 expert_xxx_1a2b3c4d）",
    )
    name_cn: str = Field(
        default="",
        description="专家中文名（2~20 字，体现专业领域，如「量化策略研究员」）。"
        "update 时留空表示沿用原名",
    )
    prompt: str = Field(
        default="",
        description="用户对专家的原始需求描述（由用户消息提炼，保留其经验与场景，"
        "不要改写成营销文案）。update 时留空表示沿用原描述",
    )

    # ── 结构化配置（快路径：Agent 直接产出，本工具不再调模型） ──────────
    system_prompt: str = Field(
        default="",
        description=(
            "专家系统提示词正文（300~800 字，第二人称「你是……」撰写），"
            "包含角色定位、专业方法、输出规范与交互方式。**必须提供**，"
            "缺失时本工具会退化为调用模型自动生成（更慢）"
        ),
    )
    scenario: str = Field(default="", description="应用场景描述（80~200 字）")
    capabilities: list[str] = Field(
        default_factory=list, description="核心能力（3~6 条，每条一句话）"
    )
    workflow: list[str] = Field(
        default_factory=list, description="工作流程步骤（3~6 步，按执行顺序）"
    )
    boundaries: list[str] = Field(
        default_factory=list, description="操作边界 / 禁止事项（2~5 条）"
    )
    output_spec: list[str] = Field(
        default_factory=list, description="输出规范（3~6 条）"
    )
    features_prompt: list[str] = Field(
        default_factory=list, description="能力示例提示词（3~5 条，用户可直接点击的短句）"
    )
    description: str = Field(
        default="",
        description=f"专家功能描述（不超过 {MAX_DESCRIPTION_LEN} 字），用于卡片与详情页展示",
    )
    skills_names: list[str] = Field(
        default_factory=list,
        description="技能标签：**恰好 3 个**，每个 2~6 个汉字（如 宏观投研 / 风险控制 / 因子分析）",
    )
    tag_id: str = Field(
        default="",
        description="分类标签 ID：从「候选分类标签」里选唯一最匹配的一个；都不匹配时给 other",
    )
    name_en: str = Field(
        default="", description="英文标识名（小写字母/数字/连字符，3~40 字符，如 quant-factor-researcher）"
    )
    profession_zh: str = Field(default="", description="专家头衔中文（如 资深量化策略研究员）")
    profession_en: str = Field(default="", description="专家头衔英文（如 Senior Quantitative Researcher）")


def _to_public(record: dict) -> dict:
    """库记录 -> 对外字段（与 ``api/expert.py:_to_public`` 同一口径）。"""
    if not record:
        return {}
    return {
        "export_agent_id": record.get("export_agent_id", ""),
        "tag_id": record.get("tag_id", ""),
        "export_name_en": record.get("export_name_en", ""),
        "export_name_cn": record.get("export_name_cn", ""),
        "export_description": record.get("export_description", ""),
        "export_skills_names": record.get("export_skills_names", []),
        "export_required_skills": record.get("export_required_skills", []),
        "export_features_prompt": record.get("export_features_prompt", []),
        "export_file_path": record.get("export_file_path", ""),
        "agent_status": record.get("agent_status", ""),
        "export_version": record.get("export_version", 1),
    }


def _current_user_id(db: Database, username: str) -> str:
    """取归属键 ``user_id``；查不到时回退用户名（与接口层同一口径）。"""
    try:
        user = db.get_user_by_username(username)
        if user and getattr(user, "user_id", ""):
            return user.user_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取用户 user_id 失败，回退为用户名: %s", exc)
    return username


def resolve_owned_expert(db: Database, username: str, expert_agent_id: str):
    """校验 ``expert_agent_id`` 是否为 ``username`自己创建的可用专家。

    供聊天接口在绑定「待修改专家」时调用（请求字段 ``pending_expert_id`` 来自
    前端，仍需服务端归属校验兜底）：

    - 只接受 ``ExpertStore.get_owned`` 命中的记录 —— 预置专家（不落库）与他人的
      专家都查不到，返回 ``None``；
    - ``agent_status`` 不是 active（生成失败 / 已停用）同样返回 ``None``。

    Returns:
        命中的专家记录 dict；不合法时 ``None``。
    """
    expert_agent_id = str(expert_agent_id or "").strip()
    if not expert_agent_id:
        return None
    try:
        store = ExpertStore(db)
        record = store.get_owned(expert_agent_id, _current_user_id(db, username))
    except Exception as exc:  # noqa: BLE001
        logger.warning("校验待修改专家归属失败 | %s | %s", expert_agent_id, exc)
        return None
    if record is None or record.get("agent_status") != STATUS_ACTIVE:
        return None
    return record


def _agent_config():
    """运行期配置（``{"config": Config, ...}``）；未初始化时返回 None。"""
    try:
        from easy_agent.bootstrap.runtime_config import get_runtime_agent_config

        cfg = get_runtime_agent_config()
        if cfg and cfg.get("config"):
            return cfg["config"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 Agent 配置失败: %s", exc)
    return None


def normalize_tag_options(raw) -> list[dict]:
    """把配置里的 ``expert_tags`` 规整成 ``[{"tag_id","tag_name"}]``。

    兼容 Pydantic 模型与裸 dict 两种形态（测试里可能注入 dict），空条目直接丢弃。
    """
    options: list[dict] = []
    for item in raw or []:
        if isinstance(item, dict):
            tag_id = str(item.get("tag_id") or "").strip()
            tag_name = str(item.get("tag_name") or "").strip()
        else:
            tag_id = str(getattr(item, "tag_id", "") or "").strip()
            tag_name = str(getattr(item, "tag_name", "") or "").strip()
        if tag_id and tag_name:
            options.append({"tag_id": tag_id, "tag_name": tag_name})
    return options


def _config_tag_options(config) -> list[dict]:
    """运行期配置里的分类标签候选（``config.expert_tags``）。"""
    return normalize_tag_options(getattr(config, "expert_tags", None))


def resolve_tag_id(value: str, options: list[dict]) -> str:
    """把 ``tag_id`` 或 ``tag_name`` 归一成候选列表里的 ``tag_id``。

    模型经常把中文标签名（"宏观研究"）填到 ``tag_id`` 里；这里两种写法都接受，
    对不上候选时返回空串（不落脏标签，与 ``pick_tag_id`` 同一口径）。
    """
    text = (value or "").strip()
    if not text or not options:
        return ""
    lowered = text.casefold()
    for item in options:
        if lowered == str(item.get("tag_id") or "").casefold():
            return str(item["tag_id"])
    for item in options:
        if lowered == str(item.get("tag_name") or "").casefold():
            return str(item["tag_id"])
    return ""


#: 工具基础描述（候选标签会在构造时追加，见 ``create_save_expert_tool``）
BASE_DESCRIPTION = (
    "创建或修改「专家 Agent」并持久化（落库 + 写版本目录）。"
    "用户要求新建专家 / 修改某个已有专家时使用。"
    "**创建前必须先按 expert-creator 技能的规范产出完整配置**（system_prompt 为核心，"
    "另含 capabilities/workflow/boundaries/description/skills_names 等），"
    "一次性填入本工具参数，不要分多次调用。"
    "修改时 action=update：expert_agent_id 由系统在用户点「编辑」时绑定，"
    "**不要从用户消息里取 ID，更不要猜测**；系统已绑定时可不传该参数，"
    "传了则必须与系统绑定值一致，否则调用会被拒绝。"
    "本工具返回创建/修改结果，请据此如实告知用户专家的名称、ID 与版本。"
)


class SaveExpertTool(BaseTool):
    name: str = "save_expert"
    description: str = BASE_DESCRIPTION
    args_schema: type = SaveExpertArgs

    username: str = ""
    session_id: str = ""
    #: 兜底生成配置所用的模型（``config.models`` 的 key）；空 = 跟随当前激活模型。
    #: 命名避开 ``model_`` 前缀：pydantic v2 的 protected_namespaces 会对该前缀告警。
    config_model: str = ""
    #: 分类标签候选（``config.expert_tags`` 规整后），随会话一起构造；
    #: 为空时在调用时回读运行期配置。
    tag_options: list[dict] = Field(default_factory=list)
    #: 会话级「待修改专家」绑定表（``{session_id: expert_agent_id}``），由
    #: ``agent_manager`` 持有并在聊天请求带 ``pending_expert_id`` 时写入。
    #: 修改专家时 ID 以这里为准（用户消息里不再携带专家 ID，防篡改）；
    #: 模型若显式传了 ``expert_agent_id``，必须与绑定值一致，否则拒绝。
    #: 两张表都是 ``agent_manager`` 那一份（``SkipValidation`` 防止 pydantic 复制）：
    #: 会话 Agent 会复用，绑定和本轮技能常常在工具装配之后才写入。
    edit_targets: SkipValidation[Optional[dict]] = None
    #: 会话 → 本轮输入框选中的技能目录名（``agent_manager`` 持有，供本工具落库）。
    #: 技能胶囊只在请求体的 ``skills`` 字段里，工具侧看不到，所以由装配根暂存一份。
    turn_skills: SkipValidation[Optional[dict]] = None

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("此工具仅支持异步调用，请使用 _arun")

    def _tags(self, config) -> list[dict]:
        return self.tag_options or _config_tag_options(config)

    # ── 内部：用户选定的技能包（本次改造） ──────────────────────────────

    def _selected_skill_dirs(self) -> list[str]:
        """本轮输入框选中的技能包 → canonical 目录名（已剔除造专家用的元技能）。

        技能胶囊只在聊天请求体的 ``skills`` 字段里，工具侧看不到，因此由
        ``agent_manager._session_turn_skills`` 暂存、``chat.py`` 每轮写入。

        返回空列表表示"用户一个都没选"——按需求语义，专家不绑定技能包，
        对话时保持技能库全量可见（= 改造前行为）。
        """
        sid = (self.session_id or "")[-5:] or "new"
        raw = [
            str(name).strip()
            for name in ((self.turn_skills or {}).get(self.session_id or "", []) or [])
            if str(name).strip()
        ]
        meta = [name for name in raw if name in _META_SKILL_DIRS]
        names = [name for name in raw if name not in _META_SKILL_DIRS]
        if meta:
            logger.info(
                f"[{sid}] 🧑‍🔬 保存专家：已剔除造专家用的元技能 {meta}（不写入专家技能包）"
            )
        if not names:
            logger.info(
                f"[{sid}] 🧑‍🔬 保存专家：本轮未选择技能包 | 用户={self.username} | "
                f"专家将不绑定技能（对话时技能库全量可见）"
            )
            return []

        from easy_agent.services import tool_scope

        kept, dropped = tool_scope.normalize_expert_skills(self.username, names, sid)
        logger.info(
            f"[{sid}] 🧑‍🔬 保存专家：技能包已归一 | 用户={self.username} | "
            f"原始={names} | 归一后目录名={kept} | 剔除={dropped or '无'}"
        )
        return kept

    # ── 内部：把工具参数整理成 generate_expert_config 的口径 ────────────

    def _structured_payload(self, args: SaveExpertArgs) -> Optional[dict]:
        """把结构化参数规整成配置 dict；缺核心字段时返回 ``None``（走兜底生成）。"""
        raw = {
            "system_prompt": args.system_prompt,
            "scenario": args.scenario,
            "capabilities": args.capabilities,
            "workflow": args.workflow,
            "boundaries": args.boundaries,
            "output_spec": args.output_spec,
            "features_prompt": args.features_prompt,
            "description": args.description,
            "skills_names": args.skills_names,
            "tag_id": args.tag_id,
            "name_en": args.name_en,
            "profession_zh": args.profession_zh,
            "profession_en": args.profession_en,
        }
        if not all(str(raw.get(k) or "").strip() for k in _REQUIRED_STRUCTURED):
            return None
        try:
            return normalize_config(raw)
        except ValueError as exc:
            logger.warning("结构化专家配置不合法，改用模型兜底生成: %s", exc)
            return None

    async def _resolve_config(
        self, args: SaveExpertArgs, config, name_cn: str, user_prompt: str
    ) -> tuple[dict, str]:
        """取得专家配置：优先结构化直传，缺失时调模型兜底。

        Returns:
            ``(config_dict, source)``；``source`` 取 ``"structured"`` / ``"llm"``。
        """
        tags = self._tags(config)
        structured = self._structured_payload(args)
        if structured is not None:
            # 分类标签：接受 tag_id 或中文 tag_name，统一归一成候选里的 tag_id；
            # 对不上候选就清空（不落脏标签，与接口层 pick_tag_id 同一口径）。
            structured["tag_id"] = resolve_tag_id(
                structured.get("tag_id", ""), tags
            ) or pick_tag_id(structured.get("tag_id", ""), tags)
            return structured, "structured"

        from easy_agent.agent.model import create_model

        llm = create_model(config, self.config_model or None, self.session_id or None)
        generated = await generate_expert_config(
            llm, name_cn, user_prompt, tag_options=tags
        )
        return generated, "llm"

    # ── 主流程 ────────────────────────────────────────────────────────

    async def _arun(self, action: str = "create", **kwargs) -> str:  # type: ignore[override]
        try:
            args = SaveExpertArgs(action=action, **kwargs)
        except Exception as exc:  # noqa: BLE001
            return f"错误：参数不合法（{exc}）。请检查 action 与各字段类型后重试。"

        if args.action == "update":
            return await self._update(args)
        return await self._create(args)

    async def _create(self, args: SaveExpertArgs) -> str:
        try:
            validate_create_params(args.name_cn, args.prompt)
        except ValueError as exc:
            return f"错误：{exc}。请向用户补齐信息后重试（不要编造专家描述）。"

        config = _agent_config()
        if config is None:
            return "错误：服务端配置未初始化，暂时无法创建专家，请稍后再试。"

        db = get_database()
        user_id = _current_user_id(db, self.username)
        store = ExpertStore(db)
        name_cn = args.name_cn.strip()

        conflict = store.find_by_name_cn(user_id, name_cn)
        if conflict:
            return (
                f"错误：已有同名的专家Agent「{conflict.get('export_name_cn') or name_cn}」，"
                "请修改专家名称后重试（名称需在本账号内唯一）。"
            )

        expert_agent_id, name_en_placeholder = build_expert_ids(name_cn)

        # 用户创建专家时选定的技能包（可为空 = 不绑定 = 对话时技能库全量可见）
        selected_skills = self._selected_skill_dirs()

        # 先落 building 占位行：生成途中崩溃也能查到记录，前端列表可见「生成中」
        store.create(
            {
                "export_agent_id": expert_agent_id,
                "session_id": self.session_id or "",
                "user_id": user_id,
                "export_name_en": name_en_placeholder,
                "export_name_cn": name_cn,
                "export_prompt": args.prompt,
                "export_description": "",
                "export_skills_names": [],
                "export_required_skills": selected_skills,
                "export_features_prompt": [],
                "export_file_path": "",
                "agent_status": STATUS_BUILDING,
                "export_version": 1,
            }
        )

        try:
            generated, source = await self._resolve_config(args, config, name_cn, args.prompt)
        except Exception as exc:  # noqa: BLE001
            store.update_fields(expert_agent_id, {"agent_status": STATUS_FAILED})
            logger.error("专家配置生成失败 | %s | %s", expert_agent_id, exc)
            return (
                f"错误：专家配置生成失败（{exc}）。已保留一条「生成失败」记录，"
                "请稍后重试或改用更明确的专家描述。"
            )

        ok = self._persist(
            store=store,
            config=config,
            username=self.username,
            expert_agent_id=expert_agent_id,
            name_cn=name_cn,
            version=1,
            generated=generated,
            required_skills=selected_skills,
        )
        if not ok:
            return (
                "错误：专家配置落盘失败（版本目录写入异常）。已保留「生成失败」记录，"
                "请稍后重试。"
            )

        # 技能暂存使命完成：清掉，避免下一轮无关的创建/修改误用上一轮的选择
        self._clear_turn_skills()

        record = store.get(expert_agent_id) or {}
        logger.info(
            "对话内创建专家成功 | user=%s | expert=%s | 来源=%s | tag=%s | 技能包=%s",
            self.username,
            expert_agent_id,
            source,
            record.get("tag_id", ""),
            record.get("export_required_skills") or "（未绑定）",
        )
        return self._success_text("创建", _to_public(record), source)

    async def _update(self, args: SaveExpertArgs) -> str:
        # 待修改专家 ID 的取值优先级：系统绑定值（用户点「编辑」时经请求结构化字段
        # 写入会话上下文，用户不可见、不可改）> 模型参数。两者同时存在且不一致时
        # 视为篡改，直接拒绝——消息体里的 ID 不可信（用户可任意修改）。
        bound_id = str((self.edit_targets or {}).get(self.session_id or "", "") or "").strip()
        supplied_id = (args.expert_agent_id or "").strip()
        if supplied_id and bound_id and supplied_id != bound_id:
            logger.warning(
                "修改专家被拒绝：模型传入的 expert_agent_id 与系统绑定值不一致 | "
                "session=%s | supplied=%s | bound=%s",
                self.session_id[-5:] if self.session_id else "-",
                supplied_id,
                bound_id,
            )
            return (
                "错误：本次会话待修改的专家与传入的 expert_agent_id 不一致，已拒绝执行。"
                "请回到专家面板重新点「编辑」，由系统带入正确的待修改专家。"
            )
        expert_agent_id = bound_id or supplied_id
        if not expert_agent_id:
            return (
                "错误：修改专家需要先在专家面板点「编辑」，由系统带入待修改的专家；"
                "不要凭记忆或猜测 expert_agent_id。"
            )

        config = _agent_config()
        if config is None:
            return "错误：服务端配置未初始化，暂时无法修改专家，请稍后再试。"

        db = get_database()
        user_id = _current_user_id(db, self.username)
        store = ExpertStore(db)

        record = store.get_owned(expert_agent_id, user_id)
        if record is None:
            return (
                f"错误：专家「{expert_agent_id}」不存在，或不属于当前用户，无法修改。"
                "请确认 ID 是否正确。"
            )

        name_cn = (args.name_cn or record.get("export_name_cn") or "").strip()
        user_prompt = (args.prompt or record.get("export_prompt") or "").strip()
        try:
            validate_create_params(name_cn, user_prompt)
        except ValueError as exc:
            return f"错误：{exc}。请补齐信息后重试。"

        conflict = store.find_by_name_cn(user_id, name_cn, exclude_agent_id=expert_agent_id)
        if conflict:
            return (
                f"错误：已有同名的专家Agent「{conflict.get('export_name_cn') or name_cn}」，"
                "请修改专家名称后重试。"
            )

        # 修改 = 新版本目录 v{n+1}，不覆盖 v{n}（运行中的旧会话不受影响）
        new_version = int(record.get("export_version") or 1) + 1

        # 技能包：本轮选了就整份替换（用户在编辑流程里重新选）；没选则沿用原记录，
        # 避免"只想改描述"的一次编辑把专家绑定清空。
        selected_skills = self._selected_skill_dirs()
        next_skills = selected_skills or None
        if next_skills is None:
            logger.info(
                f"[{expert_agent_id}] 修改专家：本轮未选择技能包，沿用原绑定 | "
                f"原={record.get('export_required_skills') or '（未绑定）'}"
            )
        else:
            logger.info(
                f"[{expert_agent_id}] 修改专家：技能包将替换 | "
                f"原={record.get('export_required_skills') or '（未绑定）'} | 新={next_skills}"
            )

        try:
            generated, source = await self._resolve_config(
                args, config, name_cn, user_prompt
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("专家配置生成失败（修改）| %s | %s", expert_agent_id, exc)
            return f"错误：专家配置生成失败（{exc}）。原专家保持不变，请稍后重试。"

        ok = self._persist(
            store=store,
            config=config,
            username=self.username,
            expert_agent_id=expert_agent_id,
            name_cn=name_cn,
            version=new_version,
            generated=generated,
            required_skills=next_skills,
        )
        if not ok:
            return "错误：专家配置落盘失败（版本目录写入异常）。原专家保持不变，请稍后重试。"

        store.update_fields(
            expert_agent_id, {"export_name_cn": name_cn, "export_prompt": user_prompt}
        )

        # 该用户所有缓存 Agent 失效，下次请求用新版本提示词重建
        try:
            from easy_agent.bootstrap.runtime_config import invalidate_user_agents

            invalidate_user_agents(self.username)
        except Exception as exc:  # noqa: BLE001
            logger.warning("专家修改后驱逐 Agent 缓存失败: %s", exc)

        self._clear_turn_skills()

        updated = store.get_owned(expert_agent_id, user_id) or {}
        logger.info(
            "对话内修改专家成功 | user=%s | expert=%s | v%s | 来源=%s | 技能包=%s",
            self.username,
            expert_agent_id,
            new_version,
            source,
            updated.get("export_required_skills") or "（未绑定）",
        )
        return self._success_text("修改", _to_public(updated), source)

    def _clear_turn_skills(self) -> None:
        """清掉本会话的「本轮技能暂存」（保存完成后调用），避免黏到下一次无关的创建。"""
        if self.turn_skills is not None:
            self.turn_skills.pop(self.session_id or "", None)

    # ── 落盘（两条路径共用） ───────────────────────────────────────────

    def _persist(
        self,
        *,
        store: ExpertStore,
        config: Any,
        username: str,
        expert_agent_id: str,
        name_cn: str,
        version: int,
        generated: dict,
        required_skills: Optional[list[str]] = None,
    ) -> bool:
        """写版本目录 + 刷新库记录；失败时把状态置 ``failed`` 并返回 False。

        ``required_skills``：专家绑定的技能包目录名。``None`` = 本次不修改该字段
        （修改专家且用户没重选时用），列表（含空列表）= 覆盖。
        """
        name_en = slugify(generated.get("name_en") or "", max_len=48)
        try:
            file_path = write_expert_version(
                username,
                expert_agent_id,
                version,
                name_cn,
                generated,
                name_en=name_en,
                model_name=self.config_model or getattr(config, "active_model", ""),
                raw_output=generated,
            )
        except Exception as exc:  # noqa: BLE001
            store.update_fields(expert_agent_id, {"agent_status": STATUS_FAILED})
            logger.error("专家版本目录写入失败 | %s | %s", expert_agent_id, exc)
            return False

        current = store.get(expert_agent_id) or {}
        fields: dict[str, Any] = {
            "export_file_path": file_path,
            "export_features_prompt": generated.get("features_prompt", []),
            "export_description": generated.get("description", ""),
            "export_skills_names": generated.get("skills_names", []),
            # 模型/Agent 给出的 tag_id 有效时覆盖；无效（不在候选内）时保留原值
            "tag_id": generated.get("tag_id") or current.get("tag_id", "") or "",
            "agent_status": STATUS_ACTIVE,
            "export_version": int(version),
        }
        if name_en:
            fields["export_name_en"] = name_en
        # 技能包：None = 本次不改（修改专家且未重选）；列表 = 覆盖（含清空）
        if required_skills is not None:
            fields["export_required_skills"] = list(required_skills)
        store.update_fields(expert_agent_id, fields)
        return True

    # ── 结果文案 ──────────────────────────────────────────────────────

    @staticmethod
    def _success_text(verb: str, record: dict, source: str) -> str:
        skills = "、".join(record.get("export_skills_names") or []) or "（未生成）"
        packages = record.get("export_required_skills") or []
        packages_text = (
            "、".join(packages)
            if packages
            else "（未绑定技能包 → 该专家对话时技能库全量可见）"
        )
        how = "由你直接产出" if source == "structured" else "由模型自动生成"
        return (
            f"专家{verb}成功！\n"
            f"- 专家名称：{record.get('export_name_cn', '')}\n"
            f"- 专家ID：{record.get('export_agent_id', '')}\n"
            f"- 英文标识：{record.get('export_name_en', '')}\n"
            f"- 版本：v{record.get('export_version', 1)}\n"
            f"- 功能描述：{record.get('export_description', '')}\n"
            f"- 技能标签：{skills}\n"
            f"- 绑定技能包：{packages_text}\n"
            f"- 配置来源：{how}\n"
            f"请如实告知用户：{verb}已完成，可在「专家 · 技能 · 连接器」面板的专家 Tab 中"
            f"看到并使用（点击「使用」即可由该专家回答）。"
            "不要向用户暴露服务端文件路径。"
        )


def create_save_expert_tool(
    *,
    username: str,
    session_id: str = "",
    config_model: str = "",
    tag_options=None,
    edit_targets: Optional[dict] = None,
    turn_skills: Optional[dict] = None,
) -> SaveExpertTool:
    """构造 ``save_expert`` 工具，并把分类标签候选写进工具描述。

    标签候选（``config.expert_tags``）随工具描述一起给模型看，模型就能**在生成配置的
    同一次输出里**选好 ``tag_id``——不需要为了分类再多调一次模型（原先弹窗方案的
    耗时主要来自这次额外调用）。

    ``edit_targets`` 为会话级「待修改专家」绑定表（``{session_id: expert_agent_id}``），
    由 ``agent_manager`` 持有：修改专家时 ID 以绑定值为准，用户消息里不再携带
    专家 ID（防篡改），见 ``SaveExpertTool.edit_targets``。

    ``turn_skills`` 为会话级「本轮输入框选中的技能目录名」
    （``{session_id: [dir_name, ...]}``），同由 ``agent_manager`` 持有：创建 / 修改专家时
    写进库字段 ``export_required_skills``，对话时按它做技能作用域硬隔离。
    """
    options = normalize_tag_options(tag_options)
    description = BASE_DESCRIPTION
    if options:
        lines = "\n".join(f"- {o['tag_id']}: {o['tag_name']}" for o in options)
        description += f"\n\n候选分类标签（tag_id: 名称），tag_id 只能取自这里：\n{lines}"
    return SaveExpertTool(
        username=username,
        session_id=session_id or "",
        config_model=config_model or "",
        tag_options=options,
        edit_targets=edit_targets,
        turn_skills=turn_skills,
        description=description,
    )
