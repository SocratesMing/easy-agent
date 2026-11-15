"""文档 SQL 同步的纯函数工具：表名/查询、行解析、命名、暂存与取数。"""

from __future__ import annotations

import logging
import os
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from easy_agent.knowledges.core.config import SourceSyncConfig
from easy_agent.knowledges.core.models import DocumentStatus

logger = logging.getLogger(__name__)

# 台账（knowledge_source_sync_items 表）里 status 字段的取值对照：
#   uploading : 正在把文件上传给知识库
#   parsing   : 已提交，等待知识库解析
#   ready     : 解析完成，可以检索
#   failed    : 失败（超过固定重试次数后不再重试）
#   deleted   : 文档在知识库里被人工删除了，不再重传
#
# 进程重启后，状态仍停在下面两个值，说明“上次没跑完”，需要重新检查、回收
_ACTIVE_ITEM_STATUSES = ("uploading", "parsing")
# 本地文档处于这些状态时无需再次上传：
#   READY      解析完成
#   PENDING    已建本地记录、文件已落盘（但可能还没拿到远端 ID）
#   PROCESSING 知识库正在解析
#   PAUSED     人为暂停——要尊重人的操作，不自动重传
_READY_DOCUMENT_STATUSES = {
    DocumentStatus.READY.value,
    DocumentStatus.PENDING.value,
    DocumentStatus.PROCESSING.value,
    DocumentStatus.PAUSED.value,
}
# 这些状态说明“文档没成功”，可以尝试重试
_RETRYABLE_DOCUMENT_STATUSES = {
    DocumentStatus.FAILED.value,
    DocumentStatus.CANCELLED.value,
    DocumentStatus.UNKNOWN.value,
}
# 暂存副本需要保留的文档状态：解析中/暂停/失败等（READY 已入库，由清理逻辑删除）
_STAGED_KEEP_STATUSES = (
    _READY_DOCUMENT_STATUSES - {DocumentStatus.READY.value}
) | _RETRYABLE_DOCUMENT_STATUSES

# 文件名里不允许出现的字符（Windows/Linux 通用）：\ / : * ? " < > | 以及换行、制表符
_ILLEGAL_FILENAME = re.compile(r'[\\/:*?"<>|\r\n\t]+')
# 支持解析的发布日期格式，代码会按顺序逐个尝试
_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d")
# 标题最长保留 100 个字符（超出的直接截掉）
_MAX_TITLE_CHARS = 100

# 同步来源标识：写入元数据 sql 的 source 字段，用于幂等与区分来源（固定值，不配置）
# 可以理解为给这批文档打个“来源标签”：以后要查“哪些文档是 SQL 同步来的”，就按它过滤。
SOURCE_SYNC_SOURCE = "source_sync"

# 在途文档状态轮询间隔（秒）与单篇失败重试上限：代码内固定，不放进配置文件。
INFLIGHT_POLL_INTERVAL_SECONDS = 15.0
MAX_UPLOAD_ATTEMPTS = 3

# 文档 SQL 表结构固定；表名必须通过 source_sync.mysql.table 配置。
# 为什么要限制字符：SQL 里的“表名”没法用参数占位符传递，只能拼进字符串，
# 所以必须先做白名单校验（只允许字母/数字/下划线），防止表名配置里塞 SQL 注入内容。
_TABLE_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
# 内部字段名 → 数据库真实列名 的映射。
# 数据库列名是驼峰（publishDate），代码里统一用下划线（publish_date），
# 集中在这里做一次转换，其他代码就不用关心数据库的命名风格了。
_SOURCE_COLUMNS = {
    "record_id": "id",
    "publish_date": "publishDate",
    "batch_id": "batchid",
    "report_title": "reportTitle",
    "report_tag": "reportTag",
    "report_second_source": "reportSecondSource",
}
# 查询的列清单（写死，不用 SELECT *，避免表结构变化时把无关的大字段也查出来）
_SOURCE_SELECT = ", ".join(_SOURCE_COLUMNS.values())


def resolve_table_name(sync_config: SourceSyncConfig) -> str:
    """返回文档 SQL 表名；未配置时直接报错。

    为什么要专门校验表名？
    SQL 里的“表名”不能用参数占位符（%(name)s）传递，只能拼字符串，
    所以这里用白名单校验（只允许字母/数字/下划线），防止有人把
    ``; DROP TABLE ...`` 之类的内容配进表名，造成 SQL 注入。
    """
    configured = sync_config.mysql.table.strip()  # 去掉首尾空格，防止手滑多打空格
    if not configured:
        raise RuntimeError(
            "研报 SQL 表名未配置：请设置 knowledge.source_sync.mysql.table（COL_SQL_TABLE）"
        )
    if not _TABLE_NAME_RE.fullmatch(configured):  # fullmatch：要求整个字符串都符合规则
        raise RuntimeError(
            f"source_sync.mysql.table 非法（仅允许字母/数字/下划线）: {configured!r}"
        )
    return configured


