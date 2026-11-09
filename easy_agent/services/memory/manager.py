"""会话级记忆管理服务（基于工作区持久化）。

负责：
1. 每轮对话后根据上下文更新工作区下的 memory.md（项目、经验与偏好）
2. 监控记忆文件长度，确保不超过 MAX_MEMORY_CHARS (600) 字符
3. 超过限制时自动调用 LLM 压缩，保留核心信息

记忆文件位置：workspace/{username}/{workspace_name}/memory.md
生命周期：会话创建时预创建工作区目录 → 首轮响应后写入 memory.md → 后续输入时自动加载

结构化 sidecar：Markdown 旁的 memory.jsonl / AGENTS.jsonl（条目 / 来源 / 版本），
Markdown 仍是权威读取文件；默认开启，EASY_STRUCTURED_MEMORY=0 回退旧行为。
"""

import logging
import re
import time
from collections.abc import Sequence
from pathlib import Path

from easy_agent.services.memory.store import (
    SOURCE_DIRECT_WRITE,
    SOURCE_IMPORT,
    SOURCE_LLM_LONG_TERM,
    SOURCE_LLM_SESSION,
    atomic_write_text,
    content_digest,
    memory_lock,
    structured_memory_enabled,
    sync_from_markdown,
    write_structured_memory,
)
from easy_agent.services.agents.prompts import load_prompt, render
from easy_agent.services.memory.digest import clip_middle
from easy_agent.services.memory.ops import (
    annotate_for_prompt,
    apply_compaction,
    apply_memory_response,
    fit_to_budget,
)

logger = logging.getLogger("easy_agent.memory")

MAX_MEMORY_CHARS = 600
# 触发压缩的阈值（达到此长度即压缩，留出余量避免频繁触发）
COMPRESS_THRESHOLD = 540

# 用户级长期记忆（memories/{username}/AGENTS.md）上限，跨会话积累故比会话级更大
MAX_LONG_TERM_MEMORY_CHARS = 1500
LONG_TERM_COMPRESS_THRESHOLD = 1350

# Markdown 一级标题（与提示词契约一致；结构化渲染复用同一标题）
SESSION_MEMORY_TITLE = "会话记忆"
LONG_TERM_MEMORY_TITLE = "用户长期记忆"

#: 记忆的小节（与提示词一致）：增量修改时新条目只能进这些小节，渲染时按这个顺序排
SESSION_SECTIONS = ("决策与理由", "偏好与约定")
LONG_TERM_SECTIONS = ("工作背景", "个人背景", "当前关注", "近期动态")

#: 一次记忆更新最多做几遍：写回前发现文件已被别处改写（另一个进程的更新、用户在设置页
#: 手动修改），就基于最新内容重做；再冲突就放弃本次更新，不覆盖别人刚写的内容。
MAX_UPDATE_ATTEMPTS = 2

# 提示词统一放在 easy_agent/config/prompts/ 下（文件为权威）；
# 以下为文件缺失时的兜底文本，保证部署漏挂载仍能工作。
# 占位符使用 string.Template 的 $name 形式，对正文中的 JSON 大括号免疫。
FALLBACK_COMPRESS_PROMPT = """你是一个记忆压缩助手。请将下面的会话记忆压缩到不超过 $max_chars 个字符：

1. 保留可复用的经验、用户偏好、行为原则、关键解决方案
2. 删除一次性任务、临时状态、过时信息，合并相似条目
3. 保持 Markdown 层级结构；空间不足时优先压缩旧内容

原始记忆：
---
$content
---

直接输出压缩后的完整记忆内容（不要添加任何解释说明）："""


#: 增量更新后超出上限时的「精简」提示词（与 prompts/memory_compact.md 逐字一致，测试锁定）
FALLBACK_COMPACT_PROMPT = """你是记忆整理助手。下面这份记忆已经约 $current_chars 字，超过了 $max_chars 字的上限。请通过修改和删除条目把它压到上限以内，最好再留出两成余量。

## 做法

- 优先删除：一次性的执行细节；代码里能直接读出的实现细节（函数名、参数名与默认值、具体写法）；已经过时或被后来的条目取代的内容。
- 相近的条目合并成一条：用 update 改写其中一条，再 delete 其余几条。
- 保留：用户明确表达的偏好与约束、关键决策及其理由；改写时意思不能变。
- 不要新增条目，也不要把条目挪到别的小节。

## 当前记忆（方括号里是条目编号）

---
$current_memory
---

只输出一个 JSON 对象，不要任何解释，也不要用代码块包裹：
{"update": [{"id": 条目编号, "text": "改写后的整条内容"}], "delete": [条目编号]}"""


