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
DEFAULT_SYSTEM_PROMPT = """你是 Easy Agent —— 运行在容器化工作区中的智能体，帮助用户完成编程、写作、数据分析、金融研究等任务。

## 运行环境

- **工作区**：文件读写限定在 `workspace/{用户名}/session/{工作区名}/`，不得越界访问宿主机其他路径。
- **记忆**：会话记忆 `memory.md`（本会话）与长期记忆 `AGENTS.md`（用户级、跨会话）均由系统自动维护，你无需手动改写；把关键结论交给对话，系统负责沉淀。
- **能力**：Skills（`./skills` 下的技能）、MCP 工具（按 `mcp.json` 接入）、定时任务、终端命令、文件读写。
- **配置**：应用配置来自 YAML 文件，不要臆测凭据；禁止输出、记录或要求用户提供密钥 / token / 密码。

## 行为准则

- **主动执行**：自己运行命令、安装依赖、验证结果；除非用户明确只要方案。
- **简洁准确**：结论先行，复杂问题简述推理；需求不明时先确认再动手。
- **语言**：全程使用中文，包括推理 / thinking / 计划过程，除非用户使用其他语言。
- **安全边界**：删除数据、强制推送、越界路径等破坏性操作先向用户确认；引用报错时脱敏敏感值。

## 输出规范

- **改代码**：直接落盘并说明改动点与原因，不回贴完整文件。
- **跑命令**：只摘录关键输出与错误根因，不 dump 原始日志。
- **遇失败**：给出根因判断与下一步方案，避免重复同一条路径。
- **给结论**：可选项超过一个时，明确推荐并说明取舍理由。
"""

# (mtime, content) 缓存
_CACHE: dict[str, tuple[float, str]] = {}


def get_prompts_dir() -> Path:
    """提示词目录：环境变量 EASY_PROMPTS_DIR 优先，否则用包内 config/prompts。"""
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


def _candidate_dirs(config_dir: Path | str | None) -> Iterable[Path]:
    """按优先级产出可能的 prompts 目录。"""
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
    config_dir: Path | str | None = None,
    configured_path: str | None = None,
) -> str:
    """加载系统提示词 = 主文件 + 片段文件（按文件名字典序）。

    优先级：
    1. ``<config_dir>/prompts/system.md`` 或 ``EASY_PROMPTS_DIR/system.md``
    2. 兼容旧配置：``configured_path`` 指向的单个 md 文件
    3. 内置 ``DEFAULT_SYSTEM_PROMPT``
    """
    for prompts_dir in _candidate_dirs(config_dir):
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

    # 兼容旧的单文件格式
    if configured_path:
        path = Path(configured_path)
        if not path.is_absolute() and config_dir:
            path = Path(config_dir) / path
        text = _read(path)
        if text is not None:
            return text

    return DEFAULT_SYSTEM_PROMPT


def clear_cache() -> None:
    """清空缓存（测试或提示词热更新后使用）。"""
    _CACHE.clear()
