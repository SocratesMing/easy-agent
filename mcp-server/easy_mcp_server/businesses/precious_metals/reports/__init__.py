# -*- coding: utf-8 -*-
"""
报表注册表 —— 新增一张报表 = 在本目录加一个模块 + 在这里 import 一次。

约定：每个报表模块导出 REPORT（单张）或 REPORTS（多张）变量。
路由层只认这里注册的报表，各自声明的 dims / fields / keywords 互不干扰。
"""
from __future__ import annotations

from pm_core import Report
from reports import r8666_overview, r8670_detail

_ALL: list[Report] = []

for _mod in (r8670_detail, r8666_overview):
    for _attr in ("REPORT", "REPORTS"):
        _obj = getattr(_mod, _attr, None)
        if _obj is None:
            continue
        for _r in (_obj if isinstance(_obj, (list, tuple)) else [_obj]):
            if isinstance(_r, Report) and all(x.report_id != _r.report_id for x in _ALL):
                _ALL.append(_r)

BY_ID: dict[str, Report] = {r.report_id: r for r in _ALL}
ALL: list[Report] = _ALL


def get(report_id: str) -> Report | None:
    return BY_ID.get(str(report_id or "").strip())


def names() -> list[str]:
    return [r.name for r in ALL]


def labels() -> list[str]:
    """「业务域-报表名」形式的报表标识，用于错误提示与工具说明（不含报表编号）。"""
    return [r.label for r in ALL]