FALLBACK_UPDATE_MEMORY_PROMPT = """你是会话记忆管理助手。请根据本次对话更新会话记忆（memory.md）。

会话记忆的定位：它是**本会话后续轮次与将来会话都要遵守的共识**，会被自动注入到接下来的对话中。
因此要写成「给接手的人留的常驻规则」：助手读到就能直接照做，不必重新追问、重新论证。

## 只记两类

1. **决策与理由**：最终选了哪个方案、为什么这么选（依据 / 取舍）；被否决的选项及其原因；在什么约束下做的取舍。
   写法：`结论 —— 依据`。例如「数据库统一用 MySQL —— 开发与生产一致，避免 SQLite 差异；已否决 SQLite」。
2. **偏好与约定**：用户明确表达的偏好与风格；硬性约束（必须 / 禁止 / 默认）；术语、命名、单位、目录与接口约定。
   写法：以祈使句为主。例如「注释一律用中文」「接口路径统一带 /agent 前缀」「金额单位统一为元」。

## 一律不记

寒暄、一次性执行过程、原始日志、临时状态、代码里能直接读出的内容（函数名、参数名与默认值、具体实现写法）、密钥 / token / 密码 / 隐私数据（报错含敏感值须先脱敏）。
边界：跨会话通用的用户偏好交给长期记忆，这里只写本会话 / 本项目内成立、且后续要遵守的决策与约定。

**工具与环境的状态一律不记**：某个工具本轮有没有、联网开关是开还是关、能否执行 shell、沙箱是否禁用、
装了哪些技能、连接器是否可用、有没有行情源或数据源。这些每轮都可能变，写进来会让下一轮直接沿用旧结论、
不再核对本轮实际注入的工具清单，从而出现「明明能联网却回答没有网络」这类错误。
确需留痕时只写一条元规则：「工具可用性以本轮实际注入的清单为准，不依据记忆判断」。

## 写法要求

- **只改需要改的**：已有条目仍然成立就不动；新信息与旧条目冲突时修改那一条（以新的为准），不要另起一条与它矛盾的。
- 一条一句，先结论后依据；同类合并，删除过时与重复的条目。
- 条目写结论与约束，不写实现细节：空间只有 $target_chars 字，只留后续轮次必须遵守的内容。
- 只依据本次真实对话，不要脑补未出现的理由或结论。
- 新增条目只能放进这两个小节：`决策与理由`、`偏好与约定`；没有条目的小节会自动省略。
- 总长度不超过 $target_chars 字符（当前约 $current_chars 字）；接近上限时优先删除旧的一次性细节、合并相近条目，保留决策、理由与约束。

## 当前记忆（方括号里是条目编号，修改或删除时引用它）

---
$current_memory
---

## 本次对话

**用户输入**：$user_message

**助手回复（节选）**：$assistant_response

**本轮操作**（工具调用记录：只用来理解做了什么决定、依据是什么，比如哪个方案试过失败、用户拒绝了哪一步；执行过程本身不要写进记忆）：
$turn_actions

---

只输出一个 JSON 对象，列出对记忆的修改，不要任何解释，也不要用代码块包裹：
{"add": [{"section": "小节名", "text": "新条目"}], "update": [{"id": 条目编号, "text": "改写后的整条内容"}], "delete": [条目编号]}
没有需要修改的内容时输出 {"add": [], "update": [], "delete": []}。"""


def build_memory_update_prompt(
    current_memory: str,
    user_message: str,
    assistant_response: str,
    max_chars: int = MAX_MEMORY_CHARS,
    turn_actions: str = "",
) -> str:
    """Build a compact, context-aware session memory update prompt.

    当前记忆按条编号展示（``services/memory/ops.py``），模型据此给出增量修改指令。
    ``turn_actions`` 是本轮操作摘要（``services/memory/digest.describe_turn_actions``）；
    外部提示词目录里的旧模板没有 ``$turn_actions`` / ``$current_chars`` 时不显示，不影响渲染。
    """
    target_chars = min(MAX_MEMORY_CHARS, max_chars)
    template = load_prompt("memory_update") or FALLBACK_UPDATE_MEMORY_PROMPT
    return render(
        template,
        target_chars=target_chars,
        current_chars=len(current_memory or ""),
        current_memory=annotate_for_prompt(current_memory) or "(空)",
        user_message=user_message,
        assistant_response=assistant_response,
        turn_actions=turn_actions or "（无）",
    )


def read_memory(memory_file: Path) -> str:
    """读取记忆文件内容（共享锁下读取，避免读到双写中间态）。"""
    try:
        with memory_lock(memory_file, exclusive=False):
            if not memory_file.exists():
                return ""
            return memory_file.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"读取记忆文件失败 {memory_file}: {e}")
        return ""


def _write_markdown_file(memory_file: Path, content: str) -> None:
    """只写 Markdown 记忆文件（权威读取文件），不触碰结构化 sidecar。

    使用与 JSONL 相同的原子写实现：临时文件 + ``os.replace``，读方不会看到
    半截 Markdown；非原子 ``write_text`` 在并发读/进程崩溃时会留下截断文件。
    """
    atomic_write_text(memory_file, content)


def write_memory(memory_file: Path, content: str, *, raise_on_error: bool = False) -> None:
    """写入记忆文件（Markdown 为权威），并尽力同步结构化 sidecar。

    兼容旧调用方：默认失败只记日志；日志只记录长度与内容 hash，不再打印全文。
    ``raise_on_error=True`` 时把异常抛给调用方（设置页保存要如实报错）。
    """
    try:
        # Markdown 与 sidecar 共用同一把跨进程锁：读方不会看到「Markdown 已更新、
        # sidecar 仍是旧内容」的中间态。
        with memory_lock(memory_file):
            _write_markdown_file(memory_file, content)
            _import_legacy_markdown(memory_file, content, source=SOURCE_DIRECT_WRITE)
        logger.info(
            f"记忆文件已更新: {memory_file} | 长度: {len(content)} 字符 | "
            f"sha256: {content_digest(content)}"
        )
    except Exception as e:
        logger.error(f"写入记忆文件失败 {memory_file}: {e}")
        if raise_on_error:
            raise


def _import_legacy_markdown(
    memory_file: Path, markdown: str, *, source: str = SOURCE_IMPORT
) -> bool:
    """把已有 / 刚写入的 Markdown 首次导入结构化 sidecar（best effort）。

    失败只告警，不影响 Markdown 权威路径；Markdown 未变化时按内容 hash 幂等跳过。
    """
    if not structured_memory_enabled() or not (markdown or "").strip():
        return False
    try:
        return sync_from_markdown(memory_file, markdown=markdown, source=source)
    except Exception as e:
        logger.warning(f"旧 Markdown 导入结构化记忆失败（忽略）: {e}")
        return False


def _persist_structured_memory(
    memory_file: Path,
    markdown: str,
    *,
    title: str,
    source: str,
    max_chars: int,
    previous_markdown: str = "",
):
    """写结构化 sidecar 并返回渲染后的 Markdown；关闭/失败时返回 None 由调用方回退。"""
    if not structured_memory_enabled():
        return None
    try:
        return write_structured_memory(
            memory_file,
            markdown,
            title=title,
            source=source,
            max_chars=max_chars,
            previous_markdown=previous_markdown,
        )
    except Exception as e:
        logger.warning(f"结构化记忆写入失败，回退旧 Markdown 路径: {e}")
        return None


