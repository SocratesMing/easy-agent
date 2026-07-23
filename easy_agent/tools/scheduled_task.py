"""LangChain tool for creating scheduled tasks.

The AI agent calls this tool when the user requests a scheduled/periodic task.
The tool validates the cron expression, persists the task to DB, and registers
it with the APScheduler runtime.
"""

import logging
import uuid
from datetime import datetime

from langchain_core.tools import BaseTool
from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, Field

from ..db import get_database
from ..models.db import ScheduledTaskModel
from ..utils.task_logger import log_task_event

logger = logging.getLogger("easy_agent.tools.scheduled_task")


class CreateScheduledTaskArgs(BaseModel):
    name: str = Field(..., description="任务名称，简短描述任务目的")
    description: str = Field(default="", description="任务详细描述")
    schedule_cron: str = Field(
        ...,
        description=(
            "5字段标准 cron 表达式（分 时 日 月 周）。示例："
            "'0 8 * * *'（每天8:00）、'0 9 * * 1'（每周一9:00）、"
            "'*/30 * * * *'（每30分钟）、'0 0 1 * *'（每月1号0点）、"
            "'0 9 * * 1-5'（工作日9:00）"
        ),
    )
    task_prompt: str = Field(
        ...,
        description=(
            "任务执行时发送给 Agent 的提示词。必须自包含完整上下文，"
            "因为执行时没有对话历史。例如：'检查 /workspace/ 目录下的文件数量并列出最近修改的文件'"
        ),
    )


class CreateScheduledTaskTool(BaseTool):
    name: str = "create_scheduled_task"
    description: str = (
        "创建定时任务。当用户需要定期/定时/周期性执行某项操作时使用此工具。"
        "提供 cron 表达式定义执行时间，提供自包含的 task_prompt 定义执行内容。"
        "任务创建后将按 cron 表达式自动定时执行。"
    )
    args_schema: type = CreateScheduledTaskArgs

    username: str = ""
    session_id: str = ""

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("此工具仅支持异步调用，请使用 _arun")

    async def _arun(self, name: str, description: str, schedule_cron: str, task_prompt: str) -> str:
        # 验证 cron 表达式
        try:
            CronTrigger.from_crontab(schedule_cron)
        except ValueError as e:
            return f"错误：无效的 cron 表达式 '{schedule_cron}'：{e}。请提供标准的5字段 cron 表达式（分 时 日 月 周）。"

        # 注册到 scheduler
        try:
            from ..services.scheduler import get_scheduler, register_scheduled_task
            get_scheduler()  # 确保已初始化
        except RuntimeError as e:
            return f"错误：调度器未初始化：{e}"

        task_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        task = ScheduledTaskModel(
            task_id=task_id,
            username=self.username,
            session_id=self.session_id,
            name=name,
            description=description,
            schedule_cron=schedule_cron,
            task_prompt=task_prompt,
            enabled=1,
            created_at=now,
            updated_at=now,
        )

        db = get_database()
        # 解析工作目录：若由会话生成则复用该会话目录，否则使用独立目录
        workspace_name = ""
        if self.session_id:
            session = db.get_session(self.session_id)
            if session and getattr(session, "workspace_name", ""):
                workspace_name = session.workspace_name
        if not workspace_name:
            workspace_name = f"scheduled_{task_id}"
        task.workspace_name = workspace_name

        db.create_scheduled_task(task)

        next_run = register_scheduled_task(task)
        if next_run:
            db.update_scheduled_task_run_times(task_id, "", next_run)

        logger.info(f"定时任务已创建 | task_id={task_id} | name={name} | cron={schedule_cron} | user={self.username}")
        log_task_event(
            username=self.username,
            task_id=task_id,
            operation="create",
            detail=f"任务创建: name={name}, cron={schedule_cron}, prompt={task_prompt[:80]}",
            name=name,
            cron=schedule_cron,
            session_id=self.session_id,
            next_run=next_run or "",
        )

        return (
            f"定时任务创建成功！\n"
            f"- 任务名称：{name}\n"
            f"- Cron 表达式：{schedule_cron}\n"
            f"- 下次执行时间：{next_run}\n"
            f"- 任务ID：{task_id}\n"
            f"请告知用户任务已创建，将在下次执行时间自动运行。"
        )
