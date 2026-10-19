"""专家会话「相关性预检」硬闸（输入侧，Agent 不启动）。

背景：``services/expert_guardrail.py`` 的「专家职责边界」块是**软约束**——它只是在
提示词里要求模型拒答，模型不遵守时仍会作答。实测：预置专家「利率债烟头专家」在
用户用「只验证权限、不用真的删」这类缓和措辞提问时，绕过了边界块给出 attrib /
icacls 的权限操作说明。提示词层面给不出「一定拒答」的保证。

因此在创建 Agent、落工具之前补一层**外部预检**：用一次轻量 LLM 调用，把用户本轮
消息与专家职责画像做四分类。

    relevant   主题在领域内       → 放行
    adjacent   相邻金融领域       → 放行（如用利率债专家画像去分析个股财报：
                                   允许作答，只是要说明侧重不同）
    partial    领域内但夹带越界子任务 → ``expert.relevance_strict=true`` 时整条拒答，
                                   默认宽松（false）放行，交给边界块「只答相关部分」
    irrelevant 与金融投研无关      → 短路拒答；Agent 不创建，任何工具都不可达

另有**独立于模型、独立于 strict 口径的高危红线**（``detect_unsafe_request``）：
只要用户消息要求删除/修改文件、改动系统配置、重启服务、读写密钥，无论口径多宽松、
无论模型怎么判，都直接短路拒答。这是「宁可少拦领域问题、也必须拦住破坏性操作」
的落点——它不依赖模型自觉。

设计要点：
- **只看主题，不看手段**：写代码、爬数据、读上传文件、清洗数据、制表绘图、出报告都
  是投研的常规工作方式，不作为越界依据（实测「写 Python 爬利率数据再回答利率问题」
  曾被判 partial 而误拒）。
- **fail-open**：预检异常 / 超时 / 输出无法解析时一律放行。宁可在边缘场景漏拦，
  也不能因为一次预检故障把用户挡在门外（可用性优先于严格性）。
- **不指定新模型**：复用进程内已初始化的全局 LLM（``agent_manager._llm_instance``），
  由调用方传入，便于测试注入假模型。
- **成本可控**：仅在「专家会话 + 有正文」时触发一次，输出限制为一行 JSON；
  且调用时**关闭思考模式**（见 ``_NO_THINKING_KWARGS``）。

配置（``config/config.yaml`` 的 ``expert`` 段）：
    expert:
      relevance_check_enabled: true    # 关闭后完全不预检，退回纯软约束
      relevance_strict: false          # partial 是否整条拒答（默认宽松）
      relevance_disable_thinking: true # 预检调用是否关闭思考模式
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Final, Optional

logger = logging.getLogger(__name__)

#: 四分类结果
RELEVANT: Final = "relevant"
ADJACENT: Final = "adjacent"
PARTIAL: Final = "partial"
IRRELEVANT: Final = "irrelevant"
#: 高危操作（不在四分类里，由 ``detect_unsafe_request`` 独立判定，永远拒答）
UNSAFE: Final = "unsafe"

#: 预检超时（秒）：超过则放行。关掉思考模式后一次分类通常 0.5~1.5 秒，
#: 8 秒足以覆盖尾延迟（未关思考时实测 1.7~6.4 秒）。
DEFAULT_TIMEOUT: Final = 8.0

#: 关闭思考模式的请求参数（OpenAI 兼容接口）。
#:
#: DeepSeek 等「混合思考」模型默认会先输出一大段隐藏的 reasoning token 再给答案：
#: 实测同一条预检提示词 `output_tokens` 从 7 涨到 258~1007，耗时 1.7~6.4 秒，
#: 而分类结论只有一行 JSON、根本不需要思考。下面两个参数分别对应
#: 「推理强度」与「thinking 开关」两种网关口径（DeepSeek / vLLM / Anthropic 代理），
#: 同时下发时**只要网关认其中一个就能生效**；都不认的网关会忽略（实测可用），
#: 若个别网关因此报 400，`check_relevance` 会自动去掉它们重试一次。
_NO_THINKING_KWARGS: Final = {
    "reasoning_effort": "none",
    "extra_body": {"thinking": {"type": "disabled"}},
}

#: 判定请求被网关拒绝（参数字段不支持）的异常特征，用于去掉关思考参数后重试。
#: 刻意写得具体：光有 "400" 可能只是 token 数或普通错误，不该触发重试。
_REJECT_HINTS: Final = (
    "400 bad request",
    "error code: 400",
    "invalid_request",
    "unsupported parameter",
    "unknown field",
    "unrecognized",
    "extra fields",
    "reasoning_effort",
    "thinking",
)

#: 专家画像 / 用户消息的截断长度：预检只需要「领域在哪」这一层信息。
#: 关掉思考模式后多几百字的画像对耗时几乎无影响（0.3~0.8s），因此放宽到 1000。
_MAX_BASIS_CHARS: Final = 1000
_MAX_MESSAGE_CHARS: Final = 2000

#: 画像末尾固定追加的一段：把「手段」显式排除在越界判定之外。
#: 实测漏判/误判的一大来源就是把「写代码、爬数据、出报告」当成越界。
_ALLOWED_METHODS: Final = (
    "允许的通用工作方式（属于本职工作，不构成越界）：读取用户上传的文件与数据、"
    "联网检索数据、编写代码做数据清洗与测算、制表绘图、产出分析报告/日报/周报/总结。"
)

_CLASSIFIER_SYSTEM: Final = """你是一个投研专家的「问题分诊器」，只输出一行 JSON，不输出任何解释。