def compress_memory(content: str, llm, max_chars: int = MAX_MEMORY_CHARS) -> str:
    """调用 LLM 压缩记忆内容，确保不超过 max_chars 字符。

    Args:
        content: 原始记忆内容
        llm: LangChain ChatModel 实例
        max_chars: 压缩后最大字符数

    Returns:
        压缩后的记忆内容。若 LLM 调用失败，回退到截断策略。
    """
    if not content:
        return content

    start = time.time()
    logger.info(
        f"开始压缩记忆 | 原始长度: {len(content)} 字符 | 目标: <= {max_chars} 字符"
    )

    try:
        from langchain_core.messages import HumanMessage

        template = load_prompt("memory_compress") or FALLBACK_COMPRESS_PROMPT
        prompt = render(template, max_chars=max_chars, content=content)
        response = llm.invoke([HumanMessage(content=prompt)])
        compressed = getattr(response, "content", str(response)).strip()

        # 如果压缩后仍超限，递归截断保留头部（最常访问的部分）
        if len(compressed) > max_chars:
            logger.warning(
                f"LLM 压缩后仍超限 ({len(compressed)} 字符)，执行硬截断"
            )
            compressed = _hard_truncate(compressed, max_chars)

        elapsed = time.time() - start
        logger.info(
            f"记忆压缩完成 | 压缩后长度: {len(compressed)} 字符 | 耗时: {elapsed:.2f}s | "
            f"压缩率: {len(compressed) / max(len(content), 1) * 100:.1f}%"
        )
        return compressed
    except Exception as e:
        logger.error(f"LLM 压缩记忆失败，回退到硬截断: {e}")
        return _hard_truncate(content, max_chars)


def _compact_with_ops(
    markdown: str, llm, *, title: str, sections: Sequence[str], max_chars: int, scope: str
) -> str:
    """增量更新后超出上限：让模型用同样的指令格式删减、合并条目，不整份重写。

    整份重写的 :func:`compress_memory` 会改掉小节标题、改写没问题的条目，压完仍超限时还会
    截半句并加省略说明（真实对话里出现过）。这里模型只能修改或删除条目；回答不可用或精简
    后仍超限时，按长度整条丢弃（``ops.fit_to_budget``），保证结果不超过 ``max_chars``。
    """
    from langchain_core.messages import HumanMessage

    start_chars = len(markdown)
    template = load_prompt("memory_compact") or FALLBACK_COMPACT_PROMPT
    prompt = render(
        template,
        max_chars=max_chars,
        current_chars=start_chars,
        current_memory=annotate_for_prompt(markdown),
    )
    try:
        reply = getattr(llm.invoke([HumanMessage(content=prompt)]), "content", "") or ""
    except Exception as exc:  # noqa: BLE001 - 精简失败就按长度丢弃，不影响写入
        logger.warning(f"{scope}精简调用失败，改为按长度丢弃条目: {exc}")
        reply = ""
    change = apply_compaction(str(reply), markdown, title=title, sections=sections)
    compacted = change.markdown or markdown
    fitted, dropped = fit_to_budget(compacted, title=title, sections=sections, max_chars=max_chars)
    logger.info(
        f"{scope}超出上限，按指令精简 | {change.summary} | 按长度丢弃 {dropped} 条 | "
        f"{start_chars} → {len(fitted)} 字（上限 {max_chars}）"
    )
    return fitted


def _hard_truncate(content: str, max_chars: int) -> str:
    """硬截断策略：优先按行保留，单行过长时按字符截断。"""
    if len(content) <= max_chars:
        return content

    lines = content.split("\n")
    result = []
    current_len = 0
    # join 会自动添加 \n 分隔符，suffix 不再以 \n 开头
    suffix = "...(记忆已压缩，部分旧内容已省略)"
    # 预留 suffix 长度 + join 分隔符
    budget = max_chars - len(suffix) - 1

    for line in lines:
        if current_len + len(line) + 1 > budget:
            # 当前行放不下，若 result 为空（单行长内容场景），按字符截断当前行
            if not result:
                result.append(line[: max(0, budget - current_len)])
            break
        result.append(line)
        current_len += len(line) + 1

    result.append(suffix)
    return "\n".join(result)


def _changed_since(memory_file: Path, expected: str) -> bool:
    """记忆文件当前内容是否已不是 ``expected``（调用方须已持有 ``memory_lock``）。

    记忆更新是「读 → 调模型（几秒）→ 写」，锁只罩住写；写回前用它核对，避免把这段时间里
    别处写入的内容（另一个进程的更新、设置页的手动修改）整份覆盖掉。
    """
    return read_memory(memory_file) != expected


def enforce_memory_limit(memory_file: Path, llm=None, max_chars: int = MAX_MEMORY_CHARS) -> bool:
    """检查并强制记忆文件不超过长度限制。

    在 Agent 运行结束后调用。若记忆超过阈值则触发压缩。

    Args:
        memory_file: 记忆文件路径
        llm: 可选的 LLM 实例用于智能压缩；为 None 时仅硬截断
        max_chars: 最大字符数，默认会话级上限

    Returns:
        True 表示执行了压缩，False 表示无需压缩
    """
    try:
        content = read_memory(memory_file)
        if len(content) <= max_chars:
            return False

        logger.info(
            f"记忆超限触发压缩 | 文件: {memory_file} | 当前长度: {len(content)} 字符 | 上限: {max_chars}"
        )

        if llm is not None:
            compressed = compress_memory(content, llm, max_chars=max_chars)
        else:
            compressed = _hard_truncate(content, max_chars)

        with memory_lock(memory_file):
            if _changed_since(memory_file, content):
                # 压缩期间文件被改写：放弃这次压缩，下一次更新时再检查长度
                logger.warning(f"记忆在压缩期间被改写，放弃本次压缩 | 文件: {memory_file}")
                return False
            write_memory(memory_file, compressed)
        return True
    except Exception as e:
        logger.error(f"enforce_memory_limit 异常: {e}", exc_info=True)
        return False


