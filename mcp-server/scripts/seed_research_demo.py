"""投资研究相关场景的演示数据：建表并写入最小自洽数据（本地联调用）。

覆盖四个场景（iData 场景见 ``seed_idata_demo.py``）：

- **宏观预测**（6 张）：1 期报告 + 3 个指标 + 多模型预测 + SHAP 归因 + 入选特征 + 大模型解读
- **利率择时**（2 张）：1 次成功运行 + 5 个交易日的预测
- **研报观点**（2 张）：2 周的观点矩阵与宏观预测
- **黄金归因**（1 张宽表）：2 个模型 × 30 个交易日

表结构照 ``docs/MCP_docs/*_SQL清单_MCP信息.md``；字段名与各领域模块
（``domestic_macro`` / ``rates_timing`` / ``weekly_research`` / ``gold_analysis``）
SQL 里引用的完全一致，多一列无妨，少一列工具就会报 ``Unknown column``。

id 一律显式给出、不设 AUTO_INCREMENT：写入用 ``INSERT ... VALUES (...)`` 不带列名，
自增列会让列数对不上。

用法（在 mcp-server 目录下）::

    uv run python -m scripts.seed_research_demo                  # 四个场景全建
    uv run python -m scripts.seed_research_demo --scene gold      # 只建某个场景
    uv run python -m scripts.seed_research_demo --drop            # 只清理

脚本幂等：重复执行会先清空这些表的演示数据再重新写入。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from typing import Any

from easy_mcp_server.businesses.gold_analysis import gold_analysis
from easy_mcp_server.businesses.macro_forecast import domestic_macro
from easy_mcp_server.businesses.rates_timing import rates_timing
from easy_mcp_server.businesses.treasury_1y import treasury_1y
from easy_mcp_server.businesses.weekly_research import weekly_research
from easy_mcp_server.db import connection

# ── 建表语句 ────────────────────────────────────────────────────────────
# 时间字段统一 VARCHAR：工具侧对时间既有直接返回也有格式化，varchar 两种都兼容。

DDL: dict[str, tuple[str, ...]] = {
    "forecast": (
        """
        CREATE TABLE IF NOT EXISTS fmqt_forecast_report_meta (
            id                INT          NOT NULL,
            title             VARCHAR(255) NOT NULL COMMENT '报告标题',
            forecast_month    VARCHAR(7)   NOT NULL COMMENT '预测月份 YYYY-MM，唯一',
            data_cutoff       VARCHAR(7)            COMMENT '数据截止月',
            generated_at      VARCHAR(32)           COMMENT '报告生成时间',
            indicator_count   INT                   COMMENT '指标数量',
            model_pool        VARCHAR(255)          COMMENT '模型池描述',
            narrative_summary TEXT                  COMMENT '报告摘要',
            the_status        VARCHAR(16)           COMMENT 'ingesting / done',
            PRIMARY KEY (id),
            UNIQUE KEY uk_forecast_month (forecast_month)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='宏观预测报告元数据（演示）'
        """,
        """
        CREATE TABLE IF NOT EXISTS fmqt_forecast_indicator (
            id           INT          NOT NULL,
            report_id    INT          NOT NULL COMMENT '所属报告 id',
            target_name  VARCHAR(128) NOT NULL COMMENT '指标名（wind target）',
            group_cat    VARCHAR(16)           COMMENT '增长/通胀/信用/外贸/其他',
            market_pred  DECIMAL(18,4)         COMMENT '市场一致预期',
            the_prior    DECIMAL(18,4)         COMMENT '前值',
            pred         DECIMAL(18,4)         COMMENT '模型预测值',
            r2           DECIMAL(10,4)         COMMENT 'Top1 模型 R2',
            zhangyu      DECIMAL(18,4)         COMMENT '张瑜团队预测值',
            is_active    TINYINT               COMMENT '1 生效 0 失效',
            PRIMARY KEY (id),
            KEY idx_report (report_id),
            KEY idx_target (target_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='宏观预测指标（演示）'
        """,
        """
        CREATE TABLE IF NOT EXISTS fmqt_forecast_prediction (
            id           INT          NOT NULL,
            indicator_id INT          NOT NULL COMMENT '所属指标 id',
            model_rank   INT                   COMMENT '模型综合排名，1 最优',
            model        VARCHAR(64)           COMMENT '模型名',
            pred         DECIMAL(18,4)         COMMENT '该模型预测值',
            the_prior    DECIMAL(18,4)         COMMENT '前值',
            delta        DECIMAL(18,4)         COMMENT 'pred - prior',
            PRIMARY KEY (id),
            KEY idx_indicator (indicator_id, model_rank)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='多模型预测明细（演示）'
        """,
        """
        CREATE TABLE IF NOT EXISTS fmqt_forecast_feature_contribution (
            id           INT          NOT NULL,
            indicator_id INT          NOT NULL COMMENT '所属指标 id',
            model_rank   INT                   COMMENT 'Top-3 模型排名',
            model        VARCHAR(64)           COMMENT '模型名',
            feature_rank INT                   COMMENT '特征重要性排名',
            feature      VARCHAR(128)          COMMENT '特征名',
            shap         DECIMAL(18,6)         COMMENT 'SHAP 贡献值',
            share        DECIMAL(10,6)         COMMENT '影响力占比 0~1',
            PRIMARY KEY (id),
            KEY idx_indicator (indicator_id, model_rank, feature_rank)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SHAP 特征归因（演示）'
        """,
        """
        CREATE TABLE IF NOT EXISTS fmqt_forecast_selected_feature (
            id           INT          NOT NULL,
            indicator_id INT          NOT NULL COMMENT '所属指标 id',
            feature      VARCHAR(128) NOT NULL COMMENT '入选特征名',
            PRIMARY KEY (id),
            KEY idx_indicator (indicator_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='入选特征（演示）'
        """,
        """
        CREATE TABLE IF NOT EXISTS fmqt_forecast_analysis (
            id           INT          NOT NULL,
            report_id    INT          NOT NULL COMMENT '所属报告 id',
            scenario     VARCHAR(16)           COMMENT 'SC_GLOBAL / SC_FEATURE / SC_INDICATOR',
            indicator_id INT                   COMMENT 'SC_GLOBAL 时为 NULL',
            title        VARCHAR(255)          COMMENT '分析标题',
            content      TEXT                  COMMENT '正文（JSON 文本）',
            the_status   VARCHAR(16)           COMMENT 'pending / done / error',
            err_msg      VARCHAR(255)          COMMENT '失败原因',
            created_at   VARCHAR(32)           COMMENT '创建时间',
            PRIMARY KEY (id),
            KEY idx_report (report_id, scenario)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='大模型解读落库（演示）'
        """,
    ),
    "rates": (
        """
        CREATE TABLE IF NOT EXISTS fmqt_predictions_rates (
            id                INT          NOT NULL,
            signal_date       DATE         NOT NULL COMMENT '预测参考日期 T，唯一',
            run_id            INT                   COMMENT '写入该记录的运行编号',
            probability       FLOAT                 COMMENT '收益率上行概率 0-1',
            prediction        INT                   COMMENT '按 threshold 判定：1 上行 0 下行',
            threshold         FLOAT                 COMMENT '该条使用的决策阈值',
            strong_signal     INT                   COMMENT '1/0/-1/-2',
            signal_label      VARCHAR(64)           COMMENT '中文信号名',
            validation_status VARCHAR(16)           COMMENT 'pending / realized',
            actual            INT                   COMMENT '成熟真实类别',
            is_correct        TINYINT               COMMENT '1 正确 0 错误，未成熟 NULL',
            factor_version    VARCHAR(64)           COMMENT '因子版本',
            model_version     VARCHAR(64)           COMMENT '模型版本',
            created_at        DATETIME              COMMENT '首次创建时间',
            updated_at        DATETIME              COMMENT '最近更新时间',
            PRIMARY KEY (id),
            UNIQUE KEY uk_signal_date (signal_date),
            KEY idx_run (run_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='利率择时预测结果（演示）'
        """,
        """
        CREATE TABLE IF NOT EXISTS fmqt_prediction_runs_rates (
            id                       INT          NOT NULL,
            run_type                 VARCHAR(16)           COMMENT 'BOOTSTRAP / LATEST',
            status                   VARCHAR(16)           COMMENT 'QUEUED/RUNNING/SUCCEEDED/FAILED',
            stage                    VARCHAR(64)           COMMENT '执行阶段',
            error_message            TEXT                  COMMENT '错误摘要',
            source_file_name         VARCHAR(512)          COMMENT '原始 CSV 文件名',
            file_sha256              VARCHAR(64)           COMMENT 'CSV 内容摘要，唯一',
            source_size_bytes        INT                   COMMENT 'CSV 字节数',
            csv_gzip                 LONGBLOB              COMMENT 'gzip 留档，MCP 不读',
            row_count                INT                   COMMENT '模型输入行数',
            column_count             INT                   COMMENT '审计列数',
            latest_data_date         DATE                  COMMENT '输入最新参考日期',
            cycle_anchor             INT                   COMMENT '筛选周期锚点行位置',
            screening_start          INT                   COMMENT '筛选窗口起始（含）',
            screening_end_exclusive  INT                   COMMENT '筛选窗口结束（不含）',
            mature_screening_count   INT                   COMMENT '成熟样本数',
            train_start              INT                   COMMENT '训练窗口起始（含）',
            train_end_exclusive      INT                   COMMENT '训练窗口结束（不含）',
            train_end_date           DATE                  COMMENT '训练窗口最后一行日期',
            selected_factor_count    INT                   COMMENT '入选因子数',
            selected_factors         JSON                  COMMENT '入选因子名数组',
            best_params              JSON                  COMMENT '最优超参数',
            decision_threshold       FLOAT                 COMMENT '分类阈值',
            config_hash              VARCHAR(64)           COMMENT '配置摘要',
            code_hash                VARCHAR(64)           COMMENT '代码摘要',
            metrics                  JSON                  COMMENT '回测指标，LATEST 为 NULL',
            factor_explanation       TEXT                  COMMENT '最新一期解释正文',
            explanation_metadata     JSON                  COMMENT '解释状态与证据',
            created_at               DATETIME              COMMENT '创建时间',
            started_at               DATETIME              COMMENT '开始时间',
            finished_at              DATETIME              COMMENT '结束时间',
            PRIMARY KEY (id),
            UNIQUE KEY uk_file_sha256 (file_sha256),
            KEY idx_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='利率择时运行审计（演示）'
        """,
    ),
    "weekly": (
        """
        CREATE TABLE IF NOT EXISTS weekly_analyzer_data (
            `week`            VARCHAR(32) NOT NULL COMMENT '周标签 YYYYMMDD-YYYYMMDD',
            `data_json`       JSON                 COMMENT '观点正文 {"views":[...]}',
            `market_overview` TEXT                 COMMENT '周度综述 Markdown',
            `created_at`      VARCHAR(32)          COMMENT '写入时间',
            PRIMARY KEY (`week`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='周度研报观点矩阵（演示）'
        """,
        """
        CREATE TABLE IF NOT EXISTS weekly_macro_data (
            `week`            VARCHAR(32) NOT NULL COMMENT '周标签',
            `macro_json`      JSON                 COMMENT '宏观正文 {"as_of":...,"items":[...]}',
            `as_of`           VARCHAR(32)          COMMENT '宏观数据生成时间',
            `indicator_count` INT                  COMMENT '指标行数',
            PRIMARY KEY (`week`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='周度机构宏观预测（演示）'
        """,
    ),
    "gold": (
        """
        CREATE TABLE IF NOT EXISTS fmqt_gold_analysis_result (
            id                INT           NOT NULL,
            model_name        VARCHAR(32)   NOT NULL COMMENT '多元线性回归 / 随机森林',
            creation_time     DATE          NOT NULL COMMENT '数据日期（展示区间截止日）',
            comex_volume      DECIMAL(18,6)          COMMENT 'COMEX成交量',
            fund_flow         DECIMAL(18,6)          COMMENT '资金流向（SPDR 黄金 ETF 持有量）',
            usd_factor        DECIMAL(18,6)          COMMENT '美元因素',
            treasury_factor   DECIMAL(18,6)          COMMENT '美债因素',
            risk_sentiment    DECIMAL(18,6)          COMMENT '风险情绪（WTI+VIX）',
            commodity_factor  DECIMAL(18,6)          COMMENT '商品（LME 铜）',
            PRIMARY KEY (id),
            UNIQUE KEY uk_model_date (model_name, creation_time)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='黄金价格多因子归因结果（演示）'
        """,
    ),
    "treasury_1y": (
        """
        CREATE TABLE IF NOT EXISTS fmqt_treasury_1y_history (
            `signal_date` DATE        NOT NULL COMMENT '结果参考日期，唯一',
            `result_json` JSON        NOT NULL COMMENT '当日完整结果对象',
            `created_at`  DATETIME(6) NOT NULL COMMENT '首次写入时间 UTC',
            `updated_at`  DATETIME(6) NOT NULL COMMENT '最近写入时间 UTC',
            PRIMARY KEY (`signal_date`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='1年期国债模型历史结果（演示）'
        """,
    ),
}

# ── 演示数据 ────────────────────────────────────────────────────────────

REPORT_ID = 1
FORECAST_MONTH = "2026-09"
DATA_CUTOFF = "2026-08"
GENERATED_AT = "2026-09-05 09:30:00"
RUN_ID = 1

# (指标 id, 指标名, 板块, 前值, 预测值, 市场预期, 张瑜预测, R2)
INDICATORS: tuple[tuple[int, str, str, float, float, float, float, float], ...] = (
    (1, "GDP:当季同比", "增长", 4.8, 4.9, 4.85, 4.95, 0.87),
    (2, "CPI:当月同比", "通胀", 0.4, 0.6, 0.55, 0.50, 0.72),
    (3, "社会融资规模存量:同比", "信用", 8.2, 8.4, 8.30, 8.45, 0.81),
)

# 同一指标下的三个模型，排名 1 的预测值即指标层的 pred，其余依次小幅偏离
FORECAST_MODELS: tuple[str, ...] = ("LightGBM", "XGBoost", "RandomForest")

# SHAP 归因：(特征名, 影响力占比) —— 占比之和为 1
SHAP_FEATURES: tuple[tuple[str, float], ...] = (
    ("M2:同比", 0.42),
    ("PMI:新订单", 0.33),
    ("社会融资规模:同比", 0.25),
)

# 利率择时：(日期, 上行概率, 真实类别) —— 概率跨过 0.5 与阈值 0.4734，便于看分档差异
RATES_ROWS: tuple[tuple[str, float, int], ...] = (
    ("2026-09-15", 0.4210, 0),
    ("2026-09-16", 0.4880, 1),
    ("2026-09-17", 0.5310, 1),
    ("2026-09-18", 0.6120, 1),
    ("2026-09-19", 0.6580, 1),
)
RATES_THRESHOLD = 0.4734

# 研报观点：第 1 周 2 家机构，第 2 周 3 家（便于看周环比）
WEEKS: tuple[str, ...] = ("20260921-20260927", "20260928-20261004")
OPINIONS: tuple[tuple[str, str, str], ...] = (
    ("境内利率", "10年期国债", "看多"),
    ("全球股指", "上证指数/A股", "看跌"),
    ("宏观指标", "CPI:同比", "看多"),
)


def _rate_label(probability: float, threshold: float) -> tuple[int, str, int]:
    """按业务口径给 (strong_signal, signal_label, prediction)。

    ``signal_label`` 按 0.4/0.5/0.6 三档；``prediction`` 只按该条的 ``threshold`` 判定——
    两者口径不同，0.4734~0.5 之间会出现「prediction=1 但文字是震荡」的情形，这正是
    文档里点名的边界，演示数据故意覆盖到。
    """
    if probability >= 0.6:
        strong, label = 1, "强看上行"
    elif probability >= 0.5:
        strong, label = 0, "强看下行"
    elif probability >= 0.4:
        strong, label = -1, "震荡上行"
    else:
        strong, label = -2, "震荡下行"
    return strong, label, int(probability >= threshold)


def _forecast_rows() -> dict[str, list[tuple[Any, ...]]]:
    """宏观预测场景：1 报告 + 3 指标 + 每指标 3 模型预测 + 3 条 SHAP + 2 条入选特征。"""
    rows: dict[str, list[tuple[Any, ...]]] = {
        "report": [
            (
                REPORT_ID,
                "2026年9月国内宏观经济指标预测",
                FORECAST_MONTH,
                DATA_CUTOFF,
                GENERATED_AT,
                len(INDICATORS),
                "LightGBM/XGBoost/RandomForest 三模型集成",
                "增长与通胀小幅回升，信用扩张延续。",
                "done",
            )
        ],
        "indicator": [
            (iid, REPORT_ID, name, group, market, prior, pred, r2, zhangyu, 1)
            for iid, name, group, prior, pred, market, zhangyu, r2 in INDICATORS
        ],
        "prediction": [],
        "contribution": [],
        "selected_feature": [],
        "analysis": [
            (
                1,
                REPORT_ID,
                "SC_GLOBAL",
                None,
                "宏观综览：9月预测要点",
                json.dumps(
                    {
                        "summary": "增长与通胀小幅回升，信用扩张延续。",
                        "generated_at": GENERATED_AT,
                    },
                    ensure_ascii=False,
                ),
                "done",
                None,
                GENERATED_AT,
            )
        ],
    }

    prediction_id = 1
    contribution_id = 1
    selected_id = 1
    for iid, _name, _group, prior, pred, *_rest in INDICATORS:
        for rank, model in enumerate(FORECAST_MODELS, start=1):
            model_pred = round(pred + (rank - 1) * 0.02, 4)
            rows["prediction"].append(
                (prediction_id, iid, rank, model, model_pred, prior, round(model_pred - prior, 4))
            )
            prediction_id += 1

        # SHAP 只给排名 1 的模型，占比之和为 1
        for feature_rank, (feature, share) in enumerate(SHAP_FEATURES, start=1):
            rows["contribution"].append(
                (
                    contribution_id,
                    iid,
                    1,
                    FORECAST_MODELS[0],
                    feature_rank,
                    feature,
                    round(share * 10, 6),
                    share,
                )
            )
            contribution_id += 1

        for feature, _share in SHAP_FEATURES[:2]:
            rows["selected_feature"].append((selected_id, iid, feature))
            selected_id += 1

    return rows


def _rates_rows() -> dict[str, list[tuple[Any, ...]]]:
    """利率择时：1 次成功的 LATEST 运行 + 5 个交易日预测。"""
    run = (
        RUN_ID,
        "LATEST",
        "SUCCEEDED",
        "COMPLETED",
        None,
        "factors_demo.csv",
        "0" * 64,
        1024,
        None,
        1200,
        180,
        "2026-09-19",
        900,
        800,
        1000,
        120,
        1000,
        1200,
        "2026-09-19",
        3,
        json.dumps(["M2:同比", "PMI:新订单", "社会融资规模:同比"], ensure_ascii=False),
        json.dumps({"max_depth": 4, "learning_rate": 0.05}, ensure_ascii=False),
        RATES_THRESHOLD,
        "cfg-demo-hash",
        "code-demo-hash",
        None,
        "9月资金面转松，模型给出的上行概率持续抬升。",
        json.dumps(
            {"status": "done", "model": "demo-model", "generated_at": GENERATED_AT},
            ensure_ascii=False,
        ),
        GENERATED_AT,
        GENERATED_AT,
        GENERATED_AT,
    )

    predictions = []
    for index, (signal_date, probability, actual) in enumerate(RATES_ROWS, start=1):
        strong, label, prediction = _rate_label(probability, RATES_THRESHOLD)
        predictions.append(
            (
                index,
                signal_date,
                RUN_ID,
                probability,
                prediction,
                RATES_THRESHOLD,
                strong,
                label,
                "realized",
                actual,
                int(prediction == actual),
                "v1",
                "1",
                GENERATED_AT,
                GENERATED_AT,
            )
        )
    return {"run": [run], "prediction": predictions}


def _weekly_rows() -> dict[str, list[tuple[Any, ...]]]:
    """研报观点：两周的观点矩阵与机构宏观预测。"""
    institutions = ("中信证券", "中金公司", "华泰证券")
    opinions: list[tuple[Any, ...]] = []
    macro: list[tuple[Any, ...]] = []

    for index, week in enumerate(WEEKS):
        views = [
            {
                "institution": institution,
                "opinions": [
                    {"category": category, "asset": asset, "direction": direction}
                    for category, asset, direction in OPINIONS
                ],
            }
            for institution in institutions[: 2 + index]
        ]
        opinions.append(
            (
                week,
                json.dumps({"views": views}, ensure_ascii=False),
                f"## {week} 周度综述\n\n利率震荡，股指分歧加大。",
                GENERATED_AT,
            )
        )
        macro.append(
            (
                week,
                json.dumps(
                    {
                        "as_of": GENERATED_AT,
                        "items": [
                            {"indicator": "GDP:当季同比", "forecast": 4.9},
                            {"indicator": "CPI:当月同比", "forecast": 0.6},
                        ],
                    },
                    ensure_ascii=False,
                ),
                GENERATED_AT,
                2,
            )
        )

    return {"opinions": opinions, "macro": macro}


def _gold_rows() -> dict[str, list[tuple[Any, ...]]]:
    """黄金归因：2 个模型 × 30 个交易日，最后一天与文档示例完全一致。

    数值是收益率口径的小数（0.0185 = 1.85%）。越早的日期按比例略小，便于看趋势
    与环比；最后一天取文档给出的示例值，方便逐列核对真实口径。
    """
    end = date(2026, 9, 28)
    days = [(end - timedelta(days=29 - index)).isoformat() for index in range(30)]
    base: dict[str, tuple[float, ...]] = {
        "多元线性回归": (-0.0002, 0.0185, -0.0108, 0.0057, -0.0080, 0.0143),
        "随机森林": (0.0002, 0.0148, 0.0052, -0.0018, -0.0023, 0.0039),
    }

    rows: list[tuple[Any, ...]] = []
    row_id = 1
    for model, values in base.items():
        for index, day in enumerate(days):
            factor = 1.0 if index == len(days) - 1 else 0.6 + 0.4 * index / (len(days) - 1)
            rows.append((row_id, model, day, *(round(v * factor, 6) for v in values)))
            row_id += 1
    return {"main": rows}


def _treasury_1y_rows() -> dict[str, list[tuple[Any, ...]]]:
    """1 年期国债模型：35 个日期，完整结果放在 result_json 里。

    口径按文档：``prob_up_7d`` 等是 0—1 小数，``yield_pct`` 是百分数原值
    （1.2147 即 1.2147%）；每三个日期留一个 ``yield_change_2d_bp`` 为 null，
    用于验证 null 不会被当成 0。
    """
    end = date(2026, 10, 10)
    days = [(end - timedelta(days=34 - index)).isoformat() for index in range(35)]
    rows: list[tuple[Any, ...]] = []

    for index, day in enumerate(days):
        prob_up = round(0.40 + 0.01 * (index % 25), 6)
        payload = {
            "signal_date": day,
            "strategy_id": "curve_funding_7d_mtht_v3",
            "yield_pct": round(1.10 + 0.005 * (index % 20), 4),
            "yield_percentile_756": round(0.3 + 0.6 * (index % 10) / 10, 6),
            "prob_up_7d": prob_up,
            "prob_down_7d": round(1 - prob_up, 6),
            "trade_threshold": 0.65,
            "signal_blocked": 1 if index % 7 == 0 else 0,
            "production_guard": "保护条件触发：概率未达阈值" if index % 7 == 0 else "",
            "funding_signal": (-1, 0, 1)[index % 3],
            "funding_advice": "资金面偏松，可适度加仓" if index % 3 == 2 else "资金面中性，观望",
            "mt_signal": (-1, 0, 1)[index % 3],
            "ht_signal": (0, 1, -1)[index % 3],
            "technical_signal": (-1, 0, 1)[(index + 1) % 3],
            "technical_advice": "技术面偏多，趋势向上" if (index + 1) % 3 == 2 else "技术面中性",
            "yield_change_2d_bp": None if index % 3 == 0 else round(-3.5 + index % 7, 2),
            "funding_explanation": "资金利率下行，流动性宽松，对债市偏友好。",
            "technical_explanation": "均线多头排列，动量指标走强。",
            "funding_source": "资金面工作簿",
            "technical_source": "技术面工作簿",
        }
        rows.append((day, json.dumps(payload, ensure_ascii=False), GENERATED_AT, GENERATED_AT))

    return {"main": rows}


# ── 场景编排 ────────────────────────────────────────────────────────────
SCENES: dict[str, dict[str, Any]] = {
    "forecast": {
        "title": "宏观预测",
        "module": domestic_macro,
        "tables": (
            "report",
            "indicator",
            "prediction",
            "contribution",
            "selected_feature",
            "analysis",
        ),
        "builder": _forecast_rows,
    },
    "rates": {
        "title": "利率择时",
        "module": rates_timing,
        "tables": ("run", "prediction"),
        "builder": _rates_rows,
    },
    "weekly": {
        "title": "研报观点",
        "module": weekly_research,
        "tables": ("opinions", "macro"),
        "builder": _weekly_rows,
    },
    "gold": {
        "title": "黄金价格多因子归因",
        "module": gold_analysis,
        "tables": ("main",),
        "builder": _gold_rows,
    },
    "treasury_1y": {
        "title": "1 年期国债模型",
        "module": treasury_1y,
        "tables": ("main",),
        "builder": _treasury_1y_rows,
    },
}


def _apply_scene(cursor, key: str, args) -> None:
    scene = SCENES[key]
    module = scene["module"]
    print(f"\n── {scene['title']} ──")

    for statement in DDL[key]:
        cursor.execute(statement)
    tables = [module.table_ref(t) for t in scene["tables"]]
    print(f"  已确保 {len(tables)} 张表存在")

    # 防呆：正式库里这几张表存的是真实业务数据，而本脚本会先清空再写入
    occupied: dict[str, int] = {}
    for table in tables:
        cursor.execute(f"SELECT COUNT(*) AS c FROM {table}")
        count = int(cursor.fetchone()["c"])
        if count:
            occupied[table] = count

    if occupied and not args.force:
        print("  跳过：以下表里已有数据（本脚本会先清空）", file=sys.stderr)
        for table, count in occupied.items():
            print(f"    {table}: {count} 行", file=sys.stderr)
        print("    确认是测试库后加 --force 重跑", file=sys.stderr)
        return

    for table in tables:
        cursor.execute(f"DELETE FROM {table}")

    if args.drop:
        print("  已清空（--drop 生效，不写入）")
        return

    for table_key, values in scene["builder"]().items():
        if not values:
            continue
        table = module.table_ref(table_key)
        placeholders = ", ".join(["%s"] * len(values[0]))
        cursor.executemany(f"INSERT INTO {table} VALUES ({placeholders})", values)
        print(f"  {table:<52} 写入 {len(values)} 行")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scene",
        choices=(*SCENES, "all"),
        default="all",
        help="只处理某个场景（缺省全部）",
    )
    parser.add_argument("--drop", action="store_true", help="只清理演示数据，不写入")
    parser.add_argument(
        "--force",
        action="store_true",
        help="表里已有数据时也继续（会先清空！对正式库务必不要加）",
    )
    args = parser.parse_args(argv)

    keys = list(SCENES) if args.scene == "all" else [args.scene]
    print(f"目标库: {domestic_macro.schema_name() or '（当前连接的库）'}")

    with connection() as conn:
        with conn.cursor() as cursor:
            for key in keys:
                _apply_scene(cursor, key, args)

    print("\n完成。可用 `uv run python -m scripts.check_tables` 复核表是否齐全。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
