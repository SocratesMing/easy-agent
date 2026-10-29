"""流式包结构测试：包入口只有说明、不转出任何名字；被同名包遮住的旧垫片已删除。

C2 拆分后包入口曾保留一层兼容门面，让旧的 ``from easy_agent.services.streaming import X``
继续可用。调用方都已改为从定义所在的子模块导入，门面随之删除（见
``docs/design/2026-10-20-services-subpackages-and-layering.md`` §7）。
"""

import ast
from pathlib import Path

import easy_agent.services.streaming as streaming

PACKAGE_DIR = Path(streaming.__file__).parent


def test_package_init_only_has_a_docstring():
    tree = ast.parse((PACKAGE_DIR / "__init__.py").read_text(encoding="utf-8"))
    assert ast.get_docstring(tree)
    # 只有说明：没有导入、赋值，也没有 PEP 562 的惰性转出
    assert len(tree.body) == 1


def test_shadowed_shim_is_gone():
    # 同目录的 streaming.py 被包遮住，运行时从不加载
    assert not (PACKAGE_DIR.parent / "streaming.py").exists()