def update_memory_after_session(
    memory_file: Path,
    user_message: str,
    assistant_response: str,
    llm=None,
    turn_actions: str = "",
    earlier_turns: Sequence[str] = (),
) -> bool:
    """会话结束后根据上下文更新用户记忆文件。

    不是每轮都调：什么时候整理由 ``services/memory/triggers.py`` 决定，之前攒下的轮次摘要经
    ``earlier_turns`` 一起交给模型。

    流程：
    1. 读取现有记忆
    2. 调用 LLM 提炼项目、经验与偏好
    3. 写回文件
    4. 若更新后超限，触发压缩

    Args:
        memory_file: 记忆文件路径
        user_message: 本次会话用户输入
        assistant_response: 本次会话助手回复（过长时保留开头和结尾）
        llm: LLM 实例；为 None 时跳过上下文更新，仅做长度检查
        turn_actions: 本轮操作摘要（``services/memory/digest.describe_turn_actions``）
        earlier_turns: 之前攒下、还没整理过的轮次摘要（按时间先后，不含本轮）

    Returns:
        True 表示记忆已更新，False 表示未更新
    """
    if llm is None:
        logger.info("未提供 LLM 实例，跳过上下文更新，仅做长度检查")
        return enforce_memory_limit(memory_file, None)

    start = time.time()
    try:
        # 截断过长的会话内容，避免 token 浪费；回复保留开头和结尾（结尾常是下一步 / 待确认）
        user_msg_trunc = _with_earlier_turns(
            (user_message or "")[:800], earlier_turns, title="之前几轮还没整理过的对话"
        )
        asst_msg_trunc = clip_middle(assistant_response, 2000)

        # 如果会话内容为空（如纯工具调用无文本），跳过更新
        if not user_msg_trunc.strip() and not asst_msg_trunc.strip() and not turn_actions.strip():
            logger.info("会话内容为空，跳过记忆更新")
            return enforce_memory_limit(memory_file, llm)

        from langchain_core.messages import HumanMessage

        target_chars = MAX_MEMORY_CHARS
        compression_threshold = min(COMPRESS_THRESHOLD, target_chars)

        for attempt in range(1, MAX_UPDATE_ATTEMPTS + 1):
            current_memory = read_memory(memory_file)
            # 首次升级时把旧 Markdown 导入 sidecar；已同步时按内容 hash 幂等跳过
            _import_legacy_markdown(memory_file, current_memory)

            logger.info(
                f"开始根据上下文更新记忆 | 文件: {memory_file} | "
                f"当前长度: {len(current_memory)} 字符 | "
                f"用户输入: {len(user_msg_trunc)} 字符 | 助手回复: {len(asst_msg_trunc)} 字符"
                + (f" | 第 {attempt} 次（上次写回前文件已被改写）" if attempt > 1 else "")
            )

            prompt = build_memory_update_prompt(
                current_memory=current_memory,
                user_message=user_msg_trunc,
                assistant_response=asst_msg_trunc,
                max_chars=MAX_MEMORY_CHARS,
                turn_actions=turn_actions,
            )
            response = llm.invoke([HumanMessage(content=prompt)])
            reply = getattr(response, "content", str(response)).strip()

            if not reply:
                logger.warning("LLM 返回空内容，记忆未更新")
                return enforce_memory_limit(memory_file, llm)

            # 增量修改：模型给的是指令，没动到的条目一字不改；「无需修改」时不写文件
            change = apply_memory_response(
                reply, current_memory, title=SESSION_MEMORY_TITLE, sections=SESSION_SECTIONS
            )
            if change.markdown is None:
                logger.info(f"会话记忆无变化，不写文件 | {change.summary} | 文件: {memory_file}")
                return False
            logger.info(f"会话记忆修改 | {change.summary} | 文件: {memory_file}")
            updated = change.markdown

            # 若更新后超过目标预算：增量修改的结果按指令精简（不整份重写），整份重写的回答走旧压缩
            if len(updated) > compression_threshold:
                if change.mode == "ops":
                    updated = _compact_with_ops(
                        updated,
                        llm,
                        title=SESSION_MEMORY_TITLE,
                        sections=SESSION_SECTIONS,
                        max_chars=target_chars,
                        scope="会话记忆",
                    )
                else:
                    logger.info(
                        f"更新后记忆超过目标预算 ({len(updated)} > {target_chars} 字符)，触发压缩"
                    )
                    updated = compress_memory(updated, llm, max_chars=target_chars)

            # 最终硬保底
            if len(updated) > target_chars:
                updated = _hard_truncate(updated, target_chars)

            # 检测「工具/环境状态」类描述（只记日志）：会话记忆每轮重新注入，
            # 「本轮没有 web_search」写进去会让下一轮跳过工具核对。
            log_tool_state_findings(updated, scope="会话")

            # sidecar 写入与 Markdown 覆盖必须在同一把锁内完成（可重入）：
            # 否则并发写会看到 Markdown 与 sidecar 对应不同 revision / 内容的组合。
            with memory_lock(memory_file):
                if _changed_since(memory_file, current_memory):
                    logger.warning(
                        f"会话记忆在更新期间被改写，基于最新内容重做 | 文件: {memory_file} | 第 {attempt} 次"
                    )
                    continue
                result = _persist_structured_memory(
                    memory_file,
                    updated,
                    title=SESSION_MEMORY_TITLE,
                    source=SOURCE_LLM_SESSION,
                    max_chars=target_chars,
                    previous_markdown=current_memory,
                )
                if result is None:
                    # 解析失败 / 开关关闭：保持旧行为，Markdown 原文写入，不丢数据
                    write_memory(memory_file, updated)
                    final_content = updated
                    entry_count = 0
                else:
                    _write_markdown_file(memory_file, result.markdown)
                    final_content = result.markdown
                    entry_count = result.entries

            elapsed = time.time() - start
            logger.info(
                f"记忆更新完成 | 更新后长度: {len(final_content)} 字符 | 条目: {entry_count} | "
                f"sha256: {content_digest(final_content)} | 耗时: {elapsed:.2f}s"
            )
            return True

        logger.warning(
            f"会话记忆在更新期间反复被改写，放弃本次更新，不覆盖新写入的内容 | 文件: {memory_file}"
        )
        return False
    except Exception as e:
        logger.error(f"update_memory_after_session 异常: {e}", exc_info=True)
        # 异常时回退到仅做长度检查
        return enforce_memory_limit(memory_file, llm)


