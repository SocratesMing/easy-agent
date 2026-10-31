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
import mimetypes  # 根据文件后缀猜 Content-Type（如 .pdf → application/pdf）
import os  # 原子替换暂存文件（os.replace）
import re  # 正则表达式：清洗标题、校验表名、识别日期
import shutil  # 拷贝源文件到暂存目录
from collections.abc import Iterable  # 类型注解：可迭代对象（能用于 for 循环的东西）
from dataclasses import dataclass  # 轻量数据类：把一行 SQL 记录解析成明确字段
from datetime import UTC, datetime, timedelta  # 时间处理：当前时间、时间点、时间差
from pathlib import Path  # 面向对象的路径操作：取后缀、判断绝对路径、遍历目录
from typing import Any  # 类型注解：表示“任意类型”

# pymysql 是连接 MySQL 的第三方库。这里用 try/except 做“软依赖”处理：
# 没安装 pymysql 时，本模块依然能正常 import（不跑同步的环境不受影响），
# 只有真正要连接“文档 SQL”时，才会在 _connect_source 里抛出明确的错误。
try:
    import pymysql
    from pymysql.cursors import DictCursor  # DictCursor：查询结果按“列名→值”的字典返回
except ImportError:  # pragma: no cover - 依赖缺失时由运行期报错兜底
    pymysql = None  # type: ignore[assignment]
    DictCursor = None  # type: ignore[assignment]

# run_in_threadpool：把一个“会阻塞的普通函数”丢到线程池里执行。
# 原因：pymysql 查数据库、读本地文件都是阻塞操作，直接在 async 函数里调用
# 会把整个事件循环卡住（期间其他请求全部被拖慢）；放线程池既不卡事件循环，
# 也不用为 pymysql 单独写异步版本。
from starlette.concurrency import run_in_threadpool

# ---- 本项目内部的模块 ----
from easy_agent.knowledges.core.config import KnowledgeConfig, SourceSyncConfig
from easy_agent.knowledges.core.models import (
    DocumentMetadata,  # 文档的业务元数据（标题、批次号、发布日期……）
    DocumentStatus,  # 文档状态枚举：PENDING / PROCESSING / READY / FAILED / DELETED ...
    KnowledgeBaseStatus,  # 知识库状态枚举：ACTIVE / DELETING / DELETED ...
    KnowledgeBaseVisibility,  # 知识库可见性枚举：TEAM（团队库）/ PERSONAL（个人库）
    KnowledgePrincipal,  # 操作者身份（用户 ID、用户名、部门），相当于“以谁的名义操作”
)
from easy_agent.knowledges.core.original_store import LocalOriginalStore  # 本地原文存储：保存上传过的文件原件
from easy_agent.knowledges.core.repository import KnowledgeRepository  # 仓储层：所有数据库增删改查都封装在这里
from easy_agent.knowledges.service import KnowledgeService  # 知识库业务服务：上传文档、建目录、重试解析等

logger = logging.getLogger(__name__)  # 本模块的日志器，日志会带模块名，方便排查问题

# 进程内“排队锁”：同一时刻只允许一个同步任务在跑。
# 场景：启动钩子自动同步还没结束时，运维又手动触发了一次同步，
# 两路同时上传同一条记录会浪费带宽，甚至重复建文档，这里让它们排队。
# 注意：asyncio.Lock 只在“同一个进程的同一个事件循环”里有效，多进程部署管不到。
_RUN_LOCK = asyncio.Lock()

