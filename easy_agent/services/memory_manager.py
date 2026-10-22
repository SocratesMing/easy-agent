"""会话级记忆管理服务（基于工作区持久化）。

负责：
1. 每轮对话后根据上下文更新工作区下的 memory.md（项目、经验与偏好）
2. 监控记忆文件长度，确保不超过 MAX_MEMORY_CHARS (600) 字符
3. 超过限制时自动调用 LLM 压缩，保留核心信息

记忆文件位置：workspace/{username}/{workspace_name}/memory.md
生命周期：会话创建时预创建工作区目录 → 首轮响应后写入 memory.md → 后续输入时自动加载
"""

import logging
import re
import time
from pathlib import Path

from .prompt_loader import load_prompt, render

logger = logging.getLogger("easy_agent.memory")

MAX_MEMORY_CHARS = 600
# 触发压缩的阈值（达到此长度即压缩，留出余量避免频繁触发）
COMPRESS_THRESHOLD = 540

# 用户级长期记忆（memories/{username}/AGENTS.md）上限，跨会话积累故比会话级更大
MAX_LONG_TERM_MEMORY_CHARS = 1500
LONG_TERM_COMPRESS_THRESHOLD = 1350

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


FALLBACK_UPDATE_MEMORY_PROMPT = """你是一个会话记忆管理助手。根据本次对话更新会话记忆（memory.md），让后续会话无需重新探索就能接着干活。

## 只记这四类

1. 决策与依据：选了什么方案、为什么（含被否掉的选项及原因）
2. 错误根因：报错的真实原因与已验证的解法（不是原始日志）
3. 约定与路径：接口 / 命名 / 目录约定、关键文件位置、环境差异
4. 断点与待办：未完成动作、下一步、待确认问题

## 一律不记

寒暄、一次性执行过程、原始日志、临时状态、可从代码直接读出的内容；密钥 / token / 密码（报错含敏感值须脱敏）。

## 写法

每条一句话，先结论后原因；合并同类项，删除过时与重复表述。
用二级标题分组：`## 当前项目`、`## 关键决策`、`## 踩坑与解法`、`## 下一步`；无内容的小节省略。
总长不超过 $target_chars 字符。

## 当前记忆

---
$current_memory
---

## 本次对话

**用户输入**：$user_message

**助手回复（节选）**：$assistant_response

---

直接输出更新后的完整记忆文件（Markdown，一级标题 `# 会话记忆`），不要任何解释说明："""


def build_memory_update_prompt(
    current_memory: str,
    user_message: str,
    assistant_response: str,
    max_chars: int = MAX_MEMORY_CHARS,
) -> str:
    """Build a compact, context-aware session memory update prompt."""
    target_chars = min(MAX_MEMORY_CHARS, max_chars)
    template = load_prompt("memory_update") or FALLBACK_UPDATE_MEMORY_PROMPT
    return render(
        template,
        target_chars=target_chars,
        current_memory=current_memory or "(空)",
        user_message=user_message,
        assistant_response=assistant_response,
    )


def read_memory(memory_file: Path) -> str:
    """读取记忆文件内容。"""
    try:
        if not memory_file.exists():
            return ""
        return memory_file.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"读取记忆文件失败 {memory_file}: {e}")
        return ""


def write_memory(memory_file: Path, content: str) -> None:
    """写入记忆文件。"""
    try:
        memory_file.parent.mkdir(parents=True, exist_ok=True)
        memory_file.write_text(content, encoding="utf-8")
        logger.info(
            f"记忆文件已更新: {memory_file} | 长度: {len(content)} 字符\n"
            f"--- 记忆内容 ---\n{content}\n--- 记忆内容结束 ---"
        )
    except Exception as e:
        logger.error(f"写入记忆文件失败 {memory_file}: {e}")