# --------------------------------------------------------------------------- #
# 用户级长期记忆的领域边界剔除
#
# 用户级 memories/{user}/AGENTS.md 会注入**每一个**专家会话，因此不能承载
# 「某个专家只答什么 / 不答什么」这类规则——写进去就会被所有专家当成自己的规矩。
# 提示词（long_term_memory.md）负责让模型不要写，本函数负责落盘前兜底。
#
# 粒度是「子句」而非整行：一行里可能既有身份（「16 年利率债投研」）又有边界
# （「不覆盖汇率」），整行丢掉会把用户背景一起删了。
#
# 注意**只用于用户级长期记忆**：会话级 memory.md 绑定单个专家（换专家 = 换会话），
# 里面写领域规则是合理的，不做剔除。
# --------------------------------------------------------------------------- #

#: 命中即为「角色职责 / 领域边界 / 拒答规则」的特征词
_ROLE_SCOPE_PATTERNS = (
    re.compile(r"拒答|拒绝回答|不予回答"),
    re.compile(r"越界"),
    re.compile(r"只答|仅答|只接|仅接|只处理|仅处理|仅限"),
    re.compile(r"不覆盖|不涉及|不接"),
    re.compile(r"专业范围|职责范围|领域外|领域之外"),
    re.compile(r"超出.{0,8}范围"),
)
#: 句末切分（保留标点，便于原样拼回）
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。；;！!？?\n])")
#: 子句切分（命中后再按逗号拆，尽量保住同一行里的身份信息）
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[，,])")
#: Markdown 列表符号（`- ` / `* ` / `1. `），过滤后需要原样保留
_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*+]|\d+[.、)])\s*")


def _has_role_scope(text: str) -> bool:
    """文本里是否出现「角色职责 / 领域边界 / 拒答规则」的特征。"""
    return any(p.search(text or "") for p in _ROLE_SCOPE_PATTERNS)


def _has_meaningful_content(text: str) -> bool:
    """记忆里是否还有「非标题」的实际条目（只剩小标题等同于空记忆）。"""
    return any(
        line.strip() and not line.strip().startswith("#")
        for line in (text or "").splitlines()
    )


def strip_role_scope_clauses(text: str) -> tuple[str, list[str]]:
    """剔除用户级长期记忆里「专家角色职责 / 领域边界 / 拒答规则」类内容。

    与 ``long_term_memory.md`` 提示词里的「禁止记入」一节配套：提示词负责让模型
    不要写，本函数负责在落盘前兜底——模型偶尔仍会把「只答某领域」当成用户偏好写进来，
    而这份记忆会注入所有专家，一旦写进就会污染其它专家。

    Args:
        text: 待过滤的记忆全文（Markdown）。

    Returns:
        ``(cleaned_text, dropped)``。``dropped`` 是被删掉的原始子句列表，仅用于日志
        （便于排查「某条记忆怎么没了」）；无命中时原样返回，不会改动排版。
    """
    dropped: list[str] = []
    if not (text or "").strip():
        return text or "", dropped

    out_lines: list[str] = []
    for line in text.splitlines():
        if not _has_role_scope(line):
            out_lines.append(line)
            continue

        prefix_match = _BULLET_PREFIX_RE.match(line)
        prefix = prefix_match.group(0) if prefix_match else ""
        body = line[len(prefix):]

        kept_sentences: list[str] = []
        for sentence in _SENTENCE_SPLIT_RE.split(body):
            if not sentence.strip():
                continue
            if not _has_role_scope(sentence):
                kept_sentences.append(sentence)
                continue
            # 整句命中 → 再按逗号拆，保住同一句里的身份/偏好信息
            kept_parts: list[str] = []
            for part in _CLAUSE_SPLIT_RE.split(sentence):
                if not part.strip():
                    continue
                if _has_role_scope(part):
                    dropped.append(part.strip())
                else:
                    kept_parts.append(part)
            if kept_parts:
                kept_sentences.append("".join(kept_parts))

        kept_line = prefix + "".join(kept_sentences)
        # 只剩列表符号说明整条都被删了，丢掉这一行；否则保留残句
        if kept_line.strip() and kept_line.strip() not in ("-", "*", "+"):
            out_lines.append(kept_line)

    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip()
    # 结尾标点/分隔符可能残留（如「16 年利率债投研；」后整句被删），收一下尾
    cleaned = re.sub(r"[；;、,，\s]+$", "", cleaned).strip()
    return cleaned, dropped


# --------------------------------------------------------------------------- #
# 「工具 / 环境能力状态」检测（现阶段只记日志，不删内容）
#
# 提示词（memory_update.md / long_term_memory.md）要求模型不要写工具状态，但框架
# deepagents 的 <memory_guidelines> 明确鼓励写「how to use tools」，模型照做的概率不低；
# 而记忆每次都会重新注入，一条「本轮没有 web_search」就足以让下一轮不再核对工具清单。
#
# 这里只在落盘前**检测并打日志**：正则误删会伤用户画像（「我在 Windows 上用 shell 做分析」
# 这类正常背景容易被当成能力快照），先观察命中率，确认没有误伤再改为剔除。
# --------------------------------------------------------------------------- #

