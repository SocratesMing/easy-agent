"""文档 SQL → 知识库增量同步。

【这个文件是干什么的（零基础先看这里）】
外部有一个 MySQL 数据库（文中叫“文档 SQL”或“研报 SQL”），里面登记着
一篇篇研报/文档的信息：标题、发布日期、批次号（batchid）等；
真正要入库的文件（PDF、Word 等）放在服务器某个目录（file_root）里。
本模块负责把两边“配起来”，搬进知识库（RAGFlow）：

    MySQL 里的登记记录 ＋ 磁盘上的文件  →  上传到知识库  →  解析成可检索的文档

【几个必须知道的名词】
- 知识库（knowledge base）：RAGFlow 里存放文档、支持检索的地方；
- 增量同步：每次只处理“新出现”或“之前失败”的记录，已经入库的跳过，
  不会每次都把所有文件重传一遍；
- 台账（sync item，对应 knowledge_source_sync_items 表）：记录每条源记录
  同步到了哪一步（上传中/解析中/完成/失败），用来做断点续跑和防止重复上传；
- 幂等：同一条源记录不管处理多少次，最终只会有一份文档，
  靠“来源 source ＋ 源记录 ID source_record_id”这个唯一键来保证；
- 在途（inflight）：已经上传给知识库、但还没解析完成的文档；
- 滑动窗口：同时最多允许 max_inflight 个文档“在途”，满了就等一等，
  腾出空位再传下一个，避免把知识库压垮；
- dry-run（演习模式）：只打印“本来要做什么”，不真正上传，用于上线前检查。

流程：
1. 从外部 MySQL（文档 SQL）按 ``publishDate`` 取最近窗口内的记录；
2. 按 ``batchid``（与磁盘文件名相同）在 ``file_root`` 下匹配文件，
   生成 ``{report_title}_{id}.扩展名`` 的上传名；
3. 配置 ``staging_dir`` 时，先把本窗口内去重通过的文件批量拷贝到暂存目录；
4. 按 ``publish_date`` 在知识库内 find-or-create（有就复用、没有就新建）
   年/月/日 三级目录；
5. 调用 ``KnowledgeService.upload_document`` 入库，写入业务元数据；
6. 文档解析为 READY 后删除暂存副本（失败保留，便于排查/重试）；
7. 用 ``knowledge_source_sync_items`` 台账 + ``knowledge_documents`` 幂等键做差集，
   按滑动窗口（在途上限）分批上传，失败按次数重试，支持断点续跑。

关闭时（``knowledge.source_sync.enabled=false``）启动流程零副作用：
不会连数据库、不会建目录、不会上传任何文件。

【建议阅读顺序】
1. ``run_source_sync``       —— 总入口：检查配置、组装依赖、调用引擎；
2. ``SourceSyncEngine.run``  —— 主流程：算时间窗口 → 拉数据 → 逐条处理；
3. ``prepare_staging`` / ``stage_rows`` —— 批量暂存：目录就绪 → 拷贝源文件；
4. ``_upload_row``           —— 单条记录如何变成知识库里的文档；
5. ``refresh_inflight``      —— 在途文档的状态回收与重试（READY 后删暂存副本）。
"""

from __future__ import annotations

# ---- 标准库（Python 自带，不需要额外安装）----
import asyncio  # 异步编程基础：事件循环、协程、锁、sleep
import logging  # 打日志
from datetime import datetime, timedelta  # 时间处理：当前时间、时间点、时间差

# run_in_threadpool：把一个“会阻塞的普通函数”丢到线程池里执行。
from starlette.concurrency import run_in_threadpool

# ---- 本项目内部的模块 ----
from easy_agent.knowledges.core.config import KnowledgeConfig
from easy_agent.knowledges.core.models import KnowledgePrincipal
from easy_agent.knowledges.core.original_store import LocalOriginalStore  # 本地原文存储：保存上传过的文件原件
from easy_agent.knowledges.core.repository import KnowledgeRepository  # 仓储层：所有数据库增删改查都封装在这里
from easy_agent.knowledges.runtime.source_sync_engine import SourceSyncEngine
from easy_agent.knowledges.runtime.source_sync_support import (
    INFLIGHT_POLL_INTERVAL_SECONDS,
    MAX_UPLOAD_ATTEMPTS,
    SOURCE_SYNC_SOURCE,
    SourceRow,
    _iter_windows,
    build_source_query,
    build_staged_name,
    build_upload_name,
    cleanup_stale_staging_files,
    copy_to_staging,
    date_folder_segments,
    describe_missing_source,
    ensure_staging_dir,
    fetch_source_rows,
    parse_publish_date,
    parse_source_row,
    remove_file_if_exists,
    resolve_batchid_file,
    resolve_staging_dir,
    resolve_table_name,
    sanitize_title,
)
from easy_agent.knowledges.service import KnowledgeService  # 知识库业务服务：上传文档、建目录、重试解析等