第一步：找出用户消息的**主题**（他想研究、了解或产出的对象）。
第二步：判断这个主题与「专家职责画像」的关系，四选一：
- "relevant" ：主题就在专家领域内；
- "adjacent" ：主题属金融投研、但不在专家专精范围（例如用债券专家的画像去分析个股财报、
  信用债、可转债、外汇、商品）——这类**允许作答**，只需说明侧重不同；
- "partial"  ：主题在领域内，但同时夹带了明确越界的新诉求（例如顺便删文件、改系统配置）；
- "irrelevant"：主题与金融投研无关，或本身就是删除/修改文件、修改系统配置、重启服务、
  读写密钥、执行与投研无关的系统操作等诉求。

关键规则（务必遵守）：
1. **只看主题，不看手段**。写代码、爬取网络数据、读取用户上传的文件、清洗数据、
   做表格、画图表、生成分析报告/日报/周报——这些都是投研的常规工作方式，**一律
   不算越界**，不得因此判 partial 或 irrelevant。只有当"写代码/执行命令"本身就是
   目的、与投研主题无关时（例如纯粹要一段排序算法），才算越界。
2. **带附件时默认在范围内**：用户上传了文件并让你分析，说明他要分析的就是这份材料；
   除非正文明确要求删除文件、改配置等破坏性操作，否则按 relevant 处理。
3. **拿不准就放行**：边界模糊、信息不足、只有一句话的追问，判 "relevant"，
   不要判 irrelevant。
4. 「会话上下文」只用于消解指代（「继续」「上面第三点」「整理成报告」），
   追问按上文主题判定；但上下文不改变上述判定标准。