#: 只认「能力名词 + 否定/状态词」的紧邻组合，避免大范围误伤
_TOOL_STATE_PATTERNS = (
    re.compile(
        r"(?:没有|无|未开启|未启用|不可用|不可|不能|无法|禁用|禁|不支持|缺少)[^。；;\n]{0,12}"
        r"(?:web_search|联网|网络|shell|沙箱|连接器|行情源|数据源|技能清单)"
    ),
    re.compile(
        r"(?:web_search|联网搜索|联网|shell|沙箱)[^。；;\n]{0,8}"
        r"(?:未开启|不可用|被禁用|禁用|不允许|已关闭|关闭)"
    ),
)
#: 出现这些词说明写的是「以本轮为准」的元规则——正是我们想要的那一条，不算命中
_TOOL_STATE_META_HINTS = ("以本轮", "每轮", "实际注入", "当轮")
#: 子句切分：只判到分句粒度，避免「一句元规则 + 一句能力快照」被整行放过
_TOOL_STATE_SPLIT_RE = re.compile(r"(?<=[。；;！!？?\n])|(?<=[，,])")


def find_tool_state_clauses(text: str) -> list[str]:
    """找出记忆里「工具 / 环境能力状态」类子句（含元规则的子句不算）。

    粒度是「子句」而非整行：真实污染长这样——
    「联网以本轮实际注入工具清单为准，……；不可 shell/脚本抓外网；Windows 沙箱禁 shell」，
    同一个列表项里既有正确的元规则又有过期的能力快照，整行判会漏掉后者。
    """
    hits: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for clause in _TOOL_STATE_SPLIT_RE.split(stripped):
            clause = _BULLET_PREFIX_RE.sub("", clause or "").strip()
            if not clause:
                continue
            if any(hint in clause for hint in _TOOL_STATE_META_HINTS):
                continue
            if any(p.search(clause) for p in _TOOL_STATE_PATTERNS):
                hits.append(clause)
    return hits


def log_tool_state_findings(text: str, *, scope: str) -> None:
    """把命中的能力快照写进日志：用于验证提示词改动是否生效、命中率有多高。"""
    hits = find_tool_state_clauses(text)
    if not hits:
        return
    logger.warning(
        f"🧠 {scope}记忆含 {len(hits)} 处「工具/环境状态」描述"
        f"（应改写为元规则「以本轮实际注入的清单为准」）| 样例: {hits[:3]}"
    )


FALLBACK_LONG_TERM_MEMORY_PROMPT = """你是用户长期记忆管理助手。请根据本次对话更新用户长期记忆（AGENTS.md），只沉淀跨会话长期有效、能帮助下次会话快速理解用户的个人信息与背景。

## 记入（满足其一）

1. **工作背景**：当前职位与职责、所在组织或团队、主要项目与技术栈、专业领域和长期目标
2. **个人背景**：稳定身份信息、经验资历、沟通与协作偏好、城市/时区等可控背景信息
3. **当前关注**：当前重点方向、关键任务、学习目标或阶段性目标
4. **近期动态**：近期重要变化、进行中的事项、下一步计划或待确认决策

## 排除

一次性任务及其结果、临时状态、单会话内的中间结论、完整执行过程、寒暄内容、密钥 / token / 密码和其他隐私数据。
判据：只在本次会话成立的信息一律不写。

**工具与环境的能力状态（同样禁止）**：某个工具有没有、联网开没开、能否执行 shell、沙箱限制、
已装技能清单、连接器列表、有没有行情源 / 数据源。理由：这份记忆会注入每个专家会话，
而工具清单每轮、每个专家都可能不同，写进来等于给所有会话塞一份过期的能力快照。

## 禁止记入（会注入到所有专家，写进来就会污染其他专家）

这份记忆会进入**每一个**专家会话的系统提示词。专家之间互不共享领域，写错位置就会让
A 专家的规矩去约束 B 专家（曾出现：用户切到 Java 专家后，模型照着这里的利率债规则拒答）。

1. **某个专家角色的职责范围与领域边界**：例如“只接利率债、不覆盖汇率/外汇”“越界不答”
   “代码委托仅限某领域，领域外不做”——这类规则属于专家自己的提示词，不属于用户画像。
2. **拒答规则与话术**：例如“超出范围一律拒答”“请提问我领域内的问题”。
3. 更新时如果发现已有内容里存在上述条目，**直接删除**，不要保留也不要改写沿用。

区分口径：

- ✅ 可以记：用户身份与背景（“16 年利率债投研”“资深 Java 架构师”）、跨角色都成立的工作偏好
  （“偏好表格输出、结论先行、数据要标来源”）。
- ❌ 不可以记：任何**限定“只能/不能回答什么”**的规则（“只答债券”“汇率拒答”“个股不做”）。

## 写法要求

- **只改需要改的**：已有条目仍然成立就不动；新信息与旧条目冲突时修改那一条，不要另起一条与它矛盾的。
- 新增条目只能放进这四个小节：`## 工作背景`、`## 个人背景`、`## 当前关注`、`## 近期动态`；没有条目的小节会自动省略。
- 每个方面写一类一句话；与已有条目重复就合并精炼，删除过时、冲突或低价值的条目，不堆叠近似内容。
- 近期动态只保留仍有后续价值的事项，已完成的长期结论移入工作背景或删除一次性细节。
- 「暂无…」之类的占位条目，有了真实内容后删掉。
- 与工具相关只允许写元规则（「以本轮实际注入的清单为准」），不写具体清单与开关状态。
- 总长度不超过 $target_chars 字符（当前约 $current_chars 字）；接近上限时优先保留工作背景、当前关注和近期动态，再压缩低频个人背景。

## 当前长期记忆（方括号里是条目编号，修改或删除时引用它）

---
$current_memory
---

## 本次对话

**用户输入**：$user_message

**助手回复（节选）**：$assistant_response

---

只输出一个 JSON 对象，列出对长期记忆的修改，不要任何解释，也不要用代码块包裹：
{"add": [{"section": "小节名", "text": "新条目"}], "update": [{"id": 条目编号, "text": "改写后的整条内容"}], "delete": [条目编号]}
没有需要修改的内容时（绝大多数对话都是这样）输出 {"add": [], "update": [], "delete": []}。"""