logger = logging.getLogger(__name__)  # 本模块的日志器，日志会带模块名，方便排查问题

# 进程内“排队锁”：同一时刻只允许一个同步任务在跑。
# 场景：启动钩子自动同步还没结束时，运维又手动触发了一次同步，
# 两路同时上传同一条记录会浪费带宽，甚至重复建文档，这里让它们排队。
# 注意：asyncio.Lock 只在“同一个进程的同一个事件循环”里有效，多进程部署管不到。
_RUN_LOCK = asyncio.Lock()



# ---------------------------------------------------------------------------
# 生命周期入口
# ---------------------------------------------------------------------------
async def run_source_sync(
    app,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    wait_for_inflight: bool = True,
) -> dict[str, int]:
    """执行一次文档同步（启动钩子 / CLI / 测试共用）。

    这是本模块对外最重要的入口，负责“组装零件”：
        配置 → 数据库 → RAGFlow 客户端 → 归属用户 → 仓储/服务 → 引擎 → 开跑

    任何依赖缺失都会直接抛 RuntimeError（配置错误应当尽早暴露），
    但同步开关关闭时会直接返回空字典（零副作用）。
    """
    config: KnowledgeConfig | None = getattr(app.state, "knowledge_config", None)
    if config is None:
        raise RuntimeError("知识库配置未加载，无法执行文档同步")
    sync_config = config.source_sync
    if not sync_config.enabled:
        # 开关关闭：什么都不做，直接返回（启动钩子也因此不会产生任何副作用）
        logger.info("研报 SQL 同步未启用（knowledge.source_sync.enabled=false），跳过")
        return {}
    if not config.enabled:
        # 同步开关开着，但知识库模块没开：这是矛盾配置，直接报错
        raise RuntimeError("知识库模块未启用，无法执行文档同步")
    # 从 app.state 里取启动时初始化好的数据库连接和 RAGFlow 客户端
    db = getattr(app.state, "db", None)
    ragflow = getattr(app.state, "ragflow_client", None)
    if db is None or ragflow is None:
        raise RuntimeError("数据库或 RAGFlow 客户端未就绪，无法执行文档同步")

    # 同步操作需要一个“归属用户”：找配置里指定的用户名对应的用户记录
    owner = await run_in_threadpool(db.get_user_by_username, sync_config.owner_username)
    if owner is None:
        raise RuntimeError(
            f"同步归属用户不存在: {sync_config.owner_username!r}"
            "（检查 source_sync.owner_username）"
        )
    # 组装操作者身份，后续上传/建目录都会以这个身份做权限校验
    principal = KnowledgePrincipal(
        user_id=str(owner.user_id),
        username=str(owner.username),
        department_id=(str(owner.department_id) or None) if owner.department_id else None,
    )
    # 原文存储：优先用应用里初始化好的，没有就新建一个本地存储兜底
    original_store = getattr(app.state, "knowledge_original_store", None)
    repository = KnowledgeRepository(db)
    service = KnowledgeService(
        repository,
        ragflow,
        config,
        original_store=original_store or LocalOriginalStore(),
    )
    engine = SourceSyncEngine(
        config=config,
        sync_config=sync_config,
        repository=repository,
        service=service,
        principal=principal,
    )
    # 加锁：如果已有同步在跑，这里会排队等待，避免两路同时上传
    async with _RUN_LOCK:
        return await engine.run(
            limit=limit,
            dry_run=dry_run,
            wait_for_inflight=wait_for_inflight,
        )


async def startup_source_sync(app) -> None:
    """启动钩子：开关开启时调度后台同步任务（失败不阻断启动）。

    Web 框架启动时调用。这里只负责“创建后台任务”，不会等同步跑完，
    所以不会拖慢服务启动；任务句柄挂在 app.state.source_sync_task 上，
    方便关闭服务时取消它。
    """
    config = getattr(app.state, "knowledge_config", None)
    sync_config = getattr(config, "source_sync", None) if config is not None else None
    if sync_config is None or not sync_config.enabled:
        # 没加载配置或开关没开：静默返回，不启动任何任务
        return
    # 启动前先检查必填项，缺一个就不启动（免得后台任务反复报错刷屏）
    if (
        not sync_config.mysql.host.strip()
        or not sync_config.mysql.database.strip()
        or not sync_config.mysql.table.strip()
    ):
        logger.error(
            "研报 SQL 配置不完整：mysql.host / mysql.database / mysql.table 均为必填，跳过同步"
        )
        return
    if not sync_config.file_root.strip():
        logger.error(
            "研报 SQL 配置不完整：source_sync.file_root 必填（按 batchid 匹配磁盘文件），跳过同步"
        )
        return
    try:
        # 暂存目录可选；配了就校验绝对路径 + 不能与源目录相同
        resolve_staging_dir(sync_config)
    except (RuntimeError, OSError) as exc:
        logger.error("研报 SQL 配置不完整：%s，跳过同步", exc)
        return
    # create_task：把协程丢到事件循环后台执行，立即返回不阻塞
    task = asyncio.create_task(_sync_loop(app), name="source-doc-sync")
    app.state.source_sync_task = task  # 存下句柄，关闭时才能取消
    logger.info("研报 SQL 同步任务已调度（后台执行）")


