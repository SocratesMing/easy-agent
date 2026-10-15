"""提示词加载器。

所有提示词统一放在 ``easy_agent/config/prompts/`` 下，启动时一次性读取：

```
prompts/
├── system.md              # 主系统提示词
├── fragments/*.md         # 片段，按文件名字典序拼接到系统提示词末尾
├── memory_update.md       # 会话记忆生成
├── memory_compress.md     # 记忆压缩
└── long_term_memory.md    # 用户长期记忆生成
```

设计要点：
- **文件为权威，代码内置为兜底**：文件缺失/读取失败时回落到内置文本，
  保证部署漏挂载时 Agent 仍可用，而不是直接崩溃。
- **按 mtime 缓存**：避免每轮对话重复读盘。
- **占位符用 string.Template（``$name``）**：提示词正文含 JSON 示例等大括号内容，
  用 ``str.format`` 会抛 KeyError。
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from string import Template

_SERVICE_DIR = Path(__file__).resolve().parent  # easy_agent/services
_PACKAGE_DIR = _SERVICE_DIR.parent  # easy_agent

DEFAULT_PROMPTS_DIR = _PACKAGE_DIR / "config" / "prompts"

# 兜底系统提示词（与 prompts/system.md 核心内容一致，文件缺失时使用）
DEFAULT_SYSTEM_PROMPT = """
你是 Easy Agent，一个在会话中配合用户执行工作的 AI Agent，能够帮助用户完成编程、写作、文档处理、数据分析、金融研究等各类任务。

## 工作方式

- 推理、计划与回复使用中文；用户使用其他语言时跟随用户语言，后端代码和路径名默认使用 ASCII。
- 不要凭空宣称工具存在；工具可用的前提是本轮会话已注入相应工具，回答前先核对可用能力。
- 因实时信息产生的动作须核对现有事实；必要时先联网搜索，避免把过时或不存在的数据当作事实。
- 涉及工具调用时，让结果决定下一步而不是继续空转；失败就修正方案，不原样重复同一调用。
- 先明确用户目标；存在关键歧义时先澄清，能继续推进时就直接推进。
- 计划说明要紧凑；复杂任务拆成可验证的步骤，逐个执行并核对中间结果。
- 自主完成可交付的阶段结果，而不是只回复想法；把回复当作计划，把文件、数据、代码或命令结果当作交付。
- 输出前核对已实现部分是否满足要求、引用是否有依据、文件是否写在正确位置。

## 安全与隔离

- 应用配置来自 YAML，不要臆测凭据；禁止输出、记录或索要密钥、token、密码。
- 删除数据、重写不可恢复文件、强制推送、越界路径等破坏性操作必须先得到用户明确确认。
- 目录删除这类不可逆操作直接拒绝并说明原因，不要原样重试。
- 数据按用户与会话隔离；不要跨用户或跨会话访问、引用或留存别的会话数据。
- 引用错误、日志或外部结果时脱敏敏感值，不把配置细节当作用户的私有数据。

## 记忆

- 会话记忆与长期记忆由系统自动维护；把关键结论交给对话即可，不要手动改写记忆文件。
- 不要沉淀一次性任务细节、临时状态、寒暄或敏感信息；用户偏好和可复用经验由系统负责提取。
- 长期个人记忆文件的更新按四个方面组织：工作背景、个人背景、当前关注与近期动态；无内容的小节省略。
- 长期个人记忆总长度不超过 1500 字符；更新时合并同类信息，删除过时、重复和低价值内容。

## 专家模式

- 用户选择专家模式时以该专家的专业视角工作；专家补充内容有更高优先级，但不得降低安全和隔离要求。
- 专家补充内容与系统规则冲突时优先遵循系统级要求，并向用户说明保留的安全边界。

## 命令

- 执行 Python 命令优先使用 `uv run`（例如 `uv run python xxx.py`），以复用项目的 uv 环境与依赖。
- 当环境未安装 uv 或缺少依赖导致失败时，再回退到 `python3 xxx.py`；不要编造命令输出。

## 提交输出

