"""下载贵金属和外汇行情数据到 MySQL 数据库

数据源: akshare
- 贵金属: 国际黄金(XAU)、国际白银(XAG)、国际铂金(XPT)、国际钯金(XPD)、
          COMEX黄金(GC)、COMEX白银(SI)
- 外汇: 美元、欧元、英镑、日元、港币、澳大利亚元、加拿大元、瑞士法郎、新加坡元

用法:
    python tests/download_market_data.py              # 下载最近一个月数据
    python tests/download_market_data.py --days 90    # 下载最近90天数据
    python tests/download_market_data.py --start 2026-01-01 --end 2026-05-11  # 指定日期范围
"""

import argparse
import sys
from datetime import datetime, timedelta

import pandas as pd
import pymysql
from dbutils.pooled_db import PooledDB

PRECIOUS_METALS_SYMBOLS = {
    "XAU": "国际黄金",
    "XAG": "国际白银",
    "XPT": "国际铂金",
    "XPD": "国际钯金",
    "GC": "COMEX黄金",
    "SI": "COMEX白银",
}

FOREX_CURRENCIES = [
    "美元",
    "欧元",
    "英镑",
    "日元",
    "港币",
    "澳大利亚元",
    "加拿大元",
    "瑞士法郎",
    "新加坡元",
]

MYSQL_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "Test1234",
    "database": "agent",
    "charset": "utf8mb4",
}


def get_pool():
    return PooledDB(
        creator=pymysql,
        maxconnections=5,
        mincached=1,
        maxcached=3,
        blocking=True,
        host=MYSQL_CONFIG["host"],
        port=MYSQL_CONFIG["port"],
        user=MYSQL_CONFIG["user"],
        password=MYSQL_CONFIG["password"],
        database=MYSQL_CONFIG["database"],
        charset=MYSQL_CONFIG["charset"],
        cursorclass=pymysql.cursors.DictCursor,
    )