def _seconds_until_start_time(start_time: str) -> float:
    """距下一次 ``HH:MM:SS``（服务器本地时间）还有多少秒；已过则顺延到次日。"""

    target_time = datetime.strptime(start_time, "%H:%M:%S").time()
    now = datetime.now()
    target = datetime.combine(now.date(), target_time)
    if target <= now:
        target += timedelta(days=1)
    return max(0.0, (target - now).total_seconds())


async def _sync_loop(app) -> None:
    """后台循环：按固定间隔触发同步，不等待上一轮解析完成。

    说明：
    - 触发时刻固定在「首次执行时刻 + k * interval_minutes」上，与上一轮跑了多久无关；
      某轮执行超过间隔时跳过错过的触发点，不补跑；
    - 每轮只负责扫描＋暂存＋提交上传，在途文档的解析进度由下一轮开头的
      refresh_inflight 回收，避免“等全部解析完再计时”；
    - start_time 非空时先等到当天的该时刻（已过则顺延到次日）再第一次执行，
      仅首次生效；留空 = 启动后立即执行第一次；
    - interval_minutes <= 0 表示“只跑一次”，此时保留等解析结束的收尾行为；
    - CancelledError 必须原样抛出：吞掉它会导致任务无法被正常取消，
      关闭服务时可能卡住。
    """
    sync_config = app.state.knowledge_config.source_sync
    start_time = str(getattr(sync_config, "start_time", "") or "").strip()
    interval = int(getattr(sync_config, "interval_minutes", 0))
    loop = asyncio.get_running_loop()
    try:
        if start_time:
            delay = _seconds_until_start_time(start_time)
            logger.info(
                "研报 SQL 同步将在 %s（约 %.1f 分钟后）首次执行",
                start_time,
                delay / 60,
            )
            await asyncio.sleep(delay)
        if interval <= 0:
            # 只跑一次：等全部在途文档解析结束再退出（命令行/补数据场景）
            try:
                await run_source_sync(app)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.error("研报 SQL 同步失败: %s", exc)
            return
        next_tick = loop.time()
        while True:
            try:
                # 定时触发只提交下一批，不等在途解析完成；剩余解析由下一轮回收
                await run_source_sync(app, wait_for_inflight=False)
            except asyncio.CancelledError:
                raise  # 取消信号：必须往上抛，不能当成普通错误吞掉
            except Exception as exc:  # noqa: BLE001
                # 单次同步失败只记日志，不退出循环——下一轮还会再试
                logger.error("研报 SQL 同步失败: %s", exc)
            next_tick += interval * 60
            now = loop.time()
            if next_tick <= now:
                # 本轮执行超过一个间隔：对齐到下一个未来触发点，错过的直接跳过
                missed = int((now - next_tick) // (interval * 60)) + 1
                logger.warning(
                    "研报 SQL 同步执行超过触发间隔，跳过 %s 个触发点", missed
                )
                next_tick += missed * interval * 60
            await asyncio.sleep(next_tick - now)
    except asyncio.CancelledError:
        raise


async def shutdown_source_sync(app) -> None:
    """关闭钩子：取消后台同步任务并等待其退出。

    忽略取消异常和普通异常，保证关闭流程不会因此报错。
    """
    task = getattr(app.state, "source_sync_task", None)
    if task is None:
        return  # 没启动过任务，什么都不用做
    task.cancel()  # 给任务发“取消”信号
    try:
        await task  # 等任务真正结束，避免进程退出时留下半截操作
    except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110 - shutdown 取消同步任务，异常无需上抛
        pass


# 本模块对外公开的名字：from xxx import * 或工具做静态检查时会参考这个列表
__all__ = [
    "INFLIGHT_POLL_INTERVAL_SECONDS",
    "MAX_UPLOAD_ATTEMPTS",
    "SOURCE_SYNC_SOURCE",
    "SourceRow",
    "SourceSyncEngine",
    "_iter_windows",
    "build_source_query",
    "build_staged_name",
    "build_upload_name",
    "cleanup_stale_staging_files",
    "copy_to_staging",
    "date_folder_segments",
    "describe_missing_source",
    "ensure_staging_dir",
    "fetch_source_rows",
    "parse_publish_date",
    "parse_source_row",
    "remove_file_if_exists",
    "resolve_batchid_file",
    "resolve_staging_dir",
    "resolve_table_name",
    "run_source_sync",
    "sanitize_title",
    "shutdown_source_sync",
    "startup_source_sync",
]