def build_source_query(table: str) -> str:
    """按固定列映射拼查询；table 已在 resolve_table_name 校验。

    生成的 SQL 形如::

        SELECT id, publishDate, ... FROM `t_report`
        WHERE publishDate >= %(start)s AND publishDate < %(end)s

    %(start)s / %(end)s 是 pymysql 的“参数占位符”：真正的值在执行时才由
    驱动安全地传进去，既不用手动拼接日期字符串，也能避免 SQL 注入。
    条件用“左闭右开”（>= start 且 < end），保证相邻时间窗口首尾不重复。
    """
    return (
        f"SELECT {_SOURCE_SELECT} FROM `{table}` "
        "WHERE publishDate >= %(start)s AND publishDate < %(end)s"
    )


# ---------------------------------------------------------------------------
# 字段解析与命名
# ---------------------------------------------------------------------------
def _row_value(row: dict[str, Any], column: str, *aliases: str) -> Any:
    """从一行查询结果里取值，支持“主列名 + 备用列名（别名）”。

    MySQL 的列名在不同环境下大小写可能不一致（publishDate / publishdate），
    调用方可以传多个候选名，按顺序返回第一个存在的值；都不存在则返回 None。
    """
    for key in (column, *aliases):
        if key and key in row:
            return row.get(key)
    return None