def build_long_term_memory_update_prompt(
    current_memory: str,
    user_message: str,
    assistant_response: str,
    max_chars: int = MAX_LONG_TERM_MEMORY_CHARS,
) -> str:
    """Build a size-aware long-term memory update prompt（当前记忆按条编号，模型给增量修改指令）。"""
    target_chars = min(MAX_LONG_TERM_MEMORY_CHARS, max_chars)
    template = load_prompt("long_term_memory") or FALLBACK_LONG_TERM_MEMORY_PROMPT
    return render(
        template,
        target_chars=target_chars,
        current_chars=len(current_memory or ""),
        current_memory=annotate_for_prompt(current_memory) or "(空)",
        user_message=user_message,
        assistant_response=assistant_response,
    )


def _with_earlier_turns(current: str, earlier: Sequence[str] = (), *, title: str) -> str:
    """本轮内容；有之前攒下、还没整理的轮次时（``services/memory/triggers.py``）先按时间列出。

    放进提示词的用户输入一节，不改模板：外部提示词目录里的旧模板也能看到这几轮。
    """
    earlier = [text.strip() for text in earlier if text and text.strip()]
    if not earlier:
        return current
    lines = [f"（{title}，按时间先后）"]
    for index, text in enumerate(earlier, 1):
        first, *rest = text.splitlines()
        lines.append(f"{index}. {first}")
        lines.extend(f"   {line}" for line in rest)
    lines += ["（本轮）", current]
    return "\n".join(lines)


def _prepare_long_term_context(
    memory_file: Path,
    user_message: str,
    assistant_response: str,
    earlier_user_messages: Sequence[str] = (),
) -> tuple[str, str, str]:
    """读取当前长期记忆、导入 legacy 内容，并截断本次会话文本（无其它副作用）。

    Returns:
        ``(current_memory, user_msg_trunc, asst_msg_trunc)``
    """
    current_memory = read_memory(memory_file)
    # 首次升级时把旧 Markdown 导入 sidecar；已同步时按内容 hash 幂等跳过
    _import_legacy_markdown(memory_file, current_memory)

    # 截断过长的会话内容，避免 token 浪费（之前攒下的轮次登记时已各自截断）
    user_msg_trunc = _with_earlier_turns(
        (user_message or "")[:800], earlier_user_messages, title="之前几轮还没整理过的用户输入"
    )
    asst_msg_trunc = clip_middle(assistant_response, 2000)
    return current_memory, user_msg_trunc, asst_msg_trunc


