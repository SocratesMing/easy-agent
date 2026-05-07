import gzip
import logging
import os
import time
from datetime import datetime, timedelta

import schedule

from . import bloom_classification as classify
from .bloom_analysis import analysis_bloom
from .bloom_repository import insert_bloom_analysis

logger = logging.getLogger("easy_agent.bloom")


def start_scheduler(db, llm):
    logger.info("彭博数据读取定时任务启动")

    def job():
        scheduler_process(db, llm)

    schedule.every().day.at("17:00").do(job)
    while True:
        schedule.run_pending()
        time.sleep(60)


def read_file(file_path: str, encoding: str = 'utf-8') -> str | None:
    try:
        with open(file_path, 'r', encoding=encoding) as file:
            return file.read()
    except FileNotFoundError:
        logger.error("文件[%s]不存在", file_path)
        return None
    except Exception as e:
        logger.error("文件[%s]处理失败 %s", file_path, e)
        return None


def read_gz_file(file_path: str, encoding: str = 'utf-8') -> str | None:
    try:
        with gzip.open(file_path, 'rb') as file:
            return file.read().decode(encoding)
    except FileNotFoundError:
        logger.error("文件[%s]不存在", file_path)
        return None
    except Exception as e:
        logger.error("文件[%s]处理失败 %s", file_path, e)
        return None


def scheduler_process(db, llm):
    today = datetime.now()
    todaystr = today.strftime("%Y%m%d")
    lock_key = f"fmqt_lock_key_{todaystr}"

    lock_acquired = _try_acquire_lock(db, lock_key)
    if not lock_acquired:
        logger.info("获取锁失败，另一台机器处理中")
        return

    file_path = f"/qts/data/bloom/FMQT_001_A_1_{todaystr}.out"
    logger.info("获取锁成功，日期[%s]，文件[%s]，开始执行定时任务", todaystr, file_path)

    content = read_file(file_path)
    if content is None:
        _release_lock(db, lock_key)
        return

    logger.info("文件[%s]读取成功", file_path)
    classify.classification(content, today.strftime("%Y-%m-%d"), db)
    time.sleep(10)

    logger.info("=======开始大模型分析[%s]=======", today)
    result = analysis_bloom(db, llm, today)
    if result is not None:
        for item in result:
            insert_bloom_analysis(db, item)
        logger.info("=======大模型分析完成=======")

    _release_lock(db, lock_key)

    yesterday = today - timedelta(days=1)
    yesterdaystr = yesterday.strftime("%Y%m%d")
    yes_lock_key = f"fmqt_lock_key_{yesterdaystr}"
    _clean_lock(db, yes_lock_key)


def _try_acquire_lock(db, lock_key: str) -> bool:
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            db._execute(cursor, """
                INSERT INTO fmqt_lock (lock_key, created_at)
                VALUES (?, ?)
            """, (lock_key, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            return True
    except Exception:
        return False


def _release_lock(db, lock_key: str):
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            db._execute(cursor, "DELETE FROM fmqt_lock WHERE lock_key = ?", (lock_key,))
    except Exception as e:
        logger.warning("释放锁失败: %s", e)


def _clean_lock(db, lock_key: str):
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            db._execute(cursor, "DELETE FROM fmqt_lock WHERE lock_key = ?", (lock_key,))
    except Exception as e:
        logger.warning("清理锁失败: %s", e)