def parse_publish_date(value: Any) -> str | None:
    """把源表 publishDate 归一化为 YYYY-MM-DD；无法识别返回 None。

    源表里的日期可能长这样（不同系统写入的格式不一样）::

        datetime 对象 / date 对象
        "2026-10-21 10:00:00"
        "2026-10-21"
        "2026/10/21"
        "20261021"
        "2026-10-21T10:00:00"（ISO 格式，带 T）

    这里统一转成 "2026-10-21" 这种“年-月-日”字符串，
    后面的日期目录（2026年/10月/21日）和文档元数据都用它。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        # 数据库直接返回 datetime 对象：格式化即可
        return value.strftime("%Y-%m-%d")
    if hasattr(value, "strftime") and not isinstance(value, str):
        # 数据库返回 date 对象（注意：datetime 是 date 的子类，上面已单独处理）
        return value.strftime("%Y-%m-%d")  # datetime.date
    text = str(value).strip()  # 其他情况统一转成字符串再处理
    if not text:
        return None
    # 逐个尝试已知格式，第一个成功的就返回
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # 兼容 "2026-10-21T10:00:00" / "2026-10-21 10:00:00.000"
    # 即：开头是 年-月-日 的，直接截取前 10 个字符解析
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


@dataclass(frozen=True, slots=True)
class SourceRow:
    """一条研报 SQL 记录解析后的字段，供暂存、上传和 dry-run 共用。"""

    record_id: str
    publish_date: str | None
    batch_id: str
    title: Any
    report_tag: str
    second_source: str


def parse_source_row(row: dict[str, Any]) -> SourceRow:
    """把数据库原始行转成 SourceRow，集中处理字段别名、空值和类型转换。"""
    columns = _SOURCE_COLUMNS
    raw_id = _row_value(row, columns["record_id"])
    return SourceRow(
        record_id=str(raw_id).strip() if raw_id is not None else "",
        publish_date=parse_publish_date(_row_value(row, columns["publish_date"])),
        batch_id=str(_row_value(row, columns["batch_id"]) or "").strip(),
        title=_row_value(row, columns["report_title"]),
        report_tag=str(_row_value(row, columns["report_tag"]) or "").strip(),
        second_source=str(
            _row_value(row, columns["report_second_source"]) or ""
        ).strip(),
    )


def sanitize_title(title: Any) -> str:
    """标题清洗：非法文件名字符替换为 _，压缩空白并截断到 100 字。

    标题会被拼进上传文件名，所以必须先做“消毒”，分四步：
    1. 统一转字符串、去掉首尾空白；
    2. 把文件系统不认的字符（\\ / : * ? " < > | 等）替换成下划线；
    3. 多个连续空白压成一个空格；
    4. 去掉首尾的空格、点、下划线（避免文件名看起来怪怪的），再截断到 100 字。
    """
    text = str(title or "").strip()
    text = _ILLEGAL_FILENAME.sub("_", text)
    text = re.sub(r"\s+", " ", text).strip(" ._")
    return text[:_MAX_TITLE_CHARS]


def build_upload_name(title: Any, record_id: str, file_path: str) -> str:
    """上传名：``{report_title}_{id}{原扩展名}``；标题为空时 ``{id}{扩展名}``。

    例：标题“2026年新能源行业展望”、id “123”、文件 “xxx.pdf”
        → “2026年新能源行业展望_123.pdf”

    文件名总字节数受知识库 ``max_filename_utf8_bytes``（默认 127）限制，
    中文标题按 UTF-8 字节二次裁剪，避免上传阶段被拒。
    """
    extension = Path(file_path).suffix  # 原文件的扩展名（含点），如 ".pdf"
    cleaned = sanitize_title(title)  # 先清洗标题（去非法字符、截断）
    suffix = f"_{record_id}{extension}"  # 文件名里必须保留的后半段：_id + 扩展名
    # 给标题部分留 120 字节预算（比知识库上限 127 略小，留点余量）。
    # 注意：一个汉字在 UTF-8 里占 3 个字节，所以必须按“字节”裁剪而不是按“字符数”裁剪。
    budget = 120 - len(suffix.encode("utf-8"))
    if budget <= 0:
        # 光 “_id + 扩展名” 就超预算了，标题只能整个丢掉
        cleaned = ""
    else:
        # 先按字节截断，再用 errors="ignore" 解码：
        # 万一截断点刚好把一个汉字“劈成两半”，就忽略残字节，避免出现乱码
        encoded = cleaned.encode("utf-8")[:budget]
        cleaned = encoded.decode("utf-8", errors="ignore")
    stem = f"{cleaned}_{record_id}" if cleaned else str(record_id)
    return f"{stem}{extension}"


def resolve_batchid_file(
    sync_config: SourceSyncConfig, batch_id: Any
) -> Path | None:
    """按文档 SQL 的 ``batchid`` 匹配 ``file_root`` 下的磁盘文件。

    文件名包含 batchid 即可（忽略大小写），兼容 ``xxx_{batchid}.pdf``
    这类带前缀/后缀的命名；文件名完全等于 batchid 的优先命中。

    举例（batchid = "abc123"）::

        file_root/abc123.pdf             ← 命中（去掉扩展名后完全相等，优先级最高）
        file_root/研报_abc123_终稿.pdf    ← 命中（文件名里“包含”abc123，次优先）
        file_root/其他文件.pdf            ← 不命中

    找不到时返回 None。
    """
    raw = str(batch_id or "").strip()
    root = sync_config.file_root.strip()
    if not raw or not root:
        return None
    base = Path(root)
    if not base.is_absolute():
        raise RuntimeError("source_sync.file_root 必须是绝对路径")

    target = raw.casefold()  # casefold() 是更“彻底”的小写化，用于忽略大小写比较
    contains_hit: Path | None = None  # 暂存“文件名包含 batchid”的第一个命中
    for entry in base.iterdir():  # 遍历 file_root 目录下的每一个条目
        if not entry.is_file():
            continue  # 只关心文件，跳过子目录
        name = entry.name.casefold()
        # 第一优先级：文件名完全等于 batchid（带扩展名），或去掉扩展名后等于 batchid
        if name == target or Path(name).stem == target:
            return entry
        if contains_hit is None and target in name:
            # 第二优先级：文件名里“包含” batchid，如 “研报_abc123_终稿.pdf”
            # 只记住第一个命中的，保证多次运行结果稳定（不会忽左忽右）
            contains_hit = entry
    return contains_hit  # 可能为 None：表示磁盘上没有对应文件


# ---------------------------------------------------------------------------
# 暂存目录：先把要入库的文件拷贝过来，READY 后再删除副本
# ---------------------------------------------------------------------------
_STAGING_TEMP_SUFFIX = ".part"


def resolve_staging_dir(sync_config: SourceSyncConfig) -> Path | None:
    """解析暂存目录：未配置返回 None（保持直接读 file_root 的旧行为）。

    暂存目录必须是绝对路径，且不能与 ``file_root`` 相同——否则 READY 后
    删除暂存副本会把业务原件一起删掉。
    """
    raw = sync_config.staging_dir.strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        raise RuntimeError("source_sync.staging_dir 必须是绝对路径")
    source_root = sync_config.file_root.strip()
    if source_root and path.resolve() == Path(source_root).resolve():
        raise RuntimeError("source_sync.staging_dir 不能与 file_root 相同")
    return path


def ensure_staging_dir(staging_dir: Path) -> None:
    """目录存在就直接用，不存在就创建（含多级父目录）。"""
    staging_dir.mkdir(parents=True, exist_ok=True)


def build_staged_name(title: Any, record_id: str, source_file: str | Path) -> str:
    """暂存文件名，与上传名保持一致：``{report_title}_{id}{扩展名}``。

    为什么必须和上传名一致？因为文档解析为 READY 后，``refresh_inflight``
    手里只有 ``knowledge_documents.name``（上传名），要靠它反推暂存路径。
    ``Path(...).name`` 只取最后一段，防止异常 record_id 带出目录穿越。
    """
    return Path(build_upload_name(title, record_id, str(source_file))).name


def copy_to_staging(source_file: Path, staged_file: Path) -> bool:
    """把源文件原子拷贝到暂存路径；同名副本已存在时直接覆盖。

    先写 ``xxx.part`` 再 ``os.replace``，避免进程中断留下半截文件；
    同名副本一律覆盖，保证暂存内容始终是源文件的最新版本。
    返回 True=已写入，False=源文件与目标路径相同（无需拷贝）。
    """
    if source_file.resolve() == staged_file.resolve():
        return False
    staged_file.parent.mkdir(parents=True, exist_ok=True)
    # 带 PID 的临时名：多进程部署时避免两个进程抢同一个 .part 文件
    temp_file = staged_file.with_name(
        f"{staged_file.name}.{os.getpid()}{_STAGING_TEMP_SUFFIX}"
    )
    try:
        # 用 copyfile 而不是 copy2：不保留源文件的旧 mtime，
        # 否则新拷来的暂存文件会被 TTL 清理误判为“超期残留”。
        shutil.copyfile(source_file, temp_file)
        os.replace(temp_file, staged_file)
    finally:
        temp_file.unlink(missing_ok=True)
    return True


def remove_file_if_exists(path: Path) -> bool:
    """删除普通文件；文件本来就不存在时返回 False。"""
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def cleanup_stale_staging_files(
    staging_dir: Path,
    retention_hours: int,
    *,
    keep_names: set[str] | None = None,
) -> int:
    """删除暂存目录中超过保留时长的**孤儿**文件，返回删除数量。

    主要兜底这些残留：进程在上传/解析中途退出、删除时遇到权限问题、
    或 ``.part`` 临时文件没来得及替换。``keep_names`` 里的文件会跳过，
    用于保留失败/解析中文档对应的暂存副本（失败文件由用户要求保留）。

    只处理普通文件，不递归目录。
    """
    cutoff = datetime.now(UTC) - timedelta(hours=retention_hours)
    keep_names = keep_names or set()
    removed = 0
    for entry in staging_dir.iterdir():
        if not entry.is_file() or entry.name in keep_names:
            continue
        try:
            modified_at = datetime.fromtimestamp(entry.stat().st_mtime, tz=UTC)
            if modified_at < cutoff:
                entry.unlink(missing_ok=True)
                removed += 1
        except OSError:
            logger.warning("暂存残留清理失败 | path=%s", entry, exc_info=True)
    if removed:
        logger.info("暂存残留清理完成 | dir=%s removed=%s", staging_dir, removed)
    return removed


def describe_missing_source(sync_config: SourceSyncConfig, batch_id: str) -> str:
    """生成“找不到源文件”的统一原因，区分未配置目录和 batchid 未命中。"""
    if not sync_config.file_root.strip():
        return "未配置 source_sync.file_root，无法按 batchid 匹配磁盘文件"
    return f"batchid 未匹配到磁盘文件: {batch_id or '-'}"


def date_folder_segments(publish_date: str) -> list[str]:
    """日期目录段：``2026-10-23`` → ``["2026年", "10月", "23日"]``。

    知识库里的目录是“年/月/日”三层结构，
    2026-10-23 的文档会归档到 2026年 > 10月 > 23日 目录下。
    月/日统一补零（"03" → "03月"），让目录名按字典序排列时
    1 月 ~ 12 月、1 日 ~ 31 日仍是自然顺序。
    """
    # "2026-10-23".split("-") → ["2026", "10", "23"]
    year, month, day = publish_date.split("-")
    return [f"{year}年", f"{int(month):02d}月", f"{int(day):02d}日"]


def _iter_windows(
    start: datetime, end: datetime, days: int
) -> Iterable[tuple[datetime, datetime]]:
    """按 days 切分 [start, end) 窗口，避免一次拉取过大数据量。

    例：start=10-01, end=10-20, days=7 →
    ``(10-01, 10-08), (10-08, 10-15), (10-15, 10-20)``

    每个窗口单独执行一次 SQL 查询，最后一段可能不足 days 天。
    区间是“左闭右开”：包含 start、不包含 end，避免相邻窗口重复取到同一条记录。
    """
    cursor = start  # 当前窗口的起点，会不断往后挪
    while cursor < end:
        boundary = min(cursor + timedelta(days=days), end)  # 窗口终点，最多到 end
        yield cursor, boundary  # yield：生成器写法，调用方可以一边取一边处理
        cursor = boundary  # 下一段从这一段终点开始


# ---------------------------------------------------------------------------
# 外部 SQL
# ---------------------------------------------------------------------------
def _connect_source(sync_config: SourceSyncConfig) -> Any:
    """创建到“文档 SQL”（MySQL）的连接。这是同步阻塞函数，调用方需放线程池。"""
    mysql = sync_config.mysql
    host = mysql.host.strip()
    database = mysql.database.strip()
    if not host or not database:
        raise RuntimeError(
            "研报 SQL 配置不完整：mysql.host / mysql.database / mysql.table 均为必填"
        )

    return pymysql.connect(
        host=host,  # 数据库地址
        port=mysql.port,  # 端口，默认 3306
        user=mysql.user,  # 用户名
        password=mysql.password.get_secret_value(),  # 密码。配置里用 SecretStr 包裹，取值要显式调用
        database=database,  # 库名
        charset=mysql.charset,  # 字符集（通常 utf8mb4，能存中文和 emoji）
        cursorclass=DictCursor,  # 让查询结果返回 dict（列名→值），而不是默认的元组
        connect_timeout=10,  # 连接超时：10 秒连不上就报错，避免一直挂着
        read_timeout=60,  # 读取超时：单次查询最多等 60 秒，防止慢查询拖死同步
    )


def fetch_source_rows(
    sync_config: SourceSyncConfig, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """执行固定查询，返回窗口内的源记录（同步阻塞，调用方放线程池）。

    参数：
        sync_config: 同步配置（含 MySQL 连接信息与表名）
        start:       查询起始时间（包含）
        end:         查询结束时间（不包含）
    返回：
        行列表，每行是一个 dict，键是数据库的列名。

    为什么叫“同步阻塞”？因为这个函数内部直接调用 pymysql，
    调用期间当前线程会被“卡住”等数据库返回。所以调用方要把它放进
    线程池（run_in_threadpool）执行，不能直接 await 它。

    表名必须配置。
    连接/查询失败只记录「研报 SQL」错误日志并抛出，不做连接参数兜底。
    """
    table = resolve_table_name(sync_config)  # 先校验表名，不合法就没必要连库了
    mysql = sync_config.mysql
    try:
        connection = _connect_source(sync_config)
    except Exception as exc:  # noqa: BLE001
        # 连不上：把关键连接信息（不含密码）打进日志，方便排查网络/账号问题
        logger.error(
            "研报 SQL 连接失败 | host=%s:%s database=%s err=%s",
            mysql.host,
            mysql.port,
            mysql.database,
            exc,
        )
        raise  # 继续往上抛，由调用方决定怎么处理
    logger.info(
        "研报 SQL 连接成功 | host=%s:%s database=%s table=%s",
        mysql.host,
        mysql.port,
        mysql.database,
        table,
    )
    try:
        # with 语句：离开代码块时自动关闭游标，不用手动 cursor.close()
        with connection.cursor() as cursor:
            cursor.execute(
                build_source_query(table),
                {
                    # 占位符 %(start)s / %(end)s 的值在这里传进去
                    "start": start.strftime("%Y-%m-%d %H:%M:%S"),
                    "end": end.strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            # fetchall() 一次性取出全部结果；再逐行转成普通 dict
            rows = [dict(row) for row in cursor.fetchall()]
        logger.info(
            "研报 SQL 查询成功 | table=%s window=%s~%s rows=%s",
            table,
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
            len(rows),
        )
        return rows
    except Exception as exc:  # noqa: BLE001
        logger.error("研报 SQL 查询失败 | table=%s err=%s", table, exc)
        raise
    finally:
        # 不管成功还是失败，最后的 finally 一定会执行：关闭连接，避免连接泄漏
        connection.close()


# ---------------------------------------------------------------------------
# 同步引擎
# ---------------------------------------------------------------------------
