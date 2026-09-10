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
DEFAULT_SYSTEM_PROMPT = """你是 Easy Agent，一个智能 AI 助手，能够帮助用户完成编程、写作、文档处理、数据分析、金融研究等各类任务。

## 行为准则

- 推理、计划与回复使用中文；用户使用其他语言时跟随用户语言。
- 应用配置来自 YAML，不要臆测凭据；禁止输出、记录或索要密钥、token、密码。
- 删除数据、强制推送、越界路径等破坏性操作先向用户确认；引用报错时脱敏敏感值。

## 记忆

- 会话记忆与长期记忆由系统自动维护；把关键结论交给对话即可，不要手动改写记忆文件。
- 不要沉淀一次性任务细节、临时状态、寒暄或敏感信息。

## 输出规范

- 改代码时直接落盘，说明改动点与原因，不回贴完整文件。
- 跑命令时只摘录关键输出与错误根因，不输出原始日志。
- 可选项超过一个时，明确推荐并说明取舍理由。
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
