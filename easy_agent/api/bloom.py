"""Bloom financial data API routes"""

import logging
import os
import sys
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Body

from ..domain.bloom.bloom_repository import (
    query_bloom_dashboard,
    query_bloom_stock_index,
    query_bloom_chart,
    query_bloom_stock_index_chart,
    query_bloom_analysis_by_pair,
    upsert_bloom_analysis,
)
from ..domain.bloom.bloom_classification import classification, classification_gz
from ..domain.bloom.bloom_scheduler import read_file, read_gz_file
from ..domain.bloom.bloom_analysis import analysis_bloom
from ..db import get_database

logger = logging.getLogger("easy_agent.bloom")

bloom_router = APIRouter(prefix="/api/bloom", tags=["Bloom"])


@bloom_router.post("/queryBloom", summary="查询彭博数据面板")
def query_bloom(
    filters: list[dict] = Body(
        ...,
        examples=[
            [
                {"type": "短期基准利率", "region": "瑞士"},
                {"type": "短期基准利率", "region": "欧元区"},
            ]
        ],
    ),
):
    db = get_database()
    results = []
    logger.info("查询彭博数据面板")
    try:
        for item in filters:
            results.append(query_bloom_dashboard(db, item["type"], item["region"]))
    except Exception as e:
        logger.error("调用查询彭博数据面板异常 %s", str(e))
    return results


@bloom_router.post("/queryBloomStockIndex", summary="查询彭博数据面板-股指")
def query_stock_index(
    filter: dict = Body(..., examples=[{"type": "股指价格", "region": "道琼斯指数"}]),
):
    db = get_database()
    logger.info(
        "查询彭博数据面板-股指 type=%s region=%s",
        filter.get("type"),
        filter.get("region"),
    )
    return query_bloom_stock_index(db, filter["type"], filter["region"])


@bloom_router.post("/queryBloomLineChart", summary="查询彭博数据折线图")
def query_bloom_line_chart(
    query: dict = Body(
        ...,
        examples=[
            {
                "type": "短期基准利率",
                "region": ["瑞士", "加拿大"],
                "startDate": "2025-06-01",
                "endDate": "2025-06-15",
            }
        ],
    ),
) -> dict:
    db = get_database()
    logger.info("查询彭博数据折线图")
    results = {}
    for region in query["region"]:
        results[region] = query_bloom_chart(
            db, query["type"], region, str(query["startDate"]), str(query["endDate"])
        )
    return results


@bloom_router.post("/queryBloomStockIndexChart", summary="查询股指折线图")
def query_stock_index_chart(
    query: dict = Body(
        ...,
        examples=[
            {
                "type": "股指价格",
                "bloomCodeCN": ["道琼斯指数", "纳斯达克指数"],
                "startDate": "2025-06-01",
                "endDate": "2025-06-15",
            }
        ],
    ),
) -> dict:
    db = get_database()
    logger.info("查询股指折线图")
    results = {}
    for bloom_code_cn in query["bloomCodeCN"]:
        results[bloom_code_cn] = query_bloom_stock_index_chart(
            db,
            query["type"],
            bloom_code_cn,
            str(query["startDate"]),
            str(query["endDate"]),
        )
    return results


@bloom_router.post("/queryBloomAnalysis", summary="查询大模型分析彭博数据")
def query_bloom_analysis(
    pair: str = "EURUSD", startDate: str = "2025-06-27", endDate: str = "2025-06-27"
) -> List:
    db = get_database()
    logger.info("查询大模型分析彭博数据 pair=%s", pair)
    return query_bloom_analysis_by_pair(db, pair, startDate, endDate)


@bloom_router.post("/importBloom", summary="全量导入彭博数据")
def import_bloom(startDate: str = "20250627", endDate: str = "20250628"):
    db = get_database()
    start = datetime.strptime(startDate, "%Y%m%d")
    end = datetime.strptime(endDate, "%Y%m%d")

    currentdate = start
    while currentdate <= end:
        if sys.platform.startswith("win"):
            file_path = (
                f"E:\\download\\FMQT_001_A_1_{currentdate.strftime('%Y%m%d')}.out"
            )
        else:
            file_path = (
                f"/qts/data/bloom/FMQT_001_A_1_{currentdate.strftime('%Y%m%d')}.out"
            )

        logger.info("导入数据 [%s]", file_path)
        if os.path.exists(file_path):
            content = read_file(file_path)
            if content is not None:
                logger.info("文件[%s]读取成功", file_path)
                classification(content, currentdate.date().strftime("%Y-%m-%d"), db)
        else:
            logger.info("文件[%s]不存在,尝试读取压缩包", file_path)
            gz_path = file_path + ".gz"
            if os.path.exists(gz_path):
                content = read_gz_file(gz_path)
                if content is not None:
                    logger.info("文件[%s]读取成功", gz_path)
                    classification_gz(
                        content, currentdate.date().strftime("%Y-%m-%d"), db
                    )

        currentdate += timedelta(days=1)

    return True


@bloom_router.post("/reAnalysisBloom", summary="彭博指定日数据分析落库")
def re_analysis_bloom(analysisDate: str = "20250627"):
    db = get_database()
    start = datetime.strptime(analysisDate, "%Y%m%d")
    result = analysis_bloom(db, start)

    if result is not None:
        for item in result:
            upsert_bloom_analysis(db, item)
    return True
