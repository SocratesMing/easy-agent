import logging
from datetime import datetime

logger = logging.getLogger("easy_agent.bloom")


def insert_bloom_data(
    db,
    bloom_code: str,
    bloom_code_cn: str,
    px_last: float,
    last_update: str,
    px_last_eod: float,
    last_update_eod: str,
    type_: str,
    region: str,
    bloom_date: str,
    sbm_time: int,
):
    creat_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """
            INSERT INTO fmqt_bloom (bloomCode, bloomCodeCN, pxLast, lastUpdate,
                                    pxLastEod, lastUpdateEod, type, region,
                                    bloomDate, sbmTime, creatDate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                bloom_code,
                bloom_code_cn,
                px_last,
                last_update,
                px_last_eod,
                last_update_eod,
                type_,
                region,
                bloom_date,
                sbm_time,
                creat_date,
            ),
        )
        logger.info(
            "彭博数据入库完成 index[%s] lastUpdate[%s] pxLast[%s] lastUpdateEod[%s] bloomDate[%s]",
            bloom_code_cn,
            last_update,
            px_last,
            last_update_eod,
            bloom_date,
        )


def query_bloom_by_type(
    db, type_: str, start_date: str, end_date: str, limit: int = None
):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        if limit:
            db._execute(
                cursor,
                """
                SELECT bloomCodeCN, bloomDate, pxLast
                FROM fmqt_bloom
                WHERE type = ? AND bloomDate >= ? AND bloomDate <= ?
                ORDER BY bloomDate DESC
                LIMIT ?
            """,
                (type_, start_date, end_date, limit),
            )
        else:
            db._execute(
                cursor,
                """
                SELECT bloomCodeCN, bloomDate, pxLast
                FROM fmqt_bloom
                WHERE type = ? AND bloomDate >= ? AND bloomDate <= ?
                ORDER BY bloomDate DESC
            """,
                (type_, start_date, end_date),
            )

        rows = cursor.fetchall()
        result = {}
        for row in rows:
            if db.db_type == "sqlite":
                name, date_val, value = (
                    row["bloomCodeCN"],
                    row["bloomDate"],
                    row["pxLast"],
                )
            else:
                name, date_val, value = (
                    row["bloomCodeCN"],
                    row["bloomDate"],
                    row["pxLast"],
                )
            if name not in result:
                result[name] = {}
            result[name][date_val] = str(value)
        return result


def insert_bloom_analysis(db, analysis_data: dict):
    creat_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """
            INSERT INTO fmqt_bloom_analysis (pair, signalLevel, signalSide,
                                             drive, contradict, operate,
                                             analysisDate, creatDate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                analysis_data.get("pair", ""),
                analysis_data.get("signalLevel", ""),
                analysis_data.get("signalSide", ""),
                analysis_data.get("drive", ""),
                analysis_data.get("contradict", ""),
                analysis_data.get("operate", ""),
                analysis_data.get("analysisDate", ""),
                creat_date,
            ),
        )
        logger.info(
            "彭博分析结果入库 pair[%s] signalLevel[%s]",
            analysis_data.get("pair"),
            analysis_data.get("signalLevel"),
        )


def query_bloom_analysis(db, analysis_date: str = None, limit: int = 50):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        if analysis_date:
            db._execute(
                cursor,
                """
                SELECT * FROM fmqt_bloom_analysis
                WHERE analysisDate = ?
                ORDER BY creatDate DESC
                LIMIT ?
            """,
                (analysis_date, limit),
            )
        else:
            db._execute(
                cursor,
                """
                SELECT * FROM fmqt_bloom_analysis
                ORDER BY creatDate DESC
                LIMIT ?
            """,
                (limit,),
            )

        rows = cursor.fetchall()
        if db.db_type == "sqlite":
            return [dict(row) for row in rows]
        return rows


def query_bloom_dashboard(db, type_: str, region: str):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """
            SELECT * FROM fmqt_bloom
            WHERE type = ? AND region = ?
            ORDER BY bloomDate DESC
            LIMIT 2
        """,
            (type_, region),
        )
        rows = cursor.fetchall()
        if db.db_type == "sqlite":
            rows = [dict(row) for row in rows]
        else:
            rows = list(rows)

        if not rows:
            return {
                "type": type_,
                "region": region,
                "innersource": "外部数据管理系统",
                "outsource": "彭博",
            }

        data = dict(rows[0])
        if len(rows) == 2 and rows[1].get("pxLast", 0) != 0:
            data["growthRate"] = (
                rows[0].get("pxLast", 0) - rows[1].get("pxLast", 0)
            ) / rows[1].get("pxLast", 1)
        else:
            data["growthRate"] = 0
        data["innersource"] = "外部数据管理系统"
        data["outsource"] = "彭博"
        return data


