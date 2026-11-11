"""Prompts management API routes

SEC-08：提示词文档会进入所有用户 / 专家的系统提示词，读写都只允许管理员；
``read`` / ``update`` 的 ``path`` 解析后必须仍落在 prompts 目录内，防止
``..`` 穿越或绝对路径读写目录外的 ``.md`` 文件（审计 PoC：未认证读 README、
写 ``memories/{user}/AGENTS.md`` 做跨用户 Prompt 注入）。
"""

import logging
import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from easy_agent.bootstrap.config import Config
from easy_agent.middleware import get_current_username
from easy_agent.utils.auth import is_super_admin

logger = logging.getLogger("easy_agent.prompts")

prompts_router = APIRouter(prefix="/agent/prompts", tags=["Prompts"])

# 提示词管理是全局配置，只放行超级管理员账号（admin / liufengbao）。


def _require_admin(username: str) -> None:
    """提示词管理仅限超级管理员（未登录的情况由 get_current_username 返回 401）。"""
    if not is_super_admin(username):
        raise HTTPException(status_code=403, detail="仅管理员可以操作提示词")


def _get_prompts_dir() -> str:
    try:
        cfg = Config.load()
        return getattr(cfg.tools, "prompts_dir", None) or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts"
        )
    except Exception:
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts"
        )


def _resolve_prompt_path(prompts_base: str, path: str) -> Path:
    """把 ``path`` 解析成 prompts 目录内的 ``.md`` 绝对路径（SEC-08）。

    绝对路径、``..`` 穿越、非法字符等解析后落到目录外的输入一律 400，读写都不触达
    目录外的文件。校验用 ``resolve()``（会展开 ``..`` 与符号链接），因此符号链接
    指向目录外同样被拒绝。
    """
    if not isinstance(path, str) or not path.strip():
        raise HTTPException(status_code=400, detail="非法的文件路径")
    try:
        base = Path(prompts_base).resolve()
        target = Path(prompts_base, f"{path}.md").resolve()
    except (OSError, ValueError):
        # 含 NUL 等无法构成路径的输入
        raise HTTPException(status_code=400, detail="非法的文件路径") from None
    if not target.is_relative_to(base):
        logger.warning("拒绝越界访问提示词文件 | path=%s | base=%s", path, base)
        raise HTTPException(status_code=400, detail="非法的文件路径")
    return target


@prompts_router.post("/query", summary="查询所有提示词文档")
def query_all(
    username: Annotated[str, Depends(get_current_username)],
) -> dict:
    _require_admin(username)
    prompts_base = _get_prompts_dir()
    res = []
    logger.info("查询所有提示词文档 %s", prompts_base)
    if os.path.isdir(prompts_base):
        for biz_type in os.listdir(prompts_base):
            prompt_path = os.path.join(prompts_base, biz_type)
            if os.path.isdir(prompt_path):
                for file in os.listdir(prompt_path):
                    if file.endswith(".md"):
                        res.append(os.path.join(biz_type, file))
    return {"data": res}


@prompts_router.post("/read", summary="读取单个提示词文档")
def read_prompt(
    username: Annotated[str, Depends(get_current_username)],
    path: str = Query("fx/options_quote", description="md路径"),
) -> dict:
    _require_admin(username)
    prompts_base = _get_prompts_dir()
    logger.info("读取提示词 [%s] | 操作人=%s", path, username)
    md_path = _resolve_prompt_path(prompts_base, path)
    try:
        if md_path.exists():
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
            return {"path": path, "content": content}
        return {"path": path, "content": f"文件不存在: {md_path}"}
    except Exception as e:
        logger.error("读取文件Error %s", e)
        return {"path": path, "content": str(e)}


@prompts_router.post("/update", summary="更新单个提示词文档")
def update_prompt(
    username: Annotated[str, Depends(get_current_username)],
    path: str = Body(...),
    content: str = Body(...),
) -> str:
    _require_admin(username)
    prompts_base = _get_prompts_dir()
    md_path = _resolve_prompt_path(prompts_base, path)
    logger.info("[%s].md 更新内容 | 操作人=%s", path, username)
    try:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info("[%s].md 更新完成 | 操作人=%s", path, username)
        return f"[{path}]写入完成"
    except Exception as e:
        logger.error("写入文件Error %s", e)
        return f"[{path}]写入失败 {e}"
