"""会话级记忆管理服务（基于工作区持久化）。

负责：
1. 每轮对话后根据上下文更新工作区下的 memory.md（项目、经验与偏好）
2. 监控记忆文件长度，确保不超过 MAX_MEMORY_CHARS (600) 字符
3. 超过限制时自动调用 LLM 压缩，保留核心信息

记忆文件位置：workspace/{username}/{workspace_name}/memory.md
生命周期：会话创建时预创建工作区目录 → 首轮响应后写入 memory.md → 后续输入时自动加载
"""

import logging
import time
from pathlib import Path

logger = logging.getLogger("easy_agent.memory")

MAX_MEMORY_CHARS = 600
# 触发压缩的阈值（达到此长度即压缩，留出余量避免频繁触发）
COMPRESS_THRESHOLD = 540

# 用户级长期记忆（memories/{username}/AGENTS.md）上限，跨会话积累故比会话级更大
MAX_LONG_TERM_MEMORY_CHARS = 800
LONG_TERM_COMPRESS_THRESHOLD = 720

COMPRESS_PROMPT = """你是一个记忆压缩助手。请将下面的"会话记忆"内容压缩到不超过 {max_chars} 个字符，要求：
1. 保留所有可复用的经验、用户偏好、行为原则、关键解决方案
2. 删除一次性任务、临时状态、过时信息
3. 合并相似条目，用更精炼的表述
4. 保持 Markdown 格式和层级结构
5. 绝不能丢失关键信息——若空间不足，优先压缩旧内容

原始记忆：
---
{content}
---

请直接输出压缩后的完整记忆内容（不要添加任何解释说明）："""


UPDATE_MEMORY_PROMPT = """你是一个会话记忆管理助手。请根据本次对话内容，更新当前会话的记忆文件（memory.md）。目标是让后续会话能够快速恢复上下文，并继续当前项目工作。

## 通用规则

1. **合并而非堆叠**：更新已有信息，删除过时内容、重复表述和寒暄。
2. **不记敏感信息**：密钥、密码、token、隐私数据一律不记；报错信息中若包含敏感值应脱敏。
3. **根据上下文提炼**：不按任务简单或复杂分类。优先总结当前项目做什么、已完成的关键结果、可复用经验、用户偏好和后续必要动作。
4. **精炼高密度表达**：每条信息用一两句话概括，只保留能帮助继续实施、复现、验证和决策的内容。
5. **省略无价值内容**：没有可靠内容时省略对应小节；不要为了填满长度扩写，不要记录一次性过程、原始日志或临时状态。
6. **总长度限制**：更新后总内容不得超过 {target_chars} 字符。接近上限时，只保留高价值信息。

## 建议结构

使用 Markdown 二级标题按内容组织，建议为：`## 当前项目`、`## 经验与注意事项`、`## 用户偏好`、`## 下一步`。只保留有信息量的标题和条目；没有可靠内容时省略对应小节。

## 输出预算

目标长度：不超过 {target_chars} 字符

## 当前记忆文件内容

---
{current_memory}
---

## 本次对话内容（摘要）

**用户输入**：
{user_message}

**助手回复**（节选）：
{assistant_response}

---

请直接输出更新后的完整记忆文件内容（Markdown 格式，以 `# 会话记忆` 作为一级标题，不要添加任何解释说明）："""


def build_memory_update_prompt(
    current_memory: str,
    user_message: str,
    assistant_response: str,
    max_chars: int = MAX_MEMORY_CHARS,
) -> str:
    """Build a compact, context-aware session memory update prompt."""
    target_chars = min(MAX_MEMORY_CHARS, max_chars)
    return UPDATE_MEMORY_PROMPT.format(
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

        prompt = COMPRESS_PROMPT.format(max_chars=max_chars, content=content)
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


UPDATE_LONG_TERM_MEMORY_PROMPT = """你是一个用户长期记忆管理助手。请根据本次对话内容，更新用户的长期记忆文件（AGENTS.md）。

## 这是什么
这是**用户级别**的长期记忆，跨所有会话持久化，根据上下文记录该用户相对稳定、长期有效的信息：
- 个人偏好与习惯（如沟通风格、技术栈倾向、输出格式要求）
- 跨会话可复用的经验、踩过的坑、关键解决方案
- 长期项目背景、当前项目目标中不会随本次会话结束而失效的信息
- 不随单个会话结束而失效的稳定信息

## 更新规则
1. **只记长期稳定、跨会话可复用的信息**：不要记录一次性任务、临时状态、寒暄或完整执行过程。
2. **合并而非堆叠**：若新信息与已有条目重复或相近，合并精炼，不要产生多篇重复内容。
3. **用户偏好优先**：用户明确表达出的稳定偏好（如界面要求、格式习惯）应被记录并长期保留。
4. **不记敏感信息**：密钥、密码、token、隐私数据一律不记。
5. **精炼表达**：每条用一句话概括，结构清晰，使用二级标题按主题分章节（如 `## 用户偏好`、`## 当前项目背景`、`## 可复用经验`）。
6. **总长度限制**：更新后总内容不得超过 {target_chars} 字符。接近上限时优先保留长期有效的偏好与核心经验。

## 输出预算

目标长度：不超过 {target_chars} 字符

只记录长期稳定、跨会话可复用的信息；没有可靠内容时省略对应小节，不要为填满长度扩写。

## 当前长期记忆内容

---
{current_memory}
---

## 本次对话内容（摘要）

**用户输入**：
{user_message}

**助手回复**（节选）：
{assistant_response}

---

请直接输出更新后的完整长期记忆文件内容（Markdown 格式，以 `# 用户长期记忆` 作为一级标题，不要添加任何解释说明）："""


def build_long_term_memory_update_prompt(
    current_memory: str,
    user_message: str,
    assistant_response: str,
    max_chars: int = MAX_LONG_TERM_MEMORY_CHARS,
) -> str:
    """Build a size-aware long-term memory update prompt."""
    target_chars = min(MAX_LONG_TERM_MEMORY_CHARS, max_chars)
    return UPDATE_LONG_TERM_MEMORY_PROMPT.format(
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
        max_chars: 长期记忆字符上限，默认 4000（比会话级更大）

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

        target_chars = max_chars
        compression_threshold = min(LONG_TERM_COMPRESS_THRESHOLD, target_chars)

        # 若更新后超过目标预算，触发压缩
        if len(updated) > compression_threshold:
            logger.info(
                f"更新后长期记忆超过目标预算 ({len(updated)} > {target_chars} 字符)，触发压缩"
            )
            updated = compress_memory(updated, llm, max_chars=target_chars)

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
