"""系统提示词可观测性工具（专家 Agent 提示词链路验证）。

用于回答一个具体问题：**「我召唤的专家提示词，真的被动态加载进模型终稿了吗？」**

做法是在专家提示词链路的三个环节各打一条带 **同一短指纹** 的日志：

1. 加载 —— ``api/expert.py`` 从磁盘读到专家提示词时；
2. 传递 —— ``services/agent_manager.py`` 组装 / 复用会话 Agent 时；
3. 拼接 —— ``agent.py`` 拼成最终 ``system_prompt`` 时。

只要**同一个指纹在 (1)(2)(3) 依次出现**，即可端到端证明该提示词已进入模型终稿；
指纹与预期专家不符、或 (3) 缺失，则说明读错版本 / 缓存未驱逐 / 附加内容被丢弃。

日志只输出「字符数 + 指纹 + 开头片段」，不落全文，避免刷屏与人设全文进日志。
若需要逐字查看终稿，可在此日志基础上再叠加「落盘开关」。
"""

import hashlib

#: 指纹长度（sha256 前 N 位十六进制，足够区分版本且便于肉眼比对）
FINGERPRINT_LEN = 8

#: 日志中展示的提示词开头片段长度
HEAD_CHARS = 80


def prompt_fingerprint(text: str) -> str:
    """返回提示词的短指纹（sha256 前 8 位）；空文本返回 ``-``。"""
    if not text:
        return "-"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:FINGERPRINT_LEN]


def prompt_head(text: str, limit: int = HEAD_CHARS) -> str:
    """返回提示词开头片段（换行折叠为空格，超长截断加省略号）。"""
    if not text:
        return "(空)"
    flat = " ".join(text.split())
    if not flat:
        return "(空白)"
    return flat[:limit] + ("..." if len(flat) > limit else "")


def describe_prompt(label: str, text: str) -> str:
    """一行式描述：``标签: 长度=N 字符 | 指纹=xxxxxxxx | 开头: ...``。

    空文本返回 ``标签: (空)``，便于在日志里一眼区分「没挂专家」与「挂了但读空」。
    """
    if not text or not text.strip():
        return f"{label}: (空)"
    return (
        f"{label}: 长度={len(text)} 字符 | 指纹={prompt_fingerprint(text)} | "
        f"开头: {prompt_head(text)}"
    )
