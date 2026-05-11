"""Prompts management API routes"""

import logging
import os

from fastapi import APIRouter, Body, Query

from ..config import Config

logger = logging.getLogger("easy_agent.prompts")

prompts_router = APIRouter(prefix="/api/prompts", tags=["Prompts"])


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


@prompts_router.post("/query", summary="查询所有提示词文档")
def query_all() -> dict:
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
def read_prompt(path: str = Query("fx/options_quote", description="md路径")) -> dict:
    prompts_base = _get_prompts_dir()
    logger.info("读取提示词 [%s]", path)
    try:
        md_path = os.path.join(prompts_base, f"{path}.md")
        if os.path.exists(md_path):
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
            return {"path": path, "content": content}
        return {"path": path, "content": f"文件不存在: {md_path}"}
    except Exception as e:
        logger.error("读取文件Error %s", e)
        return {"path": path, "content": str(e)}


@prompts_router.post("/update", summary="更新单个提示词文档")
def update_prompt(path: str = Body(...), content: str = Body(...)) -> str:
    prompts_base = _get_prompts_dir()
    logger.info("[%s].md 更新内容", path)
    try:
        md_dir = os.path.join(prompts_base, os.path.dirname(path))
        os.makedirs(md_dir, exist_ok=True)
        md_path = os.path.join(prompts_base, f"{path}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"[{path}]写入完成"
    except Exception as e:
        logger.error("写入文件Error %s", e)
        return f"[{path}]写入失败 {e}"