def init_tables(pool):
    with pool.connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS precious_metals (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL COMMENT '品种代码',
                    date DATE NOT NULL COMMENT '日期',
                    open DECIMAL(20,4) COMMENT '开盘价',
                    high DECIMAL(20,4) COMMENT '最高价',
                    low DECIMAL(20,4) COMMENT '最低价',
                    close DECIMAL(20,4) COMMENT '收盘价',
                    volume BIGINT COMMENT '成交量',
                    position BIGINT COMMENT '持仓量',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uk_symbol_date (symbol, date)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='贵金属行情数据'
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS forex_rates (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    currency VARCHAR(20) NOT NULL COMMENT '货币名称',
                    date DATE NOT NULL COMMENT '日期',
                    buy_price DECIMAL(20,4) COMMENT '中行汇买价',
                    cash_buy_price DECIMAL(20,4) COMMENT '中行钞买价',
                    sell_price DECIMAL(20,4) COMMENT '中行钞卖价/汇卖价',
                    central_parity DECIMAL(20,4) COMMENT '央行中间价',
                    boc_rate DECIMAL(20,4) COMMENT '中行折算价',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uk_currency_date (currency, date)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='外汇牌价数据'
            """)
            conn.commit()


def get_existing_dates(pool, table, key_column, key_value):
    with pool.connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"SELECT date FROM {table} WHERE {key_column} = %s",
                (key_value,),
            )
            return {row["date"] for row in cursor.fetchall()}


def _nan_to_none(val):
    if isinstance(val, float) and pd.isna(val):
        return None
    return val


def download_precious_metals(pool, start_date, end_date):
    import akshare as ak

    print(f"\n{'='*60}")
    print(f"下载贵金属数据 ({start_date} ~ {end_date})")
    print(f"{'='*60}")

    for symbol, name in PRECIOUS_METALS_SYMBOLS.items():
        try:
            existing = get_existing_dates(pool, "precious_metals", "symbol", symbol)
            df = ak.futures_foreign_hist(symbol=symbol)
            df["date"] = pd.to_datetime(df["date"]).dt.date

            mask = (df["date"] >= start_date) & (df["date"] <= end_date)
            df_filtered = df[mask].copy()

            if df_filtered.empty:
                print(f"  [{symbol}] {name}: 指定范围内无数据")
                continue

            new_rows = df_filtered[~df_filtered["date"].isin(existing)]

            if new_rows.empty:
                print(f"  [{symbol}] {name}: {len(df_filtered)} 条数据已存在，跳过")
                continue

            records = []
            for _, row in new_rows.iterrows():
                records.append((
                    symbol,
                    row["date"],
                    _nan_to_none(row.get("open")),
                    _nan_to_none(row.get("high")),
                    _nan_to_none(row.get("low")),
                    _nan_to_none(row.get("close")),
                    _nan_to_none(row.get("volume", 0)),
                    _nan_to_none(row.get("position", 0)),
                ))

            with pool.connection() as conn:
                with conn.cursor() as cursor:
                    cursor.executemany(
                        """INSERT IGNORE INTO precious_metals
                           (symbol, date, open, high, low, close, volume, position)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        records,
                    )
                    conn.commit()
                    print(f"  [{symbol}] {name}: 新增 {len(records)} 条数据")
        except Exception as e:
            print(f"  [{symbol}] {name}: 下载失败 - {e}")


def download_forex(pool, start_date, end_date):
    import akshare as ak

    start_str = start_date.strftime("%Y%m%d")
    end_str = end_date.strftime("%Y%m%d")

    print(f"\n{'='*60}")
    print(f"下载外汇牌价数据 ({start_date} ~ {end_date})")
    print(f"{'='*60}")

    for currency in FOREX_CURRENCIES:
        try:
            existing = get_existing_dates(pool, "forex_rates", "currency", currency)
            df = ak.currency_boc_sina(
                symbol=currency, start_date=start_str, end_date=end_str
            )
            df["日期"] = pd.to_datetime(df["日期"]).dt.date

            new_rows = df[~df["日期"].isin(existing)]

            if new_rows.empty:
                print(f"  [{currency}]: {len(df)} 条数据已存在，跳过")
                continue

            records = []
            for _, row in new_rows.iterrows():
                records.append((
                    currency,
                    row["日期"],
                    _nan_to_none(row.get("中行汇买价")),
                    _nan_to_none(row.get("中行钞买价")),
                    _nan_to_none(row.get("中行钞卖价/汇卖价")),
                    _nan_to_none(row.get("央行中间价")),
                    _nan_to_none(row.get("中行折算价")),
                ))

            with pool.connection() as conn:
                with conn.cursor() as cursor:
                    cursor.executemany(
                        """INSERT IGNORE INTO forex_rates
                           (currency, date, buy_price, cash_buy_price, sell_price,
                            central_parity, boc_rate)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        records,
                    )
                    conn.commit()
                    print(f"  [{currency}]: 新增 {len(records)} 条数据")
        except Exception as e:
            print(f"  [{currency}]: 下载失败 - {e}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="下载贵金属和外汇行情数据到 MySQL 数据库"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="下载最近 N 天的数据 (默认: 30)",
    )
    parser.add_argument("--start", type=str, help="开始日期 (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, help="结束日期 (YYYY-MM-DD)")
    parser.add_argument(
        "--skip-metals", action="store_true", help="跳过贵金属数据下载"
    )
    parser.add_argument(
        "--skip-forex", action="store_true", help="跳过外汇数据下载"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.start and args.end:
        start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
        end_date = datetime.strptime(args.end, "%Y-%m-%d").date()
    else:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=args.days)

    if start_date > end_date:
        print("错误: 开始日期不能晚于结束日期")
        sys.exit(1)

    print(f"数据范围: {start_date} ~ {end_date}")
    print(f"MySQL: {MYSQL_CONFIG['host']}:{MYSQL_CONFIG['port']}/{MYSQL_CONFIG['database']}")

    pool = get_pool()
    init_tables(pool)

    if not args.skip_metals:
        download_precious_metals(pool, start_date, end_date)

    if not args.skip_forex:
        download_forex(pool, start_date, end_date)

    print(f"\n{'='*60}")
    print("下载完成!")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()