def needs_compression(content: str) -> bool:
    """判断记忆内容是否需要压缩。"""
    return len(content) > COMPRESS_THRESHOLD


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
) -> bool:
    """会话结束后根据上下文更新用户记忆文件。

    流程：
    1. 读取现有记忆
    2. 调用 LLM 提炼项目、经验与偏好
    3. 写回文件
    4. 若更新后超限，触发压缩

    Args:
        memory_file: 记忆文件路径
        user_message: 本次会话用户输入
        assistant_response: 本次会话助手回复
        llm: LLM 实例；为 None 时跳过上下文更新，仅做长度检查

    Returns:
        True 表示记忆已更新，False 表示未更新
    """
    if llm is None:
        logger.info("未提供 LLM 实例，跳过上下文更新，仅做长度检查")
        return enforce_memory_limit(memory_file, None)

    start = time.time()
    try:
        current_memory = read_memory(memory_file)

        # 截断过长的会话内容，避免 token 浪费
        user_msg_trunc = (user_message or "")[:800]
        asst_msg_trunc = (assistant_response or "")[:2000]

        # 如果会话内容为空（如纯工具调用无文本），跳过更新
        if not user_msg_trunc.strip() and not asst_msg_trunc.strip():
            logger.info("会话内容为空，跳过记忆更新")
            return enforce_memory_limit(memory_file, llm)

        logger.info(
            f"开始根据上下文更新记忆 | 文件: {memory_file} | "
            f"当前长度: {len(current_memory)} 字符 | "
            f"用户输入: {len(user_msg_trunc)} 字符 | 助手回复: {len(asst_msg_trunc)} 字符"
        )

        from langchain_core.messages import HumanMessage

        prompt = build_memory_update_prompt(
            current_memory=current_memory,
            user_message=user_msg_trunc,
            assistant_response=asst_msg_trunc,
            max_chars=MAX_MEMORY_CHARS,
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        updated = getattr(response, "content", str(response)).strip()

        if not updated:
            logger.warning("LLM 返回空内容，记忆未更新")
            return enforce_memory_limit(memory_file, llm)

        target_chars = MAX_MEMORY_CHARS
        compression_threshold = min(COMPRESS_THRESHOLD, target_chars)

        # 若更新后超过目标预算，触发压缩
        if len(updated) > compression_threshold:
            logger.info(
                f"更新后记忆超过目标预算 ({len(updated)} > {target_chars} 字符)，触发压缩"
            )
            updated = compress_memory(updated, llm, max_chars=target_chars)

        # 最终硬保底
        if len(updated) > target_chars:
            updated = _hard_truncate(updated, target_chars)

        write_memory(memory_file, updated)

        elapsed = time.time() - start
        logger.info(
            f"记忆更新完成 | 更新后长度: {len(updated)} 字符 | 耗时: {elapsed:.2f}s\n"
            f"--- 记忆内容 ---\n{updated}\n--- 记忆内容结束 ---"
        )
        return True
    except Exception as e:
        logger.error(f"update_memory_after_session 异常: {e}", exc_info=True)
        # 异常时回退到仅做长度检查
        return enforce_memory_limit(memory_file, llm)


# ── 用户级长期记忆的「角色职责」过滤器 ───────────────────────────────────
#
# 用户级 AGENTS.md 会被注入**每一个**专家会话的系统提示词，而专家之间互不共享领域。
# 实测事故：用户级记忆里沉淀了「只接利率债、不覆盖汇率/外汇」「超出范围一律拒答」，
# 用户切到 Java 专家后模型照着这些规则拒答，还照抄了上一个专家的名字。
# 提示词（config/prompts/long_term_memory.md）已明确禁止记入这类内容，这里再加一道
# **确定性兜底**：写入前按子句剔除，不依赖模型每次都能遵守。
#
# 粒度是「子句」而非整行：一行里可能既有身份（「16 年利率债投研」）又有边界
# （「不覆盖汇率」），整行丢掉会把用户背景一起删了。
#
# 注意**只用于用户级长期记忆**：会话级 memory.md 绑定单个专家（换专家 = 换会话），
# 里面写领域规则是合理的，不做剔除。

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


FALLBACK_LONG_TERM_MEMORY_PROMPT = """你是用户长期记忆管理助手。请根据本次对话更新用户长期记忆（AGENTS.md），只沉淀跨会话长期有效、能帮助下次会话快速理解用户的个人信息与背景。

## 记入（满足其一方可记入）

1. **工作背景**：当前职位与职责、所在组织或团队、主要项目与技术栈、专业领域和长期目标
2. **个人背景**：稳定身份信息、经验资历、沟通与协作偏好、城市/时区等可控背景信息
3. **当前关注**：当前重点方向、关键任务、学习目标或阶段性目标
4. **近期动态**：近期重要变化、进行中的事项、下一步计划或待确认决策

## 排除

一次性任务及其结果、临时状态、单会话内的中间结论、完整执行过程、寒暄内容、密钥 / token / 密码和其他隐私数据。
判据：只在本次会话成立的信息一律不写。

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

- 用 Markdown 二级标题分组：`## 工作背景`、`## 个人背景`、`## 当前关注`、`## 近期动态`；无内容的小节省略。
- 每个方面写一类而非逐条累加；与已有条目重复就合并精炼，删除过时、冲突或低价值内容，不堆叠近似内容。
- 近期动态只保留仍有后续价值的事项；已完成事项转为可复用结论或删除。
- 总长度不超过 $target_chars 字符；接近上限时优先保留工作背景、当前关注和近期动态，再压缩低频个人背景。

## 当前长期记忆

---
$current_memory
---

## 本次对话

**用户输入**：$user_message

**助手回复（节选）**：$assistant_response
---

请直接输出更新后的完整长期记忆文件内容（Markdown，一级标题 `# 用户长期记忆`），不要添加任何解释说明："""


def build_long_term_memory_update_prompt(
    current_memory: str,
    user_message: str,
    assistant_response: str,
    max_chars: int = MAX_LONG_TERM_MEMORY_CHARS,
) -> str:
    """Build a size-aware long-term memory update prompt."""
    target_chars = min(MAX_LONG_TERM_MEMORY_CHARS, max_chars)
    template = load_prompt("long_term_memory") or FALLBACK_LONG_TERM_MEMORY_PROMPT
    return render(
        template,
        target_chars=target_chars,
        current_memory=current_memory or "(空)",
        user_message=user_message,
        assistant_response=assistant_response,
    )


def update_long_term_memory_after_session(
    memory_file: Path,
    user_message: str,
    assistant_response: str,
    llm=None,
    max_chars: int = MAX_LONG_TERM_MEMORY_CHARS,
) -> bool:
    """会话结束后更新用户级长期记忆文件（memories/{username}/AGENTS.md）。

    与 update_memory_after_session（工作区会话级 memory.md）的区别：
    - 这是**用户级、跨会话持久化**的长期记忆；
    - 只保留稳定、长期有效、跨会话有价值的用户偏好与可复用经验，
      不记录单次会话的临时上下文、一次性任务或寒暄。

    Args:
        memory_file: 长期记忆文件路径（AGENTS.md）
        user_message: 本次会话用户输入
        assistant_response: 本次会话助手回复
        llm: LLM 实例；为 None 时跳过上下文更新，仅做长度检查
        max_chars: 长期记忆字符上限，默认 1500

    Returns:
        True 表示记忆已更新，False 表示未更新
    """
    if llm is None:
        logger.info("未提供 LLM 实例，跳过长期记忆上下文更新，仅做长度检查")
        return enforce_memory_limit(memory_file, None, max_chars=max_chars)

    start = time.time()
    try:
        current_memory = read_memory(memory_file)

        # 截断过长的会话内容，避免 token 浪费
        user_msg_trunc = (user_message or "")[:800]
        asst_msg_trunc = (assistant_response or "")[:2000]

        # 如果会话内容为空（如纯工具调用无文本），跳过更新
        if not user_msg_trunc.strip() and not asst_msg_trunc.strip():
            logger.info("会话内容为空，跳过长期记忆更新")
            return enforce_memory_limit(memory_file, llm, max_chars=max_chars)

        logger.info(
            f"开始更新用户长期记忆 | 文件: {memory_file} | "
            f"当前长度: {len(current_memory)} 字符"
        )

        from langchain_core.messages import HumanMessage

        prompt = build_long_term_memory_update_prompt(
            current_memory=current_memory,
            user_message=user_msg_trunc,
            assistant_response=asst_msg_trunc,
            max_chars=max_chars,
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        updated = getattr(response, "content", str(response)).strip()

        if not updated:
            logger.warning("LLM 返回空内容，长期记忆未更新")
            return enforce_memory_limit(memory_file, llm, max_chars=max_chars)

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
            return False

        target_chars = max_chars
        compression_threshold = min(LONG_TERM_COMPRESS_THRESHOLD, target_chars)

        # 若更新后超过目标预算，触发压缩
        if len(updated) > compression_threshold:
            logger.info(
                f"更新后长期记忆超过目标预算 ({len(updated)} > {target_chars} 字符)，触发压缩"
            )
            updated = compress_memory(updated, llm, max_chars=target_chars)
            # 压缩是一次 LLM 改写，可能把刚剔掉的规则又写回来，再过滤一遍
            updated, more_dropped = strip_role_scope_clauses(updated)
            dropped.extend(more_dropped)

        # 最终硬保底
        if len(updated) > target_chars:
            updated = _hard_truncate(updated, target_chars)

        write_memory(memory_file, updated)

        elapsed = time.time() - start
        logger.info(
            f"用户长期记忆更新完成 | 更新后长度: {len(updated)} 字符 | 耗时: {elapsed:.2f}s\n"
            f"--- 记忆内容 ---\n{updated}\n--- 记忆内容结束 ---"
        )
        return True
    except Exception as e:
        logger.error(f"update_long_term_memory_after_session 异常: {e}", exc_info=True)
        # 异常时回退到仅做长度检查
        return enforce_memory_limit(memory_file, llm, max_chars=max_chars)
