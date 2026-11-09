"""三份 .env.*.example 的统一基线回归测试。

保证 dev/test/prod 的变量名集合、数量与分组顺序完全一致，每项显式非空、
无重复键；敏感项使用统一哨兵前缀且哨兵值不会被当成真实凭据。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from easy_agent.bootstrap.env import PLACEHOLDER_PREFIX, is_unset_placeholder

ROOT = Path(__file__).resolve().parents[2]
FILES = [ROOT / f".env.{env}.example" for env in ("dev", "test", "prod")]
KEY_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
# 只匹配「整行注释且值为简单 token」的旧式注释键，避免把说明文字误判为键。
COMMENTED_KEY_RE = re.compile(r"^\s*#\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^\s#]+)\s*$")

SECURITY_KEYS = (
    "EASY_JWT_SECRET",
    "EASY_CORS_ALLOW_ORIGINS",
    "EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD",
)

# 本轮从配置基线移除的变量（qwen/ark 模型 + MCP 可配置开关），三份模板都不应再出现。
REMOVED_KEYS = frozenset({
    "QWEN_MODEL", "QWEN_API_KEY", "QWEN_API_BASE", "QWEN_CONTEXT_LENGTH",
    "ARK_MODEL", "ARK_API_KEY", "ARK_CONTEXT_LENGTH",
    "EASY_MCP_MAX_CONTENT_CHARS", "EASY_MCP_TOOL_PREFIX",
    "EASY_MCP_SESSION_IDLE_SECONDS", "EASY_MCP_SESSION_CONNECT_TIMEOUT_SECONDS",
    "EASY_MCP_SESSION_CLOSE_TIMEOUT_SECONDS", "EASY_MCP_SESSION_CALL_TIMEOUT_SECONDS",
    "EASY_LLM_REQUEST_LOG",  # 旧 LLM 报文开关，已由 EASY_LOG_LEVEL 取代
})


def _parse(path: Path):
    text = path.read_text(encoding="utf-8")
    assert "\r" not in text, f"{path.name} 必须保持 LF 换行"
    keys: list[str] = []
    values: dict[str, str] = {}
    groups: list[str] = []
    commented: set[str] = set()
    for line in text.splitlines():
        if line.startswith("# ── "):
            groups.append(line)
        mc = COMMENTED_KEY_RE.match(line)
        if mc:
            commented.add(mc.group(1))
        if line.lstrip().startswith("#"):
            continue
        m = KEY_RE.match(line)
        if m:
            keys.append(m.group(1))
            values[m.group(1)] = m.group(2)
    return keys, values, groups, commented


def test_templates_have_identical_key_set_count_and_order():
    parsed = [_parse(path) for path in FILES]
    for (keys, _values, _groups, _commented), path in zip(parsed, FILES):
        assert len(keys) == len(set(keys)), f"{path.name} 存在重复键"
    assert parsed[0][0] == parsed[1][0] == parsed[2][0]
    assert len(parsed[0][0]) == 121, "基线变量数应为 121"
    assert parsed[0][2] == parsed[1][2] == parsed[2][2], "分组顺序不一致"


def test_removed_variables_are_absent_from_all_templates():
    """qwen/ark 与 MCP 可配置开关已从三套基线中移除。"""
    for path in FILES:
        keys, _values, _groups, _commented = _parse(path)
        assert not (REMOVED_KEYS & set(keys)), f"{path.name}: 仍含已删除变量"


def test_every_template_value_is_nonempty():
    for path in FILES:
        keys, values, _groups, _commented = _parse(path)
        for key in keys:
            assert values[key].strip(), f"{path.name}: {key} 的值为空"


def test_security_items_use_sentinel_and_sentinel_values_are_wellformed():
    for path in FILES:
        _keys, values, _groups, _commented = _parse(path)
        for key in SECURITY_KEYS:
            assert values[key] == PLACEHOLDER_PREFIX + key, f"{path.name}: {key} 未使用哨兵"
        for key, value in values.items():
            if value.startswith(PLACEHOLDER_PREFIX):
                assert value == PLACEHOLDER_PREFIX + key, f"{path.name}: {key} 哨兵格式错误"


def test_no_commented_keys_shadow_active_keys():
    for path in FILES:
        keys, _values, _groups, commented = _parse(path)
        assert not (commented & set(keys)), f"{path.name}: 注释键与生效键冲突"


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, True),
        ("", True),
        ("   ", True),
        (PLACEHOLDER_PREFIX + "ANY_KEY", True),
        ("CHANGE_ME", True),
        ("replace_me", True),
        ("  real secret with spaces  ", False),
        ("https://app.example.com", False),
    ],
)
def test_is_unset_placeholder(value, expected):
    assert is_unset_placeholder(value) is expected