只输出一行 JSON：{"relevance": "relevant" | "adjacent" | "partial" | "irrelevant"}
"""

# 只认这四个词，按「最具体优先」排列：
# "irrelevant" 尾部含 "relevant"，"adjacent" 不含 → irrelevant 必须排最前；
# JSON 键名 "relevance" 不会命中任何一个（relevance ≠ relevant）。
_CLASSIFICATION_RE = re.compile(r"irrelevant|partial|adjacent|relevant", re.IGNORECASE)

REFUSAL_IRRELEVANT_TEMPLATE: Final = (
    "这个问题超出了「{name}」的专业范围，请提问我领域内的问题。"
)

REFUSAL_PARTIAL_TEMPLATE: Final = (
    "这条消息里有部分内容超出了「{name}」的专业范围，按当前口径（严格模式）整条消息"
    "不再作答。\n\n"
    "请提问我领域内的问题!"
)

REFUSAL_UNSAFE_TEMPLATE: Final = (
    "这属于删除/修改文件、改动系统配置一类的高危操作，超出了「{name}」的职责范围，"
    "我不会执行。\n\n"
    "需要整理数据、分析文件或生成报告，我可以直接在工作区新建文件来做；"
    "请提问我领域内的问题。"
)

# ── 高危操作红线（确定性规则，不依赖模型） ─────────────────────────────
#
# 实测模型会把「顺便把 system.md 删了」判成 partial，而宽松口径下 partial 是放行的；
# 因此破坏性诉求必须有一条不依赖模型、不依赖 strict 口径的确定性拦截。
# 规则刻意写得窄：只认「动词 + 文件/配置类对象」在 20 字窗口内共现，避免
# 「分析删除某个字段对估值的影响」这类正常提问被误伤。

_UNSAFE_VERBS: Final = (
    "删除", "删掉", "删了", "删除掉", "清除", "清空", "移除", "覆盖", "重写",
    "改写", "修改", "改掉", "重命名", "移动到", "rm", "del", "format",
)
_UNSAFE_OBJECTS: Final = (
    "文件", "目录", "文件夹", "配置", "配置项", "system.md", "system", ".env",
    "settings", "config", ".yaml", ".yml", ".md", ".py", ".sh", "日志文件",
    "excel", "xlsx", "csv", "docx", "表格文件",
)
_UNSAFE_OPS: Final = (
    "重启", "关机", "停止服务", "停止运行", "提权", "sudo", "chmod", "chown",
    "rm -rf", "rm -r", "清库", "删库", "drop table", "truncate table",
    "读取密钥", "导出密钥", "api key", "api_key", "环境变量文件",
)
#: 否定前缀：让「不要删除」「无需修改」这类正常表述通过
_NEGATION_PREFIX_RE: Final = re.compile(
    r"(不要|别|不必|无需|不用|不需要|禁止|不能|不得|切勿|请勿)[^。；;！!？?]{0,6}$"
)
#: 动词 + 对象共现窗口（字符）
_UNSAFE_WINDOW: Final = 20


def _as_list(value: Any) -> list[str]:
    """把字符串/可迭代统一成去空字符串列表（防止传字符串时被逐字符拆开）。"""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return [str(item).strip() for item in value if str(item).strip()]


def build_basis(
    name_cn: str = "",
    description: str = "",
    skills: Optional[list[str]] = None,
    max_chars: int = _MAX_BASIS_CHARS,
    capabilities: Optional[list[str]] = None,
    scenario: str = "",
    tag_name: str = "",
) -> str:
    """把专家元信息拼成「职责画像」文本（预检的判据）。

    只要有一段可用信息就能判断领域；全都为空时返回空串——调用方据此跳过预检
    （fail-open），避免拿空画像去问模型反而得到一个随机的 irrelevant。

    ``capabilities`` / ``scenario`` 是本次新增的关键信息：此前只用
    「名称 + 功能描述 + 技能标签」，模型看不到「产出口径的日报/周报/策略报告」
    「分析资金面指标」这类**能力清单**，于是把「分析文件里的金融数据出报告」
    判成越界（实测误拒）。末尾固定追加 ``_ALLOWED_METHODS``，把写代码/爬数据/
    出报告这类手段显式排除在越界之外。
    """
    lines: list[str] = []
    if (name_cn or "").strip():
        lines.append(f"名称：{name_cn.strip()}")
    if (tag_name or "").strip():
        lines.append(f"分类：{tag_name.strip()}")
    if (description or "").strip():
        lines.append(f"职责描述：{description.strip()}")
    if (scenario or "").strip():
        lines.append(f"典型场景：{scenario.strip()}")
    picked = _as_list(skills)
    if picked:
        lines.append("技能标签：" + "、".join(picked))
    caps = _as_list(capabilities)
    if caps:
        lines.append("核心能力：\n" + "\n".join(f"- {c}" for c in caps[:6]))
    if not lines:
        # 一条信息都没有 → 返回空串，调用方据此跳过预检（不能只用默认段去问模型）
        return ""
    lines.append(_ALLOWED_METHODS)
    return "\n".join(lines).strip()[:max_chars]


#: 从专家提示词 md 中抽取「核心能力」段落的小标题匹配
_CAPABILITY_HEADING_RE: Final = re.compile(r"^#{1,4}\s*[^\n]{0,10}能力[^\n]*$", re.M)
_HEADING_RE: Final = re.compile(r"^#{1,4}\s", re.M)
_BULLET_RE: Final = re.compile(r"^\s*(?:[-*+]|\d+[.、)])\s*")


def extract_capabilities(prompt_text: str, limit: int = 6) -> list[str]:
    """从专家提示词里抽「核心能力」小节的条目，作为画像补充。

    「我的专家」在数据库里只有功能描述（≤300 字）与技能标签，没有能力清单；但生成
    的提示词 md 里一定有 ``## 核心能力`` 段（见 ``expert_service.build_expert_md``），
    从那里取是最省事、也不需要改表结构的做法。取不到就返回空列表（不影响预检）。
    """
    text = prompt_text or ""
    if not text.strip():
        return []
    heading = _CAPABILITY_HEADING_RE.search(text)
    if heading is None:
        return []
    rest = text[heading.end():]
    following = _HEADING_RE.search(rest)
    section = rest[: following.start()] if following else rest
    items: list[str] = []
    for line in section.splitlines():
        raw = line.strip()
        if not raw:
            continue
        item = _BULLET_RE.sub("", raw).strip()
        if item:
            items.append(item[:120])
        if len(items) >= limit:
            break
    return items


def parse_classification(raw: Any) -> Optional[str]:
    """从模型输出里解析四分类标签；无法识别时返回 ``None``（调用方放行）。"""
    if raw is None:
        return None
    match = _CLASSIFICATION_RE.search(str(raw))
    return match.group(0).lower() if match else None


def detect_unsafe_request(message: str) -> bool:
    """确定性识别「高危操作」诉求（删除/修改文件、改配置、重启、读写密钥）。

    这层**不依赖模型、也不受 strict 口径影响**：宽松口径下 ``partial`` 会放行，
    但破坏性诉求必须拦住。规则刻意写得窄——只在「动词 + 对象」或明确的操作词上
    命中，且排除「不要删除」「无需修改」这类否定表述，避免误伤正常提问。
    """
    text = (message or "").strip()
    if not text:
        return False
    lowered = text.lower()

    for op in _UNSAFE_OPS:
        if op in lowered:
            return True

    for verb in _UNSAFE_VERBS:
        start = 0
        while True:
            idx = lowered.find(verb, start)
            if idx < 0:
                break
            # 否定表述（「不要删除」「无需修改」）不算高危诉求
            if not _NEGATION_PREFIX_RE.search(lowered[max(0, idx - 12): idx]):
                window = lowered[max(0, idx - _UNSAFE_WINDOW): idx + len(verb) + _UNSAFE_WINDOW]
                if any(obj in window for obj in _UNSAFE_OBJECTS):
                    return True
            start = idx + len(verb)
    return False


def should_reject(classification: Optional[str], strict: bool = True) -> bool:
    """按口径决定是否短路拒答。

    - ``irrelevant`` → 一律拒答（与 strict 无关，这是硬闸的核心价值）；
    - ``UNSAFE``（高危操作）→ 一律拒答（由 ``detect_unsafe_request`` 独立判定）；
    - ``partial``    → 严格口径拒答，宽松口径（默认）放行交给提示词内边界块；
    - ``adjacent``   → 放行（相邻金融领域，允许作答，只需说明侧重不同）；
    - ``None``（预检失败/无法解析）或 ``relevant`` → 放行。
    """
    if classification in (IRRELEVANT, UNSAFE):
        return True
    if classification == PARTIAL:
        return bool(strict)
    return False


def build_refusal(expert_name: str, classification: Optional[str]) -> str:
    """生成拒答正文（与边界块的话术保持一致：「请提问我领域内的问题」）。"""
    name = (expert_name or "").strip() or "当前专家"
    if classification == UNSAFE:
        template = REFUSAL_UNSAFE_TEMPLATE
    elif classification == PARTIAL:
        template = REFUSAL_PARTIAL_TEMPLATE
    else:
        template = REFUSAL_IRRELEVANT_TEMPLATE
    return template.format(name=name)


def _build_user_prompt(
    basis: str, message: str, context: str = "", attachments: Optional[list[str]] = None
) -> str:
    parts: list[str] = []
    if (context or "").strip():
        parts.append(f"【会话上下文（仅用于理解指代）】\n{context.strip()}\n")
    parts.append(f"【专家职责画像】\n{basis}\n")
    names = [str(n).strip() for n in (attachments or []) if str(n).strip()]
    if names:
        # 附件名是重要信号：正文常常只是「分析一下这个文件」，没有任何领域词
        parts.append("【本轮上传的附件】\n" + "、".join(names[:5]) + "\n")
    parts.append(f"【用户消息】\n{(message or '').strip()[:_MAX_MESSAGE_CHARS]}\n")
    return "\n".join(parts)


def _content_to_text(response: Any) -> str:
    content = getattr(response, "content", None)
    if content is None:
        content = response
    if isinstance(content, list):
        return "".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content)


def _bind_no_thinking(llm: Any) -> Any:
    """给模型绑定「关闭思考模式」参数；绑定失败时原样返回（不阻断预检）。"""
    try:
        return llm.bind(**_NO_THINKING_KWARGS)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"预检关思考参数绑定失败，按默认参数调用: {type(exc).__name__}: {exc}")
        return llm


def _looks_like_bad_params(exc: Exception) -> bool:
    """判断异常是否像「网关不认这些请求参数」。"""
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(hint in text for hint in _REJECT_HINTS)


async def check_relevance(
    llm: Any,
    basis: str,
    message: str,
    timeout: float = DEFAULT_TIMEOUT,
    context: str = "",
    disable_thinking: bool = True,
    attachments: Optional[list[str]] = None,
) -> Optional[str]:
    """调用轻量 LLM 做四分类。

    Args:
        llm: 已初始化的 LangChain ChatModel（复用全局实例，不新建模型）。
        basis: 专家职责画像（``build_basis`` 的产物）。
        message: 用户本轮消息正文。
        timeout: 超时秒数，超时返回 ``None``（放行）。
        context: 最近若干轮对话摘要（仅用于消解「继续」「上面第三点」这类指代，
            不作为判定依据；为空则只按单条消息判定）。
        disable_thinking: 是否在本轮请求里关闭思考模式（默认开）。四分类只需输出
            一行 JSON，思考纯属浪费：实测关闭后输出 token 从 258~1007 降到 7、
            耗时从 1.7~6.4 秒降到 0.5~0.6 秒。
        attachments: 本轮上传的附件文件名（正文常常只说「分析这个文件」，
            附件名是判断主题的关键信号）。

    Returns:
        ``"relevant"`` / ``"adjacent"`` / ``"partial"`` / ``"irrelevant"``，或 ``None``
        表示无法判定（模型缺失、异常、超时、输出无法解析）——调用方一律按放行处理。
    """
    if llm is None or not (basis or "").strip() or not (message or "").strip():
        return None

    from langchain_core.messages import HumanMessage, SystemMessage

    messages = [
        SystemMessage(content=_CLASSIFIER_SYSTEM),
        HumanMessage(
            content=_build_user_prompt(basis, message, context, attachments=attachments)
        ),
    ]

    call_model = _bind_no_thinking(llm) if disable_thinking else llm
    thinking_disabled = disable_thinking
    try:
        response = await asyncio.wait_for(call_model.ainvoke(messages), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"相关性预检超时（>{timeout:.1f}s），本轮放行")
        return None
    except Exception as exc:  # noqa: BLE001 —— 预检故障不阻断对话
        # 关思考参数被网关拒绝时，退回原始模型重试一次（可用性优先）
        if thinking_disabled and _looks_like_bad_params(exc):
            logger.warning(
                f"预检关思考参数被网关拒绝，改用默认参数重试 | {type(exc).__name__}: {exc}"
            )
            try:
                response = await asyncio.wait_for(
                    llm.ainvoke(messages), timeout=timeout
                )
            except asyncio.TimeoutError:
                logger.warning(f"相关性预检超时（>{timeout:.1f}s），本轮放行")
                return None
            except Exception as exc2:  # noqa: BLE001
                logger.warning(
                    f"相关性预检调用失败，本轮放行: {type(exc2).__name__}: {exc2}"
                )
                return None
        else:
            logger.warning(f"相关性预检调用失败，本轮放行: {type(exc).__name__}: {exc}")
            return None

    raw = _content_to_text(response)
    classification = parse_classification(raw)
    if classification is None:
        logger.warning(
            f"相关性预检输出无法解析，本轮放行 | 原文: {raw.strip()[:80]!r}"
        )
    return classification
