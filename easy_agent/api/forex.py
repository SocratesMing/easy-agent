"""Forex API routes - option quoting and bond bot"""

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, time as dt_time

from fastapi import APIRouter, Body
from langchain_core.messages import HumanMessage, SystemMessage

from ..config import Config
from ..services.agent_manager import _llm_instance

logger = logging.getLogger("easy_agent.forex")

forex_router = APIRouter(prefix="/api/forex", tags=["Forex"])

BOND_BOT: dict[int, list] = {}


def _get_llm():
    return _llm_instance


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


def _get_prompt_template(prompt_name: str) -> str:
    prompts_base = _get_prompts_dir()
    md_path = os.path.join(prompts_base, f"{prompt_name}.md")
    if os.path.exists(md_path):
        with open(md_path, "r", encoding="utf-8") as f:
            return f.read()
    logger.warning("提示词模板不存在: %s", md_path)
    return ""


@forex_router.post("/option_quote", summary="外汇期权报价")
async def options_quote(
    msgId: str = Body(...),
    content: str = Body(...),
    model: str = Body(default="MiniMax-M2.7"),
) -> dict:
    logger.info("msgId[%s] 外汇期权报价内容 %s", msgId, content)

    system_prompt = _get_prompt_template("fx/options_quote")
    llm = _get_llm()

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"报价内容为:{content}"),
    ]

    try:
        start = time.time()
        rep = llm.invoke(messages).content
        js_str = rep.strip("\n").strip("```").lstrip("json\n")
        time_use = round(time.time() - start, 2)
        logger.info("msgId[%s] 响应时间 [%s] 响应内容 %s", msgId, time_use, js_str)
        js_rep = json.loads(js_str)
        return js_rep
    except Exception as e:
        logger.error("msgId[%s] 解析错误 %s", msgId, e)
        return {"code": 200, "msg": "解析失败", "data": [], "reason": str(e)}


@forex_router.post("/bond_bot", summary="现券机器人")
def bond_bot(content: str, msg_id: int = 0) -> dict:
    try:
        logger.info("现券机器人 %s", content)
        llm = _get_llm()

        if msg_id in BOND_BOT:
            msg = BOND_BOT[msg_id]
            msg.append({"role": "user", "content": f"对手方消息:{content}"})
        else:
            msg_id = len(BOND_BOT) + 1
            system_prompt = _get_prompt_template("fx/options_bond")
            msg = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"对手内容为:{content}"},
            ]

        for message in msg:
            logger.info("现券机器人会话id-%s 历史消息为 %s", msg_id, message)

        lc_messages = []
        for m in msg:
            if m["role"] == "system":
                lc_messages.append(SystemMessage(content=m["content"]))
            else:
                lc_messages.append(HumanMessage(content=m["content"]))

        rep = llm.invoke(lc_messages).content
        BOND_BOT[msg_id] = msg

        json_str = ""
        if "```" in rep:
            match = re.search(r"```(?:json)?\s*(.*?)\s*```", rep, re.DOTALL)
            if match:
                json_str = match.group(1).strip()
        else:
            json_str = rep.strip("\n").strip("```").lstrip("json\n")

        js_rep = json.loads(json_str)
        msg.append(
            {
                "role": "system",
                "content": json.dumps(js_rep.get("data", [{}])[0], ensure_ascii=False),
            }
        )
        js_rep["msg_id"] = msg_id
        return js_rep
    except Exception as e:
        logger.error("现券机器人异常会话id-%s 原因 %s", msg_id, str(e))
        return {"code": 500, "errors": str(e)}


def _clear_bond_bot():
    BOND_BOT.clear()
    logger.info("定时任务 - 清空BOND_BOT")


def _run_bond_schedule():
    while True:
        now = datetime.now().time()
        if dt_time(23, 0, 0) <= now <= dt_time(23, 1, 0):
            _clear_bond_bot()
        time.sleep(60)


_bond_thread = threading.Thread(
    target=_run_bond_schedule, name="bond-bot-cleaner", daemon=True
)
_bond_thread.start()