# 台账（knowledge_source_sync_items 表）里 status 字段的取值对照：
#   uploading : 正在把文件上传给知识库
#   parsing   : 已提交，等待知识库解析
#   ready     : 解析完成，可以检索
#   failed    : 失败（会根据 max_attempts 决定是否继续重试）
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
_SOURCE_SELECT = "id, publishDate, batchid, reportTitle, reportTag, reportSecondSource"


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
    """把源文件原子拷贝到暂存路径，返回 True=实际拷贝，False=复用已有副本。

    先写 ``xxx.part`` 再 ``os.replace``，避免进程中断留下半截文件；
    目标已存在、大小一致且不比源文件旧时直接复用，避免每轮重复拷贝大文件。
    """
    if source_file.resolve() == staged_file.resolve():
        return False
    if staged_file.exists():
        source_stat = source_file.stat()
        staged_stat = staged_file.stat()
        # 大小一致且暂存副本不比源文件旧：视为同一版本，直接复用；
        # 源文件在上次暂存后又被更新（mtime 更新）时重新拷贝。
        if (
            staged_stat.st_size == source_stat.st_size
            and staged_stat.st_mtime >= source_stat.st_mtime
        ):
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
    月份/日期前面的 0 会被去掉（"03" → "3"）。
    """
    # "2026-10-23".split("-") → ["2026", "10", "23"]
    year, month, day = publish_date.split("-")
    # int(...) 是为了去掉前导零："10" 还是 "10"，"03" 会变成 "3"
    return [f"{year}年", f"{int(month)}月", f"{int(day)}日"]


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
    if pymysql is None:
        raise RuntimeError("未安装 pymysql，无法连接研报 SQL")

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
class SourceSyncEngine:
    """单次同步执行器：解析、差集、滑动窗口上传、状态回收。

    【async 小课堂】def 前面加了 async 的函数叫“协程”：
    - 调用它不会立刻执行，只会返回一个“待执行的任务”；
    - 必须被 await（或交给事件循环）才会真正运行；
    - 执行到 await 时可以“让出” CPU 去干别的事，所以适合大量网络 IO。

    本类中的三种典型调用：
    - await self._repo(...)        → 数据库操作（丢到线程池，不卡事件循环）；
    - await self.service.xxx(...)  → 调用知识库服务（异步 HTTP 请求）；
    - await self._update_item(...) → 更新本地台账状态。
    """

    def __init__(
        self,
        *,
        config: KnowledgeConfig,
        sync_config: SourceSyncConfig,
        repository: KnowledgeRepository,
        service: KnowledgeService,
        principal: KnowledgePrincipal,
    ):
        self.config = config  # 知识库总配置（embedding 模型等）
        self.sync_config = sync_config  # 同步专属配置（MySQL、目录、并发上限等）
        self.repository = repository  # 数据库仓储：台账、文档、知识库、目录的增删改查
        self.service = service  # 知识库服务：真正执行上传/建目录/重试解析
        self.principal = principal  # 以哪个用户身份操作（权限校验、审计都会用到）
        self.source = SOURCE_SYNC_SOURCE  # 来源标识，固定为 "source_sync"
        # 暂存目录：None 表示未配置（直接读源文件）；run() 开始时会解析并创建
        self._staging_dir: Path | None = None

    async def _repo(self, method, *args, **kwargs):
        """同步仓储调用放入线程池执行，避免阻塞事件循环。

        self.repository 里的方法都是普通同步函数（内部走数据库驱动），
        直接调用会卡住 async 事件循环；run_in_threadpool 会把它们放到
        线程池的线程里执行，await 等结果，期间事件循环可以继续干别的。
        """

        return await run_in_threadpool(method, *args, **kwargs)

    async def _update_item(self, item_id: str, **fields: object) -> None:
        """更新同步台账行（统一走线程池）。

        item_id: 台账行的 ID
        **fields: 要更新的列，例如 status="failed"、attempts=2、last_error="..."
        """

        await self._repo(self.repository.update_sync_item, item_id, **fields)

    async def _fail_item(
        self,
        item_id: str,
        *,
        record_id: str,
        reason: str,
        attempts: int,
        document_id: str | None = None,
    ) -> None:
        """把台账置为 failed，统一记录原因/次数，避免各处重复拼字段。"""
        fields: dict[str, object] = {
            "status": "failed",
            "attempts": attempts + 1,
            "last_error": str(reason)[:1000],
        }
        if document_id is not None:
            fields["document_id"] = document_id
        await self._update_item(item_id, **fields)
        logger.warning("文档同步失败 | id=%s reason=%s", record_id, reason)

    # ---------------- 知识库与目录 ----------------

    async def resolve_base_id(self) -> str:
        """确定入库目标库：``base_id`` 优先；为空时按 ``base_name`` 查找/新建。

        名字在 ``knowledge_bases`` 内不唯一（同名多库时按名称会取最早创建的
        那个），需要精确指定时填 ``base_id``；新建库固定为团队公共库。

        三种情况：
        1. 配置了 base_id → 必须存在且未删除，直接用它；
        2. 没配 base_id，但按 base_name 找到了同名库 → 用这个库；
        3. 都没找到 → 自动新建一个“团队公共库”，并把状态置为 ACTIVE。
        """
        sync_config = self.sync_config
        if sync_config.base_id.strip():  # 情况 1：明确指定了库 ID
            base = await self._repo(
                self.repository.get_base, sync_config.base_id.strip()
            )
            if base is None or str(base.get("status")) in {"deleted", "deleting"}:
                # 指定的库不存在、已删除或正在删除：直接报错，而不是悄悄换一个库
                raise RuntimeError(
                    f"source_sync.base_id 不存在或已删除: {sync_config.base_id}"
                )
            return str(base["id"])

        # 情况 2：按名称查找
        base = await self._repo(
            self.repository.find_base_by_name, sync_config.base_name
        )
        if base is not None:
            return str(base["id"])
        # 情况 3：没有就新建一个团队公共库
        created = await self._repo(
            self.repository.create_base,
            name=sync_config.base_name,
            description="由文档 SQL 同步自动创建",
            # 研报接入固定入公共库（team），不提供个人库配置
            space_type=KnowledgeBaseVisibility.TEAM.value,
            owner_user_id=self.principal.user_id,
            department_id=self.principal.department_id,
            embedding_model=self.config.embedding.model,
        )
        # 新建的库默认不是 ACTIVE，这里显式激活，后面才能往里传文档
        await self._repo(
            self.repository.update_base,
            created["id"],
            status=KnowledgeBaseStatus.ACTIVE.value,
        )
        logger.info("文档同步已创建知识库 | id=%s name=%s", created["id"], created["name"])
        return str(created["id"])

    async def ensure_date_folder(self, base_id: str, publish_date: str) -> str | None:
        """确保 ``YYYY年/M月/D日`` 三级目录存在，返回日目录 ID。

        ensure_folders 收到的是一整条路径（如 "2026年/10月/23日"），
        它会“缺哪层建哪层”。但它不返回最底层目录的 ID，所以这里创建完
        再逐层往下查一遍，拿到“日”目录的 ID——上传文档时要靠它指定放哪里。
        """
        year, month, day = date_folder_segments(publish_date)
        await self.service.ensure_folders(
            base_id=base_id,
            folder_id=None,  # None 表示从知识库根目录开始
            paths=["/".join([year, month, day])],  # 一次性传入完整路径
            principal=self.principal,
        )
        # ensure_folders 只保证目录存在，这里再定位叶子（日）目录 ID
        parent: str | None = None  # None 表示从这里开始是根层级
        for segment in (year, month, day):
            folder = await self._repo(
                self.repository.find_folder_by_name, base_id, segment, parent
            )
            if folder is None:
                # 理论上不会发生（刚 ensure 过），真发生说明目录被并发删了，直接报错
                raise RuntimeError(
                    f"日期目录创建后未找到: {base_id}/{year}/{month}/{day}"
                )
            parent = str(folder["id"])  # 下一层在这个目录里面找
        return parent  # 循环结束后，parent 就是“日”目录的 ID

    # ---------------- 批量暂存 ----------------

    async def prepare_staging(self) -> None:
        """解析暂存配置：目录存在就直接用，不存在就创建。"""
        staging_dir = resolve_staging_dir(self.sync_config)
        if staging_dir is None:
            self._staging_dir = None
            return
        try:
            await run_in_threadpool(ensure_staging_dir, staging_dir)
        except OSError as exc:
            raise RuntimeError(
                f"暂存目录不可用: source_sync.staging_dir={staging_dir}: {exc}"
            ) from exc
        self._staging_dir = staging_dir
        logger.info("文档同步暂存目录就绪 | dir=%s", staging_dir)

    async def _staged_keep_names(self, base_id: str) -> set[str]:
        """收集失败/解析中文档的暂存文件名，TTL 清理时跳过它们。

        用户确认“上传/解析失败保留暂存文件”，所以按文档名做白名单；
        READY/DELETED 的文件不保留，删除失败时由下一轮清理补删。
        """
        page_size = max(1, int(self.config.limits.max_documents_per_query))
        offset = 0
        names: set[str] = set()
        while True:
            documents = await self._repo(
                self.repository.list_documents,
                base_id,
                limit=page_size,
                offset=offset,
            )
            if not documents:
                break
            for document in documents:
                name = str(document.get("name") or "")
                if (
                    name
                    and str(document.get("status") or "") in _STAGED_KEEP_STATUSES
                ):
                    names.add(Path(name).name)
            if len(documents) < page_size:
                break
            offset += page_size
        return names

    async def stage_rows(
        self, rows: list[SourceRow]
    ) -> tuple[dict[str, Path], dict[str, str], dict[str, int]]:
        """把一个窗口内待入库的文件批量拷贝到暂存目录。

        返回值：
        - staged：record_id → 暂存文件路径；
        - errors：record_id → 暂存失败原因（上传前就会写进台账）；
        - stats：{"copied": n, "reused": n, "copy_failed": n}。

        只有去重通过的新记录才会走到这里，不会重复拷贝已入库文件。
        """
        stage_stats = {"copied": 0, "reused": 0, "copy_failed": 0}
        if self._staging_dir is None:
            return {}, {}, stage_stats

        staged: dict[str, Path] = {}
        errors: dict[str, str] = {}
        for row in rows:
            if not row.record_id:
                continue
            # 文件定位与上传阶段保持一致：只用 batchid 在 file_root 下匹配。
            source_file = await run_in_threadpool(
                resolve_batchid_file, self.sync_config, row.batch_id
            )
            if source_file is None:
                # 没找到源文件时不拷贝，但仍把原因带回去，让台账记失败。
                errors[row.record_id] = describe_missing_source(
                    self.sync_config, row.batch_id
                )
                continue
            staged_file = self._staging_dir / build_staged_name(
                row.title, row.record_id, source_file
            )
            try:
                copied = await run_in_threadpool(
                    copy_to_staging, source_file, staged_file
                )
            except Exception as exc:  # noqa: BLE001 — 单条失败不阻断整批
                reason = f"暂存拷贝失败: {exc}"
                errors[row.record_id] = reason[:1000]
                stage_stats["copy_failed"] += 1
                logger.error(
                    "文档暂存拷贝失败 | id=%s src=%s dst=%s err=%s",
                    row.record_id,
                    source_file,
                    staged_file,
                    exc,
                )
                continue
            staged[row.record_id] = staged_file
            if copied:
                stage_stats["copied"] += 1
                logger.info(
                    "文档已暂存 | id=%s src=%s dst=%s",
                    row.record_id,
                    source_file,
                    staged_file,
                )
            else:
                stage_stats["reused"] += 1
                logger.info(
                    "文档暂存复用 | id=%s dst=%s", row.record_id, staged_file
                )
        return staged, errors, stage_stats

    async def _delete_staged_file(self, path: Path) -> str:
        """删除暂存文件；返回 deleted / missing / failed，删除失败不阻断同步。"""
        try:
            deleted = await run_in_threadpool(remove_file_if_exists, path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("暂存文件删除失败 | path=%s err=%s", path, exc)
            return "failed"
        if deleted:
            logger.info("暂存文件已删除 | path=%s", path)
            return "deleted"
        return "missing"

    async def _cleanup_staged_for_document(
        self, document: dict[str, Any], stats: dict[str, int] | None = None
    ) -> None:
        """按文档上传名反推暂存路径并删除（READY / DELETED 时调用）。

        数据库里只存上传名 ``knowledge_documents.name``，暂存文件与它同名，
        因此不需要给台账加 ``staged_path`` 字段，也不用做数据库迁移。
        """
        if self._staging_dir is None:
            return
        name = str(document.get("name") or "")
        if not name:
            return
        path = self._staging_dir / Path(name).name
        result = await self._delete_staged_file(path)
        if stats is None:
            return
        if result == "deleted":
            stats["staged_deleted"] += 1
        elif result == "failed":
            stats["staged_cleanup_failed"] += 1

    # ---------------- 单条入库 ----------------

    async def _upload_row(
        self,
        *,
        base_id: str,
        row: SourceRow,
        staged_file: Path | None = None,
        stage_error: str | None = None,
    ) -> dict[str, Any] | None:
        """处理一条源记录：校验 → 找文件 → 上传 → 写台账。

        参数：
            row: 已经解析好的 SourceRow（字段取值只在 run() 里做一次）。
            staged_file: 批量暂存阶段已经拷贝好的文件；为 None 时回退直接读源文件。
            stage_error: 暂存阶段失败原因；非空时直接记台账失败，不再上传。

        返回值：
        - 成功提交上传：返回“刷新后的台账行”（dict），调用方据此判断是否进入在途列表；
        - 跳过或失败：返回 None（失败原因已经写进台账的 last_error 字段）。
        """
        # ---- 第 1 步：校验主键 ----
        if not row.record_id:
            # 没有主键就无法去重（不知道是不是同一条），只能跳过
            logger.warning("文档同步跳过：源记录缺少 id | row=%s", row)
            return None
        record_id = row.record_id
        publish_date = row.publish_date
        batch_id = row.batch_id
        title = row.title
        report_tag = row.report_tag
        second_source = row.second_source

        # ---- 第 2 步：本地是否已有这条源记录对应的文档？----
        # 兜底：同一源记录已存在本地文档时不再新建，避免触发唯一约束冲突
        existing = await self._repo(
            self.repository.find_document_by_source_record,
            self.source,
            record_id,
            base_id=base_id,
            include_deleted=True,  # 连已删除的也算上，避免“删了又建”的死循环
        )
        if existing is not None:
            # 并发/重跑兜底：批量暂存时可能已经拷贝过，但这次不需要再上传，
            # 顺手删掉暂存副本，避免留下孤儿文件。
            if staged_file is not None:
                await self._delete_staged_file(staged_file)
            logger.info(
                "文档同步跳过：本地已有记录 | id=%s doc=%s status=%s",
                record_id,
                existing["id"],
                existing.get("status"),
            )
            return None

        # ---- 第 3 步：创建（或复用）台账行 ----
        # create_or_get_sync_item 是“有则取、无则建”：同一个来源 + 源记录 ID
        # 只会有一条台账，重复调用拿到的就是同一行，顺便带回之前失败过几次（attempts）。
        item = await self._repo(
            self.repository.create_or_get_sync_item,
            source=self.source,
            source_record_id=record_id,
            publish_date=publish_date,
        )
        item_id = str(item["id"])  # 台账行 ID，之后所有状态更新都靠它
        attempts = int(item.get("attempts") or 0)  # 之前已经失败过几次

        # ---- 第 4 步：发布日期必须能解析出 YYYY-MM-DD ----
        if not publish_date:
            await self._fail_item(
                item_id,
                record_id=record_id,
                reason="publishDate 无法解析",
                attempts=attempts,
            )
            return None
        # ---- 第 5 步：暂存失败直接记台账，不再扫描/上传 ----
        if stage_error:
            await self._fail_item(
                item_id,
                record_id=record_id,
                reason=stage_error,
                attempts=attempts,
            )
            return None
        # ---- 第 6 步：定位本次要上传的文件 ----
        # 优先用批量暂存好的副本；未配置暂存目录时回退直接读 file_root。
        if staged_file is not None:
            source_file = staged_file
            if not source_file.is_file():
                await self._fail_item(
                    item_id,
                    record_id=record_id,
                    reason=f"暂存文件不存在: {source_file}",
                    attempts=attempts,
                )
                return None
        else:
            # 文件定位：只用文档 SQL 的 batchid 在 file_root 下匹配同名文件，
            # batchid 唯一，匹配不到即磁盘没有该文件，不做其它字段兜底。
            source_file = await run_in_threadpool(
                resolve_batchid_file, self.sync_config, batch_id
            )
            if source_file is None:
                await self._fail_item(
                    item_id,
                    record_id=record_id,
                    reason=describe_missing_source(self.sync_config, batch_id),
                    attempts=attempts,
                )
                return None
            if self._staging_dir is not None:
                # run() 会先批量暂存；走到这里说明调用链异常，回退直读但记一笔日志。
                logger.warning(
                    "暂存文件缺失，回退直接读取源文件 | id=%s file=%s",
                    record_id,
                    source_file,
                )

        # ---- 第 7 步：确保日期目录存在，拿到“日”目录 ID ----
        folder_id = await self.ensure_date_folder(base_id, publish_date)
        # ---- 第 8 步：拼上传文件名、猜 Content-Type、组装业务元数据 ----
        # 复用暂存命名函数，保证上传名与暂存文件名完全一致（READY 后靠它反推路径）。
        upload_name = build_staged_name(title, record_id, source_file)
        content_type = (
            mimetypes.guess_type(upload_name)[0] or "application/octet-stream"
        )
        metadata = DocumentMetadata(
            source=self.source,
            source_record_id=record_id,
            batch_id=batch_id,
            report_title=sanitize_title(title),
            report_tag=report_tag,
            report_second_source=second_source,
            publish_date=publish_date,
        )
        # ---- 第 9 步：台账先标记“上传中”，然后真正上传文件 ----
        await self._update_item(item_id, status="uploading")
        try:
            # with open(...) as f：文件用完会自动关闭（即使中途报错也会关）
            with source_file.open("rb") as content:
                accepted = await self.service.upload_document(
                    base_id=base_id,
                    folder_id=folder_id,
                    filename=upload_name,
                    content_type=content_type,
                    size_bytes=source_file.stat().st_size,  # 文件大小（字节）
                    content=content,  # 文件内容（二进制流）
                    principal=self.principal,
                    # request_id 会写进审计日志，方便按“源记录”追溯这次操作
                    request_id=f"source-sync:{record_id}",
                    metadata=metadata,
                )
        except Exception as exc:  # noqa: BLE001 — 单条失败不阻断整批
            # 上传异常不代表“什么都没发生”：可能本地记录/原文已经建好，
            # 只是调用远端失败。这里再查一次，把已有文档 ID 记到台账上，
            # 后续重试就能直接复用本地原文，避免重复建记录。
            document = await self._repo(
                self.repository.find_document_by_source_record,
                self.source,
                record_id,
                base_id=base_id,
                include_deleted=True,
            )
            await self._update_item(
                item_id,
                document_id=str(document["id"]) if document else None,
                status="failed",
                attempts=attempts + 1,
                # 截断到 1000 字符，防止异常信息过长把数据库字段撑爆
                last_error=str(exc)[:1000],
            )
            logger.error(
                "文档同步失败 | id=%s file=%s err=%s", record_id, source_file, exc
            )
            return None

        # ---- 第 10 步：上传成功，台账改为 parsing（等待知识库解析完成）----
        document = accepted.document
        await self._update_item(
            item_id,
            document_id=str(document.id),
            status="parsing",
            publish_date=publish_date,
            last_error=None,  # 清掉历史错误信息
        )
        logger.info(
            "文档同步已提交解析 | id=%s name=%s date=%s doc=%s",
            record_id,
            upload_name,
            publish_date,
            document.id,
        )
        # 重新读一次台账行再返回：期间状态可能已经被别处更新（比如解析秒完成），
        # 返回最新数据，调用方才能正确判断它是否还需要继续跟踪。
        return await self._repo(
            self.repository.get_sync_item, self.source, record_id
        )

    # ---------------- 在途回收 ----------------

    async def refresh_inflight(
        self, items: list[dict[str, Any]], *, base_id: str | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """检查一批“在途”台账行对应的文档状态，做一次状态回收。

        “在途”指台账状态停在 uploading / parsing 的行：可能正在上传、
        正在解析，也可能因为进程重启而“卡住”。本函数逐条查看文档的真实状态，
        再决定下一步：

        ==============================  ==========================================
        文档的真实状态                   本函数做的事
        ==============================  ==========================================
        文档不存在（还没落库）            台账置 failed，下一轮重新上传
        文档 DELETED                     台账置 deleted，尊重人工删除，永不重传
        文档 READY                       台账置 ready，同步完成
        文档 PENDING 且没有远端 ID        上次上传半途中断，台账置 failed 等重试
        文档 FAILED/CANCELLED/UNKNOWN     未超过 max_attempts 就重试，否则置 failed
        其他（解析中/暂停等）              原样留在 remaining，下一轮继续等
        ==============================  ==========================================

        参数：
            items:   待检查的台账行列表
            base_id: 目标知识库 ID（用于判断文档是否属于当前库）
        返回：
            (remaining, stats)
            - remaining：还没结束、需要继续等待/重试的台账行
            - stats：ready / retried / failed / staged_deleted / staged_cleanup_failed
        """
        stats = {
            "ready": 0,
            "retried": 0,
            "failed": 0,
            "staged_deleted": 0,
            "staged_cleanup_failed": 0,
        }
        remaining: list[dict[str, Any]] = []  # 本轮结束后仍需继续跟踪的台账行
        for item in items:
            item_id = str(item["id"])
            record_id = str(item["source_record_id"])
            document_id = str(item.get("document_id") or "")
            # 优先按台账里记录的文档 ID 查；没有就按“来源 + 源记录 ID”反查
            document = (
                await self._repo(self.repository.get_document, document_id)
                if document_id
                else await self._repo(
                    self.repository.find_document_by_source_record,
                    self.source,
                    record_id,
                    base_id=base_id,
                    include_deleted=True,
                )
            )
            if document is not None and base_id and str(document.get("base_id")) != base_id:
                # 台账指向旧库（已删除）的历史文档：按“未入库”处理，重新入当前库
                document = None
            if document is None:
                # 上传过程中进程中断且没有落库：重新排队
                await self._update_item(item_id, status="failed")
                stats["failed"] += 1
                continue  # 本轮不再跟踪这一行，下一轮会重新上传
            status = str(document.get("status") or "")
            if status == DocumentStatus.DELETED.value:
                # 文档在当前库内已被删除：尊重删除动作，不再重传；暂存副本已无用
                await self._update_item(
                    item_id,
                    status="deleted",
                    last_error="文档已被删除，不再重传",
                )
                await self._cleanup_staged_for_document(document, stats)
                stats["failed"] += 1
                continue
            if status == DocumentStatus.READY.value:
                # 解析完成：台账收尾为 ready，结束跟踪；删除暂存副本
                await self._update_item(
                    item_id,
                    document_id=str(document["id"]),
                    status="ready",
                )
                await self._cleanup_staged_for_document(document, stats)
                stats["ready"] += 1
                continue
            if (
                status == DocumentStatus.PENDING.value
                and not str(document.get("remote_document_id") or "")
            ):
                # 上次上传在“已建本地记录、还没拿到远端 ID”之间中断：
                # 标记失败等待重试（状态机不允许 PENDING 直接重试）
                await self._update_item(
                    item_id,
                    status="failed",
                    attempts=int(item.get("attempts") or 0) + 1,
                    last_error="上次上传未完成",
                )
                stats["failed"] += 1
                continue
            if status in _RETRYABLE_DOCUMENT_STATUSES:
                attempts = int(item.get("attempts") or 0) + 1  # 这次算第几次尝试
                if attempts <= self.sync_config.max_attempts:
                    try:
                        # 让知识库服务重新提交解析（复用本地原文，不重新上传文件）
                        await self.service.retry_document(
                            document_id=str(document["id"]),
                            principal=self.principal,
                            # 带上次数，审计日志里能区分是第几次重试
                            request_id=f"source-sync-retry:{record_id}:{attempts}",
                        )
                        # 重试成功：台账改为 parsing，并加回 remaining 继续跟踪
                        await self._update_item(
                            item_id,
                            status="parsing",
                            attempts=attempts,
                            last_error=str(document.get("error_message") or "")[:1000],
                        )
                        item["attempts"] = attempts
                        item["status"] = "parsing"
                        remaining.append(item)
                        stats["retried"] += 1
                    except Exception as exc:  # noqa: BLE001
                        # 重试动作本身又失败了：记下错误，等下一轮
                        await self._update_item(
                            item_id,
                            status="failed",
                            attempts=attempts,
                            last_error=str(exc)[:1000],
                        )
                        stats["failed"] += 1
                else:
                    # 超过最大重试次数：彻底放弃，保留最后一次的错误信息方便排查
                    await self._update_item(
                        item_id,
                        status="failed",
                        attempts=attempts,
                        last_error=str(document.get("error_message") or "超过重试次数")[:1000],
                    )
                    stats["failed"] += 1
                continue
            # 其余状态（解析中 / 暂停 / 已拿到远端 ID 的 PENDING）：
            # 还没结束，原样留在 remaining，下一轮继续检查
            remaining.append(item)
        return remaining, stats

    async def _wait_for_inflight_slot(
        self,
        inflight: list[dict[str, Any]],
        *,
        base_id: str,
        stats: dict[str, int],
    ) -> list[dict[str, Any]]:
        """在途文档达到上限时回收状态，直到腾出空位。

        扫描记录（重试）和上传新记录前都要经过这里，避免两处重复写限流循环。
        """
        while len(inflight) >= self.sync_config.max_inflight:
            inflight, batch_stats = await self.refresh_inflight(
                inflight, base_id=base_id
            )
            for key, value in batch_stats.items():
                stats[key] += value
            if len(inflight) >= self.sync_config.max_inflight:
                await asyncio.sleep(self.sync_config.poll_interval_seconds)
        return inflight

    # ---------------- 主循环 ----------------

    async def run(
        self,
        *,
        start_date: str | None = None,
        limit: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, int]:
        """执行一次完整的同步，返回统计信息。

        参数：
            start_date: 覆盖配置里的起始日期（YYYY-MM-DD），用于首次回填或补数据；
            limit:      最多扫描多少条源记录，主要给测试/演练用（达到后提前结束）；
            dry_run:    演习模式：不建库、不上传，只打印“本来会入库”的清单。

        整体流程：
            1. 确定目标知识库（base_id）；
            2. 决定时间窗口：首次回填从 start_date 开始，否则只回溯 lookback_days 天；
            3. 恢复上次中断时还在途的文档，并清理超期暂存残留；
            4. 按窗口拉取源记录：已入库→跳过，失败→重试；
            5. 新记录先批量拷贝到暂存目录，再从暂存文件上传；
            6. 等在途文档全部解析完（READY 后删除暂存副本），打印统计。
        """
        # stats 是本次同步的“统计计数器”，最终会打印到日志，也作为返回值给调用方：
        #   scanned  : 从文档 SQL 读到的记录条数
        #   existing : 本地已入库而跳过的条数
        #   uploaded : 本次新提交上传的条数
        #   failed   : 失败条数（含在途回收时判定失败的）
        #   retried  : 触发重试的条数
        #   ready    : 直接确认为已完成的条数
        #   planned  : dry-run 模式下“本来会入库”的条数
        #   truncated: 是否因为 limit 提前停止（1=是，0=否）
        #   copied   : 本批实际拷贝到暂存目录的文件数
        #   reused   : 暂存目录已有同尺寸副本、直接复用的文件数
        #   copy_failed: 暂存拷贝失败条数
        #   staged_deleted: READY/DELETED 后删除的暂存文件数
        #   staged_cleanup_failed: 暂存文件删除失败数
        #   stale_cleaned: 本轮开始时清理的超期暂存残留数
        stats = {
            "scanned": 0,
            "existing": 0,
            "uploaded": 0,
            "failed": 0,
            "retried": 0,
            "ready": 0,
            "planned": 0,
            "truncated": 0,
            "copied": 0,
            "reused": 0,
            "copy_failed": 0,
            "staged_deleted": 0,
            "staged_cleanup_failed": 0,
            "stale_cleaned": 0,
        }
        if not dry_run:
            # 暂存目录：存在直接用，不存在创建
            await self.prepare_staging()
        else:
            # 演习模式只校验路径，不创建目录、不拷贝、不删除
            self._staging_dir = resolve_staging_dir(self.sync_config)
        # dry-run 不需要（也不应该）建库，所以跳过解析目标库
        base_id = await self.resolve_base_id() if not dry_run else ""
        if not dry_run and self._staging_dir is not None:
            # 清理超期孤儿文件；失败/解析中文档的暂存副本保留
            keep_names = await self._staged_keep_names(base_id)
            stats["stale_cleaned"] = await run_in_threadpool(
                cleanup_stale_staging_files,
                self._staging_dir,
                self.sync_config.staging_retention_hours,
                keep_names=keep_names,
            )
        now = datetime.now(UTC)  # 本次同步的“截止时间”，统一用 UTC，避免时区问题
        # 看看台账里有没有记录：全为 0 说明是第一次运行（决定要不要做历史回填）
        counts = await self._repo(
            self.repository.count_sync_items, source=self.source
        )
        window_start = self._window_start(now, start_date, first_run=not any(counts.values()))
        # 把 [起始时间, 现在) 按 lookback_days 天切成若干段，逐段查询，
        # 避免一次性把几个月的数据全部拉进内存
        windows = list(_iter_windows(window_start, now, self.sync_config.lookback_days))

        inflight: list[dict[str, Any]] = []  # 在途台账行（上传/解析中，尚未结束）
        if not dry_run:
            # 断点续跑：恢复上次中断时仍在 uploading/parsing 的台账行
            inflight = await self._repo(
                self.repository.list_sync_items,
                source=self.source,
                statuses=list(_ACTIVE_ITEM_STATUSES),
                limit=self.sync_config.max_inflight,
            )
            if inflight:
                # 先检查它们对应的文档现在到底是什么状态，再决定继续等还是重试
                inflight, resumed_stats = await self.refresh_inflight(
                    inflight, base_id=base_id
                )
                for key, value in resumed_stats.items():
                    stats[key] += value
                logger.info("研报 SQL 同步恢复在途文档 | count=%s", len(inflight))
        for window_start_at, window_end_at in windows:
            # 查询数据库是阻塞操作，必须放线程池，否则会卡住事件循环
            rows = await run_in_threadpool(
                fetch_source_rows, self.sync_config, window_start_at, window_end_at
            )
            logger.info(
                "研报 SQL 同步窗口 | start=%s end=%s rows=%s",
                window_start_at,
                window_end_at,
                len(rows),
            )
            # 先扫描本窗口：已入库/可重试的记录当场处理，新记录收集起来
            pending_rows: list[SourceRow] = []
            for raw_row in rows:
                # limit 到达后提前结束（演练/测试用），并标记“被截断”
                if limit is not None and stats["scanned"] >= limit:
                    stats["truncated"] = 1
                    break
                stats["scanned"] += 1
                parsed_row = parse_source_row(raw_row)
                record_id = parsed_row.record_id
                # 在途文档达到上限时先回收，避免重试/上传突破并发上限
                inflight = await self._wait_for_inflight_slot(
                    inflight, base_id=base_id, stats=stats
                )
                # 先查本地是否已有这条源记录对应的文档（用于“跳过”或“重试”判断）
                document = (
                    await self._repo(
                        self.repository.find_document_by_source_record,
                        self.source,
                        record_id,
                        base_id=base_id,
                        include_deleted=True,
                    )
                    if record_id
                    else None
                )
                if document is not None:
                    status = str(document.get("status") or "")
                    if status == DocumentStatus.DELETED.value:
                        # 当前库内已被删除：尊重删除动作，不再重新上传
                        logger.info(
                            "文档同步跳过：文档已删除 | id=%s doc=%s",
                            record_id,
                            document["id"],
                        )
                        stats["existing"] += 1
                        continue
                    remote_id = str(document.get("remote_document_id") or "")
                    if status == DocumentStatus.PENDING.value and not remote_id:
                        # 上传中断（本地记录已建、远端还没拿到 ID）：
                        # 先把状态改成 FAILED，让它进入下一段“可重试”分支，
                        # 重试会复用本地原文，避免重复建本地记录。
                        document = await self._repo(
                            self.repository.update_document,
                            str(document["id"]),
                            status=DocumentStatus.FAILED.value,
                            error_code="SYNC_UPLOAD_INTERRUPTED",
                            error_message="上次上传未完成，重新提交",
                        )
                        status = DocumentStatus.FAILED.value
                    elif status in _READY_DOCUMENT_STATUSES:
                        # 已完成/解析中/暂停：都不需要再上传，直接跳过
                        stats["existing"] += 1
                        continue
                    if status in _RETRYABLE_DOCUMENT_STATUSES:
                        # 走重试：先确保有台账行，再累计尝试次数
                        publish_date = parsed_row.publish_date
                        retry_item = await self._repo(
                            self.repository.create_or_get_sync_item,
                            source=self.source,
                            source_record_id=record_id,
                            publish_date=publish_date,
                        )
                        retry_item_id = str(retry_item["id"])
                        attempts = int(retry_item.get("attempts") or 0) + 1
                        if attempts > self.sync_config.max_attempts:
                            # 次数用完了：不再重试，记下失败原因
                            await self._update_item(
                                retry_item_id,
                                status="failed",
                                attempts=attempts,
                                last_error="超过最大重试次数",
                            )
                            stats["failed"] += 1
                            continue
                        try:
                            await self.service.retry_document(
                                document_id=str(document["id"]),
                                principal=self.principal,
                                request_id=f"source-sync-retry:{record_id}:{attempts}",
                            )
                        except Exception as exc:  # noqa: BLE001
                            # 这次重试没成功：记录错误，下一轮再说
                            await self._update_item(
                                retry_item_id,
                                document_id=str(document["id"]),
                                status="failed",
                                attempts=attempts,
                                last_error=str(exc)[:1000],
                            )
                            stats["failed"] += 1
                            logger.error("研报文档重试失败 | id=%s err=%s", record_id, exc)
                            continue
                        # 重试成功：台账改为 parsing，并放进在途列表继续跟踪
                        await self._update_item(
                            retry_item_id,
                            document_id=str(document["id"]),
                            status="parsing",
                            attempts=attempts,
                        )
                        refreshed = await self._repo(
                            self.repository.get_sync_item, self.source, record_id
                        )
                        if refreshed is not None:
                            inflight.append(refreshed)
                        stats["retried"] += 1
                        continue
                if dry_run:
                    # 演习模式：只记录“本来会入库”，不做任何写操作
                    stats["planned"] += 1
                    logger.info(
                        "[dry-run] 待入库 | id=%s date=%s batchid=%s name=%s",
                        record_id,
                        parsed_row.publish_date,
                        parsed_row.batch_id,
                        build_upload_name(
                            parsed_row.title,
                            record_id,
                            parsed_row.batch_id,
                        ),
                    )
                    continue
                # 新记录先收集：等本窗口扫描完，统一批量拷贝到暂存目录
                pending_rows.append(parsed_row)

            # ---- 批量暂存：先把本窗口内要新入库的文件拷到暂存目录 ----
            staged_files: dict[str, Path] = {}
            stage_errors: dict[str, str] = {}
            if pending_rows and not dry_run:
                staged_files, stage_errors, stage_stats = await self.stage_rows(
                    pending_rows
                )
                for key, value in stage_stats.items():
                    stats[key] += value
                logger.info(
                    "研报 SQL 批量暂存完成 | start=%s end=%s rows=%s "
                    "copied=%s reused=%s failed=%s",
                    window_start_at,
                    window_end_at,
                    len(pending_rows),
                    stage_stats["copied"],
                    stage_stats["reused"],
                    stage_stats["copy_failed"],
                )

            # ---- 从暂存文件逐条上传（滑动窗口限流在这里生效）----
            if not dry_run:
                for parsed_row in pending_rows:
                    record_id = parsed_row.record_id
                    inflight = await self._wait_for_inflight_slot(
                        inflight, base_id=base_id, stats=stats
                    )
                    item = await self._upload_row(
                        base_id=base_id,
                        row=parsed_row,
                        staged_file=staged_files.get(record_id),
                        stage_error=stage_errors.get(record_id),
                    )
                    if item is None:
                        # 跳过或失败，原因已写进台账
                        stats["failed"] += 1
                        continue
                    if str(item.get("status")) != DocumentStatus.READY.value:
                        # 还在解析：放进在途列表，等待后续回收
                        inflight.append(item)
                        stats["uploaded"] += 1
                        continue
                    # 极快解析完成的（比如小文件）：直接记为 ready 并删暂存副本
                    stats["ready"] += 1
                    staged_file = staged_files.get(record_id)
                    if staged_file is not None:
                        result = await self._delete_staged_file(staged_file)
                        if result == "deleted":
                            stats["staged_deleted"] += 1
                        elif result == "failed":
                            stats["staged_cleanup_failed"] += 1
            # 外层循环也要检查 limit（一次窗口可能有多条记录）
            if limit is not None and stats["scanned"] >= limit:
                break

        # 【收尾】所有记录都处理完后，等在途文档一个个结束（完成或失败），
        # 直到 inflight 为空，才认为本次同步真正完成
        while inflight and not dry_run:
            inflight, batch_stats = await self.refresh_inflight(
                inflight, base_id=base_id
            )
            for key, value in batch_stats.items():
                stats[key] += value
            if inflight:
                # 还有没结束的：睡一会儿再查，避免疯狂空转打爆知识库接口
                await asyncio.sleep(self.sync_config.poll_interval_seconds)

        logger.info(
            "文档同步完成 | " + " ".join(f"{key}={value}" for key, value in stats.items())
        )
        return stats

    def _window_start(
        self, now: datetime, start_date: str | None, *, first_run: bool
    ) -> datetime:
        """计算本次同步的**起始时间**。

        规则：
        - 首次运行（台账里还没有任何记录）且配置/参数指定了 start_date：
          从 start_date 开始，做“历史回填”；
        - 其他情况：只处理最近 lookback_days 天，避免每次都全量扫描。

        注意：即使有 start_date，非首次运行也不会再从头扫，
        因为老记录已经在台账里，重新扫只会浪费时间。
        """
        configured = (start_date or self.sync_config.start_date or "").strip()
        if configured and first_run:
            # 首次回填：从配置的起始日期开始按窗口切分，避免一次拉全量
            return datetime.strptime(configured, "%Y-%m-%d").replace(tzinfo=UTC)
        return now - timedelta(days=self.sync_config.lookback_days)


# ---------------------------------------------------------------------------
# 生命周期入口
# ---------------------------------------------------------------------------
async def run_source_sync(
    app,
    *,
    start_date: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
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
        return await engine.run(start_date=start_date, limit=limit, dry_run=dry_run)


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


async def _sync_loop(app) -> None:
    """后台循环：延迟启动 → 反复执行同步 → 按间隔睡眠。

    说明：
    - start_delay_minutes > 0 时，先等几分钟再第一次执行，
      给应用一个“启动缓冲”，避免刚起来就抢资源；
    - interval_minutes <= 0 表示“只跑一次”，跑完就退出循环；
    - CancelledError 必须原样抛出：吞掉它会导致任务无法被正常取消，
      关闭服务时可能卡住。
    """
    sync_config = app.state.knowledge_config.source_sync
    delay = int(getattr(sync_config, "start_delay_minutes", 0))
    try:
        if delay > 0:
            logger.info("研报 SQL 同步将在 %s 分钟后首次执行", delay)
            await asyncio.sleep(delay * 60)  # 分钟换算成秒
        while True:
            try:
                await run_source_sync(app)
            except asyncio.CancelledError:
                raise  # 取消信号：必须往上抛，不能当成普通错误吞掉
            except Exception as exc:  # noqa: BLE001
                # 单次同步失败只记日志，不退出循环——下一轮还会再试
                logger.error("研报 SQL 同步失败: %s", exc)
            interval = int(getattr(sync_config, "interval_minutes", 0))
            if interval <= 0:
                break  # 配置为 0：只跑一次
            await asyncio.sleep(interval * 60)
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
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass


# 本模块对外公开的名字：from xxx import * 或工具做静态检查时会参考这个列表
__all__ = [
    "SourceRow",
    "SourceSyncEngine",
    "build_source_query",
    "build_staged_name",
    "build_upload_name",
    "cleanup_stale_staging_files",
    "copy_to_staging",
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