def _generate_long_term_update(
    *,
    current_memory: str,
    user_msg_trunc: str,
    asst_msg_trunc: str,
    llm,
    max_chars: int,
) -> str:
    """调一次 LLM，生成长期记忆的新正文（返回 strip 后的文本）。"""
    from langchain_core.messages import HumanMessage

    prompt = build_long_term_memory_update_prompt(
        current_memory=current_memory,
        user_message=user_msg_trunc,
        assistant_response=asst_msg_trunc,
        max_chars=max_chars,
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    return getattr(response, "content", str(response)).strip()


def _sanitize_long_term_update(
    updated: str, *, llm, max_chars: int, incremental: bool = False
) -> str | None:
    """清理 LLM 输出：剔角色职责规则 → 有意义性校验 → 超预算压缩（压缩后再过滤）→ 硬截断。

    ``incremental=True``（增量修改的结果）超预算时按指令精简（:func:`_compact_with_ops`），
    不整份重写。返回 ``None`` 表示剔除后已无实际条目（调用方保留原文件不写入）。
    """
    # 兜底剔除「角色职责 / 领域边界 / 拒答规则」：这份记忆会注入所有专家，
    # 一旦写进就会被其它专家当成自己的规矩（详见 strip_role_scope_clauses）。
    updated, dropped = strip_role_scope_clauses(updated)
    if dropped:
        logger.info(
            f"🧹 用户长期记忆已剔除 {len(dropped)} 处「角色职责/拒答规则」"
            f"（避免注入到其它专家）| 样例: {dropped[:3]}"
        )
    if not _has_meaningful_content(updated):
        logger.warning("剔除角色职责后长期记忆已无实际条目，保留原文件不写入")
        return None

    # 检测「工具/环境能力状态」类描述（只记日志，不删内容）：这类条目会让下一轮
    # 直接沿用「没有网 / 不能 shell」的旧结论，不再核对本轮实际注入的工具清单。
    log_tool_state_findings(updated, scope="用户长期")

    target_chars = max_chars
    compression_threshold = min(LONG_TERM_COMPRESS_THRESHOLD, target_chars)

    # 若更新后超过目标预算，触发压缩
    if len(updated) > compression_threshold:
        if incremental:
            updated = _compact_with_ops(
                updated,
                llm,
                title=LONG_TERM_MEMORY_TITLE,
                sections=LONG_TERM_SECTIONS,
                max_chars=target_chars,
                scope="用户长期记忆",
            )
        else:
            logger.info(
                f"更新后长期记忆超过目标预算 ({len(updated)} > {target_chars} 字符)，触发压缩"
            )
            updated = compress_memory(updated, llm, max_chars=target_chars)
        # 压缩 / 精简都经过一次 LLM 改写，可能把刚剔掉的规则又写回来，再过滤一遍
        updated, _more_dropped = strip_role_scope_clauses(updated)

    # 最终硬保底
    if len(updated) > target_chars:
        updated = _hard_truncate(updated, target_chars)
    return updated


def _persist_long_term_update(
    memory_file: Path, content: str, *, current_memory: str, max_chars: int
) -> tuple[str, int] | None:
    """在同一把跨进程锁内写 sidecar 与 Markdown，返回 ``(最终正文, 条目数)``。

    返回 ``None`` 表示读取之后文件已被别处改写（同一用户另一个会话的更新、设置页手动修改），
    本次没有写入，由调用方基于最新内容重做。
    """
    with memory_lock(memory_file):
        if _changed_since(memory_file, current_memory):
            return None
        result = _persist_structured_memory(
            memory_file,
            content,
            title=LONG_TERM_MEMORY_TITLE,
            source=SOURCE_LLM_LONG_TERM,
            max_chars=max_chars,
            previous_markdown=current_memory,
        )
        if result is None:
            # 解析失败 / 开关关闭：保持旧行为，Markdown 原文写入，不丢数据
            write_memory(memory_file, content)
            return content, 0
        _write_markdown_file(memory_file, result.markdown)
        return result.markdown, result.entries


def update_long_term_memory_after_session(
    memory_file: Path,
    user_message: str,
    assistant_response: str,
    llm=None,
    max_chars: int = MAX_LONG_TERM_MEMORY_CHARS,
    earlier_user_messages: Sequence[str] = (),
) -> bool:
    """会话结束后更新用户级长期记忆文件（memories/{username}/AGENTS.md）。

    不是每轮都调：什么时候整理由 ``services/memory/triggers.py`` 决定，攒下的轮次经
    ``earlier_user_messages`` 一起交给模型。

    与 update_memory_after_session（工作区会话级 memory.md）的区别：
    - 这是**用户级、跨会话持久化**的长期记忆；
    - 只保留稳定、长期有效、跨会话有价值的用户偏好与可复用经验，
      不记录单次会话的临时上下文、一次性任务或寒暄。

    结构：
    1. :func:`_prepare_long_term_context`：读当前记忆 + 截断本次会话文本；
    2. :func:`_generate_long_term_update`：LLM 生成新正文；
    3. :func:`_sanitize_long_term_update`：剔规则 / 有意义性 / 压缩 / 硬截断；
    4. :func:`_persist_long_term_update`：跨进程锁内写 sidecar + Markdown。

    Args:
        memory_file: 长期记忆文件路径（AGENTS.md）
        user_message: 本次会话用户输入
        assistant_response: 本次会话助手回复
        llm: LLM 实例；为 None 时跳过上下文更新，仅做长度检查
        max_chars: 长期记忆字符上限，默认 1500
        earlier_user_messages: 之前攒下、还没整理过的用户输入（按时间先后，不含本轮）

    Returns:
        True 表示记忆已更新，False 表示未更新
    """
    if llm is None:
        logger.info("未提供 LLM 实例，跳过长期记忆上下文更新，仅做长度检查")
        return enforce_memory_limit(memory_file, None, max_chars=max_chars)

    start = time.time()
    try:
        for attempt in range(1, MAX_UPDATE_ATTEMPTS + 1):
            current_memory, user_msg_trunc, asst_msg_trunc = _prepare_long_term_context(
                memory_file, user_message, assistant_response, earlier_user_messages
            )

            # 如果会话内容为空（如纯工具调用无文本），跳过更新
            if not user_msg_trunc.strip() and not asst_msg_trunc.strip():
                logger.info("会话内容为空，跳过长期记忆更新")
                return enforce_memory_limit(memory_file, llm, max_chars=max_chars)

            logger.info(
                f"开始更新用户长期记忆 | 文件: {memory_file} | "
                f"当前长度: {len(current_memory)} 字符"
                + (f" | 第 {attempt} 次（上次写回前文件已被改写）" if attempt > 1 else "")
            )

            reply = _generate_long_term_update(
                current_memory=current_memory,
                user_msg_trunc=user_msg_trunc,
                asst_msg_trunc=asst_msg_trunc,
                llm=llm,
                max_chars=max_chars,
            )
            if not reply:
                logger.warning("LLM 返回空内容，长期记忆未更新")
                return enforce_memory_limit(memory_file, llm, max_chars=max_chars)

            # 增量修改：没动到的条目一字不改；「无需修改」时不写文件
            change = apply_memory_response(
                reply, current_memory, title=LONG_TERM_MEMORY_TITLE, sections=LONG_TERM_SECTIONS
            )
            if change.markdown is None:
                logger.info(f"用户长期记忆无变化，不写文件 | {change.summary} | 文件: {memory_file}")
                return False
            logger.info(f"用户长期记忆修改 | {change.summary} | 文件: {memory_file}")

            updated = _sanitize_long_term_update(
                change.markdown, llm=llm, max_chars=max_chars, incremental=change.mode == "ops"
            )
            if updated is None:
                return False

            persisted = _persist_long_term_update(
                memory_file,
                updated,
                current_memory=current_memory,
                max_chars=max_chars,
            )
            if persisted is None:
                logger.warning(
                    f"用户长期记忆在更新期间被改写，基于最新内容重做 | 文件: {memory_file} | 第 {attempt} 次"
                )
                continue
            final_content, entry_count = persisted

            elapsed = time.time() - start
            logger.info(
                f"用户长期记忆更新完成 | 更新后长度: {len(final_content)} 字符 | "
                f"条目: {entry_count} | sha256: {content_digest(final_content)} | "
                f"耗时: {elapsed:.2f}s"
            )
            return True

        logger.warning(
            f"用户长期记忆在更新期间反复被改写，放弃本次更新，不覆盖新写入的内容 | 文件: {memory_file}"
        )
        return False
    except Exception as e:
        logger.error(f"update_long_term_memory_after_session 异常: {e}", exc_info=True)
        # 异常时回退到仅做长度检查
        return enforce_memory_limit(memory_file, llm, max_chars=max_chars)