def query_bloom_stock_index(db, type_: str, bloom_code_cn: str):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """
            SELECT * FROM fmqt_bloom
            WHERE type = ? AND bloomCodeCN = ?
            ORDER BY bloomDate DESC
            LIMIT 2
        """,
            (type_, bloom_code_cn),
        )
        rows = cursor.fetchall()
        if db.db_type == "sqlite":
            rows = [dict(row) for row in rows]
        else:
            rows = list(rows)

        if not rows:
            return {"type": type_, "region": bloom_code_cn}

        data = dict(rows[0])
        if len(rows) == 2 and rows[1].get("pxLast", 0) != 0:
            data["growthRate"] = (
                rows[0].get("pxLast", 0) - rows[1].get("pxLast", 0)
            ) / rows[1].get("pxLast", 1)
        else:
            data["growthRate"] = 0
        data["region"] = data.get("bloomCodeCN", bloom_code_cn)
        data["innersource"] = "外部数据管理系统"
        data["outsource"] = "彭博"
        return data


def query_bloom_chart(db, type_: str, region: str, start_date: str, end_date: str):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """
            SELECT bloomDate, pxLastEod FROM fmqt_bloom
            WHERE type = ? AND region = ?
              AND bloomDate >= ? AND bloomDate <= ?
            ORDER BY bloomDate ASC
        """,
            (type_, region, start_date, end_date),
        )
        rows = cursor.fetchall()
        if db.db_type == "sqlite":
            rows = [dict(row) for row in rows]
        else:
            rows = [{"bloomDate": r[0], "pxLastEod": r[1]} for r in rows]

        if not rows:
            return []

        base_px = rows[0]["pxLastEod"]
        result = []
        for row in rows:
            item = dict(row)
            if type_ in ("即期汇率", "CPI") and base_px != 0:
                item["normalpx"] = round((item["pxLastEod"] / base_px) * 100, 3)
            else:
                item["normalpx"] = item["pxLastEod"]
            result.append(item)
        return result


def query_bloom_stock_index_chart(
    db, type_: str, bloom_code_cn: str, start_date: str, end_date: str
):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """
            SELECT bloomDate, pxLastEod FROM fmqt_bloom
            WHERE type = ? AND bloomCodeCN = ?
              AND bloomDate >= ? AND bloomDate <= ?
            ORDER BY bloomDate ASC
        """,
            (type_, bloom_code_cn, start_date, end_date),
        )
        rows = cursor.fetchall()
        if db.db_type == "sqlite":
            rows = [dict(row) for row in rows]
        else:
            rows = [{"bloomDate": r[0], "pxLastEod": r[1]} for r in rows]

        if not rows:
            return []

        base_px = rows[0]["pxLastEod"]
        result = []
        for row in rows:
            item = dict(row)
            if type_ in ("即期汇率", "CPI") and base_px != 0:
                item["normalpx"] = round((item["pxLastEod"] / base_px) * 100, 3)
            else:
                item["normalpx"] = item["pxLastEod"]
            result.append(item)
        return result


def query_bloom_analysis_by_pair(db, pair: str, start_date: str, end_date: str):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """
            SELECT * FROM fmqt_bloom_analysis
            WHERE pair = ? AND analysisDate >= ? AND analysisDate <= ?
            ORDER BY analysisDate DESC
        """,
            (pair, start_date, end_date),
        )
        rows = cursor.fetchall()
        if db.db_type == "sqlite":
            return [dict(row) for row in rows]
        return list(rows)


def upsert_bloom_analysis(db, data: dict):
    creat_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pair = data.get("pair", "")
    analysis_date = data.get("analysisDate", "")

    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """
            SELECT id FROM fmqt_bloom_analysis
            WHERE pair = ? AND analysisDate = ?
        """,
            (pair, analysis_date),
        )
        existing = cursor.fetchone()

        if existing:
            db._execute(
                cursor,
                """
                UPDATE fmqt_bloom_analysis
                SET signalLevel = ?, signalSide = ?, drive = ?,
                    contradict = ?, operate = ?, creatDate = ?
                WHERE pair = ? AND analysisDate = ?
            """,
                (
                    data.get("signalLevel", ""),
                    data.get("signalSide", ""),
                    data.get("drive", ""),
                    data.get("contradict", ""),
                    data.get("operate", ""),
                    creat_date,
                    pair,
                    analysis_date,
                ),
            )
            logger.info(
                "更新彭博分析结果 pair[%s] analysisDate[%s]", pair, analysis_date
            )
        else:
            db._execute(
                cursor,
                """
                INSERT INTO fmqt_bloom_analysis (pair, signalLevel, signalSide,
                                                 drive, contradict, operate,
                                                 analysisDate, creatDate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    pair,
                    data.get("signalLevel", ""),
                    data.get("signalSide", ""),
                    data.get("drive", ""),
                    data.get("contradict", ""),
                    data.get("operate", ""),
                    analysis_date,
                    creat_date,
                ),
            )
            logger.info(
                "新增彭博分析结果 pair[%s] analysisDate[%s]", pair, analysis_date
            )