- 交付物优先写入当前工作区，并标注文件路径与摘要。
- 回复优先给结论和关键过程；不回显完整原始日志或完整文件。
- 前端页面、数据表和代码应能直接被用户理解或复用；不确定事项明确标记。
"""


# (mtime, content) 缓存
_CACHE: dict[str, tuple[float, str]] = {}

# 由 configure_prompts_dir() 设置（来自配置项 agent.prompt_path）
_CONFIGURED_DIR: Path | None = None


def configure_prompts_dir(
    path: str | Path | None, base_dir: Path | str | None = None
) -> None:
    """由应用在读取配置后设置提示词目录（配置项 agent.prompt_path）。

    相对路径会基于 ``base_dir``（配置目录）解析。设置后，系统提示词与
    记忆类提示词都从这里读取。
    """
    global _CONFIGURED_DIR
    if not path:
        _CONFIGURED_DIR = None
        return
    resolved = Path(path)
    if not resolved.is_absolute() and base_dir:
        resolved = Path(base_dir) / resolved
    _CONFIGURED_DIR = resolved


def get_prompts_dir() -> Path:
    """提示词目录优先级：configure_prompts_dir 设置 > EASY_PROMPTS_DIR > 包内默认。"""
    if _CONFIGURED_DIR is not None:
        return _CONFIGURED_DIR
    env = os.getenv("EASY_PROMPTS_DIR", "").strip()
    return Path(env) if env else DEFAULT_PROMPTS_DIR


def _read(path: Path) -> str | None:
    """带 mtime 缓存地读取文本文件，失败返回 None。"""
    try:
        if not path.is_file():
            return None
        mtime = path.stat().st_mtime
        key = str(path)
        cached = _CACHE.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        text = path.read_text(encoding="utf-8")
        _CACHE[key] = (mtime, text)
        return text
    except OSError:
        return None


def _candidate_dirs(
    config_dir: Path | str | None = None,
    prompt_path: str | Path | None = None,
) -> Iterable[Path]:
    """按优先级产出可能的 prompts 目录。"""
    if prompt_path:
        path = Path(prompt_path)
        if not path.is_absolute() and config_dir:
            path = Path(config_dir) / path
        yield path
    if config_dir:
        yield Path(config_dir) / "prompts"
    yield get_prompts_dir()


def load_prompt(name: str, prompts_dir: Path | str | None = None) -> str | None:
    """读取单个提示词文件（不含扩展名），缺失返回 None。"""
    if prompts_dir:
        text = _read(Path(prompts_dir) / f"{name}.md")
        if text is not None:
            return text
    return _read(get_prompts_dir() / f"{name}.md")


def render(template: str, **kwargs: object) -> str:
    """用 string.Template 渲染提示词，对正文中的 JSON 大括号免疫。"""
    return Template(template).safe_substitute(**kwargs)


def load_system_prompt(
    prompt_path: str | Path | None = None,
    config_dir: Path | str | None = None,
) -> str:
    """加载系统提示词 = 主文件 + 片段文件（按文件名字典序）。

    ``prompt_path`` 对应配置项 ``agent.prompt_path``，指向**提示词目录**
    （相对路径基于 ``config_dir`` 解析）。优先级：

    1. ``prompt_path`` 目录 / ``<config_dir>/prompts`` / ``EASY_PROMPTS_DIR`` 下的 system.md
    2. 兼容旧配置：``prompt_path`` 若指向单个 md 文件则直接读取
    3. 内置 ``DEFAULT_SYSTEM_PROMPT``
    """
    for prompts_dir in _candidate_dirs(config_dir, prompt_path):
        main = _read(prompts_dir / "system.md")
        if main is None:
            continue
        parts = [main.rstrip()]
        frag_dir = prompts_dir / "fragments"
        if frag_dir.is_dir():
            for frag in sorted(frag_dir.glob("*.md")):
                text = _read(frag)
                if text and text.strip():
                    parts.append(text.strip())
        return "\n\n".join(parts) + "\n"

    # 兼容旧配置：prompt_path 指向单个 md 文件
    if prompt_path and str(prompt_path).endswith(".md"):
        path = Path(prompt_path)
        if not path.is_absolute() and config_dir:
            path = Path(config_dir) / path
        text = _read(path)
        if text is not None:
            return text

    return DEFAULT_SYSTEM_PROMPT


def clear_cache() -> None:
    """清空缓存与已配置的提示词目录（测试或提示词热更新后使用）。"""
    global _CONFIGURED_DIR
    _CACHE.clear()
    _CONFIGURED_DIR = None
