"""专家 Agent 的非路由辅助逻辑（纯移动自 ``api/expert.py``）。

职责：用户/记录转换、配置读取、生成与落库、提示词加载与护栏、消息落库、
相关性预检、短路拒答事件流。路由层（``api/expert.py``）只做参数校验与编排。

依赖方向：本模块**不 import ``api.expert``**（避免循环）；需要的 repository
类型、模型与服务直接 import。``api/expert.py`` 反向 import 这些符号并通过
``__all__`` 保持旧内部名字（``expert_api._to_public`` 等）可用。
"""

import json
import logging
import time
from datetime import datetime
from typing import Optional

from fastapi import HTTPException

from easy_agent.db.expert_store import (
    STATUS_ACTIVE,
    STATUS_FAILED,
)
from easy_agent.db.repositories.expert_repository import ExpertRepository
from easy_agent.agent.model import create_model
from easy_agent.models.api import ChatRequest
from easy_agent.services import (
    expert_guardrail,
    expert_relevance,
    get_agent_config,
    public_expert_service,
)
from easy_agent.services.expert_service import (
    generate_expert_config,
    load_active_prompt,
    slugify,
    write_expert_version,
)
from easy_agent.services.session_thread import abandon_pending_approval, session_thread_in_use
from easy_agent.services.sse import SSEEvent
from easy_agent.services.sse_emitter import SSEEmitter
from easy_agent.services.streaming import build_assistant_message_dict
from easy_agent.utils import SessionLogger
from easy_agent.utils.prompt_probe import describe_prompt

logger = logging.getLogger(__name__)


# ── 辅助 ──────────────────────────────────────────────────────────────


def _current_user_id(repo: ExpertRepository, username: str) -> str:
    """取当前用户的 ``user_id``（专家表的归属键）。

    用户记录缺失时回退为用户名，保证「归属校验」在同一口径下自洽，
    不会因为一个查不到的用户而让整个接口 500。
    """
    try:
        user = repo.get_user_by_username(username)
        if user and getattr(user, "user_id", ""):
            return user.user_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取用户 user_id 失败，回退为用户名: %s", exc)
    return username


def _to_public(record: dict) -> dict:
    """把库记录转成对外的专家信息（隐藏无关列，列表型字段已经是数组）。"""
    if not record:
        return {}
    return {
        "export_agent_id": record.get("export_agent_id", ""),
        "session_id": record.get("session_id", ""),
        "tag_id": record.get("tag_id", ""),
        "export_name_en": record.get("export_name_en", ""),
        "export_name_cn": record.get("export_name_cn", ""),
        "export_prompt": record.get("export_prompt", ""),
        "export_description": record.get("export_description", ""),
        "export_skills_names": record.get("export_skills_names", []),
        "export_features_prompt": record.get("export_features_prompt", []),
        "export_file_path": record.get("export_file_path", ""),
        "agent_status": record.get("agent_status", ""),
        "export_version": record.get("export_version", 1),
        "create_timestamp": record.get("create_timestamp", ""),
        "update_timestamp": record.get("update_timestamp", ""),
    }


def _get_config():
    _cfg = get_agent_config()
    if not _cfg or not _cfg.get("config"):
        raise HTTPException(status_code=503, detail="Agent 配置未初始化，服务不可用")
    return _cfg["config"]


def _tag_options(config) -> list[dict]:
    """取配置里的专家分类标签候选（``expert_tags``）。

    兼容 Pydantic 模型与裸 dict 两种形态（测试里可能注入 dict），统一成
    ``[{"tag_id": ..., "tag_name": ...}]``；配置缺失时返回空列表。
    """
    raw = getattr(config, "expert_tags", None) or []
    options: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            tag_id = str(item.get("tag_id") or "").strip()
            tag_name = str(item.get("tag_name") or "").strip()
        else:
            tag_id = str(getattr(item, "tag_id", "") or "").strip()
            tag_name = str(getattr(item, "tag_name", "") or "").strip()
        if tag_id and tag_name:
            options.append({"tag_id": tag_id, "tag_name": tag_name})
    return options


