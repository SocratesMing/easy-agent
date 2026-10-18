#!/usr/bin/env python3
"""配置 schema / 键集合门禁（E2）。

检查两件事：

1. ``config-example.yaml`` 只使用 ``easy_agent.config`` 中登记的键（未知键报错）；
2. 若本地存在 ``config.yaml``，其使用到的键必须都能在模板中找到
   （模板是“键集合”的唯一文档来源；旧/废弃键除外）。

用法::

    python scripts/quality/check_config_schema.py            # 打印报告
    python scripts/quality/check_config_schema.py --check    # CI 门禁（有错误退出 1）

说明：``config.yaml`` 被 gitignore，CI 中通常不存在，此时只校验模板；
本地开发时额外做“本地配置 -> 模板”的漂移检查。新增配置键时请同步更新
``config-example.yaml``，否则本地门禁会失败，避免模板再次漂移。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from easy_agent.config import (  # noqa: E402
    DEPRECATED_CONFIG_KEYS,
    KNOWN_SECTION_KEYS,
    find_unknown_config_keys,
)

DEFAULT_EXAMPLE = PROJECT_ROOT / "easy_agent" / "config" / "config-example.yaml"
DEFAULT_CONFIG = PROJECT_ROOT / "easy_agent" / "config" / "config.yaml"

REQUIRED_TOP_LEVEL_KEYS = frozenset({"model", "models"})


def _get_section(data: dict, path: str) -> dict | None:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current if isinstance(current, dict) else None


def load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} 必须是 YAML mapping")
    return data


def collect_key_paths(data: dict) -> set[str]:
    """收集 schema 关注的键路径（不展开列表与动态 map 的键）。"""
    paths: set[str] = {str(key) for key in data}

    for section_path in KNOWN_SECTION_KEYS:
        section = _get_section(data, section_path)
        if section is None:
            continue
        for key in section:
            paths.add(f"{section_path}.{key}")

    models = data.get("models")
    if isinstance(models, dict):
        for provider_cfg in models.values():
            if isinstance(provider_cfg, dict):
                for key in provider_cfg:
                    paths.add(f"models.*.{key}")
    return paths


def schema_errors(data: dict, label: str) -> list[str]:
    errors = [f"{label}: 未知键 {key}" for key in find_unknown_config_keys(data)]
    missing = sorted(REQUIRED_TOP_LEVEL_KEYS - set(data))
    errors.extend(f"{label}: 缺少必需顶层键 {key}" for key in missing)
    return errors


def drift_errors(example: dict, actual: dict, label: str) -> list[str]:
    """actual 使用而 example 未记录的键（模板漂移）。"""
    example_paths = collect_key_paths(example)
    actual_paths = collect_key_paths(actual)
    ignored = set(DEPRECATED_CONFIG_KEYS)
    return [
        f"{label}: 键 {path} 未在 config-example.yaml 中记录（请同步模板）"
        for path in sorted(actual_paths - example_paths - ignored)
    ]


def check(
    example_path: Path = DEFAULT_EXAMPLE,
    config_path: Path | None = DEFAULT_CONFIG,
) -> tuple[list[str], list[str]]:
    """返回 (errors, infos)。config_path 不存在时跳过漂移检查。"""
    errors: list[str] = []
    infos: list[str] = []

    if not example_path.is_file():
        return [f"模板不存在: {example_path}"], infos
    example = load_yaml(example_path)
    errors.extend(schema_errors(example, "config-example.yaml"))
    infos.append(
        f"config-example.yaml: schema 检查通过（{len(collect_key_paths(example))} 个键路径）"
    )

    if config_path is None or not config_path.is_file():
        infos.append("config.yaml 不存在，跳过本地键集合漂移检查")
        return errors, infos

    actual = load_yaml(config_path)
    errors.extend(schema_errors(actual, "config.yaml"))
    errors.extend(drift_errors(example, actual, "config.yaml"))
    infos.append("config.yaml: 已与模板做键集合漂移检查")
    return errors, infos


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="有错误时退出 1（CI）")
    parser.add_argument("--example", type=Path, default=DEFAULT_EXAMPLE)
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="本地 config.yaml（不存在则跳过漂移检查）",
    )
    args = parser.parse_args(argv)

    try:
        errors, infos = check(args.example, args.config)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"[FAIL] 读取配置失败: {exc}", file=sys.stderr)
        return 1

    for info in infos:
        print(f"[INFO] {info}")
    for error in errors:
        print(f"[FAIL] {error}", file=sys.stderr)

    if errors:
        print(f"[FAIL] 配置 schema 检查失败：{len(errors)} 项", file=sys.stderr)
        return 1
    print("[OK] 配置 schema / 键集合检查通过")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