def _ensure_name_available(
    store: ExpertRepository,
    user_id: str,
    name_cn: str,
    *,
    exclude_agent_id: str = "",
    is_update: bool = False,
) -> None:
    """校验专家中文名在本用户下唯一（创建/修改共用）。

    重名会让同一个列表里出现两张看不出区别的卡片，因此在**落库/生成之前**就
    拦住。比较口径为「忽略空白 + 忽略大小写」；**已删除（软删除）的专家不参与
    校验**——它们已从列表移除，名字视为可复用，否则「删掉重建同名专家」会被
    莫名其妙地拦住。

    Args:
        exclude_agent_id: 修改场景传入自身 ID，避免「和自己重名」。
        is_update: 提示语区分创建与修改（修改时额外引导用户改名）。

    Raises:
        HTTPException: 409 —— 已存在同名专家（``detail`` 为可直接展示的提示语）。
    """
    conflict = store.find_by_name_cn(user_id, name_cn, exclude_agent_id=exclude_agent_id)
    if not conflict:
        return

    detail = "已有同名的专家Agent，请修改专家名称" if is_update else "已有同名的专家Agent"
    raise HTTPException(status_code=409, detail=detail)


async def _generate_and_store(
    *,
    repo: ExpertRepository,
    username: str,
    expert_agent_id: str,
    name_cn: str,
    user_prompt: str,
    version: int,
    model_name: Optional[str],
    session_id: Optional[str],
) -> dict:
    """调 LLM 生成配置 -> 写版本目录 -> 刷新库记录。返回启用后的完整记录。

    生成内容包含卡片/详情页展示用的 ``export_description``（≤300 字）与
    ``export_skills_names``（3 个技能标签），以及大模型自动归类的 ``tag_id``
    （候选来自配置文件的 ``expert_tags``，不在候选内的会被清空后回退原值）。

    失败时把 ``agent_status`` 置为 ``failed``（前端可重试），并抛出可读错误。
    """
    config = _get_config()
    store = repo
    tag_options = _tag_options(config)
    try:
        llm = create_model(config, model_name, session_id)
        generated = await generate_expert_config(
            llm, name_cn, user_prompt, tag_options=tag_options
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("专家配置生成失败 | expert=%s | %s", expert_agent_id, exc)
        store.update_fields(expert_agent_id, {"agent_status": STATUS_FAILED})
        raise HTTPException(status_code=500, detail=f"专家配置生成失败：{exc}") from exc

    # 英文标识名：优先用 LLM 生成的 name_en（如 stock-invest-advisor），
    # 缺失/非法时回退为创建时的占位 slug。文件名不含随机数字（需求 §1）。
    name_en = slugify(generated.get("name_en") or "", max_len=48)

    try:
        file_path = write_expert_version(
            username,
            expert_agent_id,
            version,
            name_cn,
            generated,
            name_en=name_en,
            model_name=model_name or getattr(config, "active_model", ""),
            raw_output=generated,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("专家版本目录写入失败 | expert=%s | %s", expert_agent_id, exc)
        store.update_fields(expert_agent_id, {"agent_status": STATUS_FAILED})
        raise HTTPException(status_code=500, detail=f"专家配置落盘失败：{exc}") from exc

    # 大模型自动归类：生成的 tag_id 有效时覆盖创建时传入的值；
    # 无效/缺失（模型没选或选了候选之外的标签）时保留原值不动。
    generated_tag = generated.get("tag_id") or ""
    current_tag = (store.get(expert_agent_id) or {}).get("tag_id", "")

    fields = {
        "export_file_path": file_path,
        "export_features_prompt": generated.get("features_prompt", []),
        # 卡片/详情页展示字段
        "export_description": generated.get("description", ""),
        "export_skills_names": generated.get("skills_names", []),
        "tag_id": generated_tag or current_tag or "",
        "agent_status": STATUS_ACTIVE,
        "export_version": int(version),
    }
    if name_en:
        # LLM 生成了更贴切的英文标识名，回写覆盖创建时的占位 slug
        fields["export_name_en"] = name_en
    store.update_fields(expert_agent_id, fields)
    return store.get(expert_agent_id) or {}


def _expert_flags() -> tuple[bool, bool, bool]:
    """读专家会话安全边界相关的三个开关（读不到配置时按默认值）。

    Returns:
        ``(guardrail_enabled, relevance_check_enabled, relevance_strict)``；
        ``relevance_strict`` 默认 **False**（宽松：部分相关也放行，只拦无关与高危），
        与 ``expert.relevance_strict: false`` 的默认口径一致。
    """
    flags = (True, True, False)
    try:
        # get_agent_config() 返回的是 {"config": Config, ...} 而非 Config 本身
        cfg = get_agent_config()
        config = cfg.get("config") if isinstance(cfg, dict) else None
        if config is not None:
            flags = (
                bool(getattr(config, "expert_guardrail_enabled", True)),
                bool(getattr(config, "expert_relevance_check_enabled", True)),
                bool(getattr(config, "expert_relevance_strict", False)),
            )
    except Exception:  # noqa: BLE001 —— 配置异常时按默认处理
        pass
    return flags


def _with_guardrail(prompt_text: str) -> str:
    """给专家提示词追加统一的「专家职责边界」块（预置与自建专家都生效）。

    在 `_load_expert_prompt` 的两个成功分支统一收口：预置专家的提示词是固定文件、
    LLM 生成的也不保证自带边界，运行时程序化拼接是唯一可靠的兜底（见
    services/expert_guardrail.py）。``expert.guardrail_enabled=false`` 时不拼接；
    第 2 条口径跟随 ``expert.relevance_strict``。
    """
    guardrail_enabled, _check_enabled, strict = _expert_flags()
    return expert_guardrail.append_guardrail(
        prompt_text, enabled=guardrail_enabled, strict=strict
    )


def _load_expert_prompt(
    repo: ExpertRepository, username: str, expert_agent_id: str, sid: str
) -> tuple[str, str, str]:
    """解析一个专家 ID 对应的提示词（「我的专家」优先，「公共专家」兜底）。

    解析顺序故意如此：同一个 ID 若同时存在于用户库与公共目录，**以用户自己的为准**
    （用户可编辑的那份），避免预置内容无提示地覆盖用户改动。

    Args:
        expert_agent_id: 前端回传的专家 ID（我的专家 ID 或公共专家目录名）。

    Returns:
        ``(expert_id, prompt_text, source)``；``prompt_text`` 为空串表示读取失败
        （调用方降级为通用 Agent，不阻断对话）；``source`` 取 ``"mine"`` / ``"public"``
        / ``""``。

    Raises:
        HTTPException: 404 —— 我的专家与公共专家都找不到该 ID；
            400 —— 命中的是我的专家但状态不是「可用」（生成失败 / 已停用）。
    """
    if not expert_agent_id:
        return "", "", ""

    # ── 1. 我的专家（数据库归属校验） ──────────────────────────────────
    user_id = _current_user_id(repo, username)
    record = repo.get_owned(expert_agent_id, user_id)
    if record is not None:
        if record.get("agent_status") != STATUS_ACTIVE:
            raise HTTPException(
                status_code=400, detail="专家已停用或生成失败，无法执行"
            )
        prompt_text, version, version_dir = load_active_prompt(username, expert_agent_id)
        if prompt_text.strip():
            logger.info(
                f"[{sid}] 📌 专家提示词已加载 | 来源=我的专家 | expert={expert_agent_id} | "
                f"v{version} | {describe_prompt('内容', prompt_text)} | dir={version_dir}"
            )
            return expert_agent_id, _with_guardrail(prompt_text), "mine"
        # 磁盘目录被清 / 文件缺失：降级为通用 Agent，不阻断用户对话
        logger.warning(
            f"[{sid}] 专家提示词读取失败，本轮降级为通用 Agent | 来源=我的专家 | "
            f"expert={expert_agent_id} | v{version} | {version_dir}"
        )
        return "", "", ""

    # ── 2. 公共（预置）专家：只读目录，不落库 ─────────────────────────
    public = public_expert_service.find_public_expert(expert_agent_id)
    if public is None:
        raise HTTPException(status_code=404, detail="专家不存在")

    prompt_text, version, prompt_path = public_expert_service.load_public_prompt(
        expert_agent_id
    )
    if prompt_text.strip():
        logger.info(
            f"[{sid}] 📌 专家提示词已加载 | 来源=公共专家 | expert={expert_agent_id} | "
            f"v{version} | {describe_prompt('内容', prompt_text)} | file={prompt_path}"
        )
        return expert_agent_id, _with_guardrail(prompt_text), "public"

    logger.warning(
        f"[{sid}] 专家提示词读取失败，本轮降级为通用 Agent | 来源=公共专家 | "
        f"expert={expert_agent_id} | v{version} | {prompt_path}"
    )
    return "", "", ""


def _expert_meta_for_message(
    repo: ExpertRepository, username: str, expert_id: str
) -> Optional[dict]:
    """取「随消息落库」的专家元信息（``{"id": ..., "name": ...}``）。

    用于回答面板的专家标识：把本轮专家写进用户消息随消息持久化，前端渲染 assistant
    消息时取其上一条用户消息的该字段展示「头像 + 名称」，因此**刷新 / 历史加载后不丢**，
    同一会话中不同轮次选了不同专家也能各自对应。

    「我的专家」（数据库）优先，「公共专家」（目录）兜底；两处都取不到中文名时返回
    ``None``（调用方不写该字段，前端按无专家兜底），不阻断本轮对话。
    """
    if not expert_id:
        return None

    name_cn = ""
    try:
        record = repo.get_owned(expert_id, _current_user_id(repo, username))
        if record:
            name_cn = str(record.get("export_name_cn") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("专家名称读取失败（我的专家）| expert=%s | %s", expert_id, exc)

    if not name_cn:
        try:
            public = public_expert_service.find_public_expert(expert_id)
            if public:
                name_cn = str(public.get("export_name_cn") or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("专家名称读取失败（公共专家）| expert=%s | %s", expert_id, exc)

    if not name_cn:
        return None
    return {"id": expert_id, "name": name_cn}


def _as_str_list(value) -> list[str]:
    """把库里的 JSON 数组字段（可能是 str / list / None）规整成字符串列表。"""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return [value.strip()] if value.strip() else []
        value = parsed
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _expert_profile(repo: ExpertRepository, username: str, expert_id: str) -> dict:
    """取专家画像（中文名 / 功能描述 / 技能标签 / 场景 / 能力清单 / 分类名）。

    「我的专家」（数据库）优先、「公共专家」（目录）兜底，与 `_load_expert_prompt`
    的解析顺序一致；两处都取不到时返回空 dict（预检会因画像为空而跳过 → 放行）。

    ``scenario`` / ``capabilities`` / ``tag_name`` 从本次改造起纳入画像：此前只喂
    名称 + 描述 + 技能标签，模型看不到专家「能产出日报/周报/策略报告」这类能力清单，
    于是把「分析文件里的金融数据出一份报告」判成越界（实测误拒）。
    """
    profile: dict = {}
    try:
        record = repo.get_owned(expert_id, _current_user_id(repo, username))
        if record:
            profile = {
                "name_cn": str(record.get("export_name_cn") or "").strip(),
                "description": str(record.get("export_description") or "").strip(),
                "skills": _as_str_list(record.get("export_skills_names")),
                "tag_id": str(record.get("tag_id") or "").strip(),
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("专家画像读取失败（我的专家）| expert=%s | %s", expert_id, exc)

    # 描述为空说明命中的是公共专家（或我的专家还没生成描述），用目录里的补齐
    if not profile.get("description"):
        try:
            public = public_expert_service.find_public_expert(expert_id)
            if public:
                profile = {
                    "name_cn": profile.get("name_cn")
                    or str(public.get("export_name_cn") or "").strip(),
                    "description": str(public.get("export_description") or "").strip(),
                    "skills": _as_str_list(public.get("export_skills_names")),
                    "tag_id": str(public.get("tag_id") or "").strip(),
                    "scenario": str(public.get("export_scenario") or "").strip(),
                    "capabilities": _as_str_list(public.get("export_capabilities")),
                }
        except Exception as exc:  # noqa: BLE001
            logger.warning("专家画像读取失败（公共专家）| expert=%s | %s", expert_id, exc)

    profile["tag_name"] = _expert_tag_name(profile.get("tag_id", ""))
    return profile


def _expert_tag_name(tag_id: str) -> str:
    """把 tag_id 换成中文分类名（画像里给模型看「债券投研」比看 ID 有用得多）。"""
    if not tag_id:
        return ""
    try:
        cfg = get_agent_config()
        config = cfg.get("config") if isinstance(cfg, dict) else None
        for tag in getattr(config, "expert_tags", None) or []:
            if str(getattr(tag, "tag_id", "")) == tag_id:
                return str(getattr(tag, "tag_name", "") or "").strip()
    except Exception:  # noqa: BLE001 —— 分类名只是辅助信息，取不到不影响预检
        pass
    return ""


def _precheck_llm():
    """预检用的模型：复用进程内已初始化的全局实例，不新建、不指定新模型。

    函数内 import 是必须的：``_llm_instance`` 在 lifespan 里才被赋值，模块顶层
    ``from ... import _llm_instance`` 会固定成初始化前的 None。
    """
    from easy_agent.services.agent_manager import _llm_instance

    return _llm_instance


def _precheck_disable_thinking() -> bool:
    """预检调用是否关闭思考模式（``expert.relevance_disable_thinking``，默认开）。

    三分类只需一行 JSON，思考只会把一次 0.6 秒的调用拖成 4 秒。
    """
    try:
        cfg = get_agent_config()
        config = cfg.get("config") if isinstance(cfg, dict) else None
        if config is not None:
            return bool(getattr(config, "expert_relevance_disable_thinking", True))
    except Exception:  # noqa: BLE001 —— 配置异常时按默认（关思考）处理
        pass
    return True


def _session_logger_for(session_id: str, username: str) -> SessionLogger:
    """本轮会话日志器（正常链路与短路拒答链路共用）。"""
    return SessionLogger(
        session_id=session_id,
        username=username,
        workspace="",
        system_prompt="",
    )


def _persist_user_turn(
    repo: ExpertRepository,
    session_id: str,
    username: str,
    request: ChatRequest,
    expert_id: str,
    turn_skill_dirs: list[str],
    *,
    turn_id: str = "",
) -> dict:
    """落本轮用户消息（正常链路与短路拒答链路共用，保证两种情况下会话都完整）。

    本轮使用的专家随用户消息一起落库（回答面板标识的数据来源，见
    `_expert_meta_for_message`）：刷新/历史加载后 assistant 消息仍能显示专家头像+
    名称，同一会话不同轮次各自对应自己那一轮的专家。
    """
    expert_meta = _expert_meta_for_message(repo, username, expert_id)
    user_message = {
        "role": "user",
        "content": request.message,
        "timestamp": datetime.now().isoformat(),
    }
    if turn_id:
        # DA-03：本轮 id（= message_id），与 thread 里本轮 HumanMessage 的 id 一致
        user_message["turn_id"] = turn_id
    if expert_meta:
        user_message["expert"] = expert_meta
    if request.files:
        user_message["files"] = request.files
    # 结构化技能目录名：气泡按它渲染技能标签，不再需要从消息文本里反扒 `/目录名`
    if turn_skill_dirs:
        user_message["skills"] = turn_skill_dirs
    # DA-03：会话 thread 下新输入会丢弃上一轮挂起的中断，DB 里的待审批同步标为拒绝
    if session_thread_in_use(get_agent_config()):
        abandon_pending_approval(repo, session_id)
    repo.add_message(session_id, user_message)
    return user_message


def _recent_context(
    repo: ExpertRepository, session_id: str, max_messages: int = 4, max_chars: int = 300
) -> str:
    """取最近几轮对话用于消解指代（「继续」「上面第三点」这类追问）。

    只喂**很短**的尾部片段：预检的成本要压住，判据仍是专家画像 + 本轮正文。
    """
    try:
        session = repo.get_session(session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("预检上下文读取失败（忽略）| session=%s | %s", session_id, exc)
        return ""
    messages = getattr(session, "messages", None) or []
    lines: list[str] = []
    for msg in messages[-max_messages:]:
        role = msg.get("role") if isinstance(msg, dict) else None
        if role not in ("user", "assistant"):
            continue
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{'用户' if role == 'user' else '专家'}：{content[:max_chars]}")
    return "\n".join(lines)


async def _relevance_gate(
    repo: ExpertRepository,
    session_id: str,
    username: str,
    expert_id: str,
    message: str,
    sid: str,
    files: Optional[list[dict]] = None,
    prompt_text: str = "",
) -> Optional[tuple[str, str]]:
    """专家会话的输入侧相关性预检（硬闸）。

    在创建 Agent **之前**判断本轮消息是否落在专家职责范围内：与专家领域无关直接拒答，
    此时 Agent 不会被创建、任何工具都不可达（这是提示词层面的边界块做不到的）。

    判定顺序有意如此：
    1. **高危红线先行**（``detect_unsafe_request``，纯规则、不调模型）——删除/修改文件、
       改配置、重启、读写密钥，无论 ``relevance_strict`` 多宽松都直接拒答；
    2. 再做 LLM 四分类（relevant / adjacent / partial / irrelevant），只有
       ``irrelevant`` 无条件拒答，``partial`` 按 strict 口径，``adjacent`` 放行。

    Returns:
        ``None`` 表示放行；``(classification, refusal_text)`` 表示应短路拒答。
        预检关闭 / 模型未就绪 / 画像为空 / 调用异常或超时，一律返回 ``None``。
    """
    if not expert_id or not (message or "").strip():
        return None

    _guardrail_enabled, check_enabled, strict = _expert_flags()
    if not check_enabled:
        return None

    profile = _expert_profile(repo, username, expert_id)
    expert_name = profile.get("name_cn", "")

    # ── 1. 高危红线：不依赖模型、不受 strict 口径影响 ────────────────────
    if expert_relevance.detect_unsafe_request(message):
        logger.info(
            f"[{sid}] ⛔ 高危操作红线命中（删除/修改文件、改配置、重启、密钥类）"
            f"| expert={expert_id} | 直接短路拒答 | Agent 未创建"
        )
        return expert_relevance.UNSAFE, expert_relevance.build_refusal(
            expert_name, expert_relevance.UNSAFE
        )

    # ── 2. 相关性四分类（一次轻量 LLM 调用，关思考模式） ────────────────
    llm = _precheck_llm()
    if llm is None:
        logger.warning(f"[{sid}] 🩺 相关性预检跳过：全局模型未初始化，本轮放行")
        return None

    # 「我的专家」库里没有能力清单，从提示词 md 的「## 核心能力」段抽取；
    # 公共专家的能力清单已随目录 config.yaml 读出（见 _expert_profile）。
    capabilities = profile.get("capabilities") or expert_relevance.extract_capabilities(
        prompt_text
    )
    basis = expert_relevance.build_basis(
        expert_name,
        profile.get("description", ""),
        profile.get("skills") or [],
        capabilities=capabilities,
        scenario=profile.get("scenario", ""),
        tag_name=profile.get("tag_name", ""),
    )
    if not basis:
        logger.warning(
            f"[{sid}] 🩺 相关性预检跳过：专家画像为空，本轮放行 | expert={expert_id}"
        )
        return None

    attachments = [
        str(f.get("filename") or "").strip()
        for f in (files or [])
        if isinstance(f, dict) and str(f.get("filename") or "").strip()
    ]

    started = time.time()
    classification = await expert_relevance.check_relevance(
        llm,
        basis,
        message,
        context=_recent_context(repo, session_id),
        disable_thinking=_precheck_disable_thinking(),
        attachments=attachments,
    )
    elapsed = time.time() - started

    if classification is None:
        logger.warning(
            f"[{sid}] 🩺 相关性预检未给出结论（异常/超时/解析失败，{elapsed:.2f}s）"
            f"| 本轮放行 | expert={expert_id}"
        )
        return None

    reject = expert_relevance.should_reject(classification, strict=strict)
    logger.info(
        f"[{sid}] 🩺 相关性预检 | expert={expert_id} | 判定={classification} | "
        f"口径={'严格' if strict else '宽松'} | 结果={'拒答' if reject else '放行'} | "
        f"耗时={elapsed:.2f}s"
    )
    if not reject:
        return None

    return classification, expert_relevance.build_refusal(
        profile.get("name_cn", ""), classification
    )


async def _refusal_event_stream(session_id: str, repo: ExpertRepository, answer: str):
    """短路拒答的极简 SSE 流：start → content → content_end → done。

    事件形状与 ``services/streaming.py`` 完全一致，前端复用同一套解析逻辑；
    先落库再推送，保证即使前端断开，拒答记录也已完整持久化（会话列表不留半句）。
    """
    sid = session_id[-5:] if session_id else "new"
    started = time.time()

    try:
        message = build_assistant_message_dict(
            content=answer,
            thinking="",
            thinking_duration=None,
            tool_call_records=[],
            blocks=[{"type": SSEEvent.CONTENT, "order": 0, "content": answer, "step": 0}],
            input_tokens=0,
            output_tokens=0,
            context_tokens=0,
            elapsed_time=time.time() - started,
            step_count=0,
        )
        repo.update_last_assistant_message(session_id, message)
        repo.update_last_assistant_message_row(session_id, message)
    except Exception as exc:  # noqa: BLE001 —— 落库失败不阻断拒答推送
        logger.warning(f"[{sid}] 拒答消息持久化失败: {exc}")

    emitter = SSEEmitter(session_id)
    yield emitter.start()
    yield emitter.content(answer, step=0)
    yield emitter.content_end()
    yield emitter.done(
        elapsed_time=round(time.time() - started, 2),
        usage={
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "context_length": None,
            "auto_compress_tokens": None,
            "context_tokens": 0,
            "step_count": 0,
        },
    )
