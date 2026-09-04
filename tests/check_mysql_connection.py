#!/usr/bin/env python3
"""Manually verify the MySQL connection configured for Easy Agent.

Examples:
    python tests/check_mysql_connection.py
    python tests/check_mysql_connection.py --config easy_agent/config/config.prod.yaml
    python tests/check_mysql_connection.py --create-database
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import pymysql
import yaml

from easy_agent.config import Config


def resolve_config_path(config_arg: str | None) -> Path:
    return Config.resolve_config_path(config_arg)


def load_mysql_config(config_path: Path) -> dict[str, Any]:
    with config_path.open(encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    database = data.get("database", {})
    if not isinstance(database, dict) or database.get("type") != "mysql":
        raise ValueError("配置文件未启用 MySQL：database.type 必须是 mysql")

    mysql_config = database.get("mysql", {})
    if not isinstance(mysql_config, dict):
        raise ValueError("配置文件缺少 database.mysql 配置")
    return mysql_config


def failure_hint(error: pymysql.err.OperationalError) -> str:
    code = error.args[0] if error.args else None
    if code == 1045:
        return "用户名或密码错误，或者 MySQL 未授权当前来源主机"
    if code == 1049:
        return "数据库不存在，可使用 --create-database 让脚本创建"
    if code == 2003:
        return "无法建立 TCP 连接，请检查 host/port 和防火墙"
    if code == 2013:
        return "连接过程中断，请检查网络和 MySQL 服务状态"
    return "请检查 MySQL 配置和服务状态"


def check_connection(config_path: Path, create_database: bool) -> int:
    mysql_config = load_mysql_config(config_path)
    host = mysql_config.get("host", "127.0.0.1")
    port = int(mysql_config.get("port", 3306))
    user = mysql_config.get("user", "root")
    password = mysql_config.get("password", "")
    database = mysql_config.get("database", "easy_agent")
    charset = mysql_config.get("charset", "utf8mb4")
    connect_timeout = int(mysql_config.get("connect_timeout", 10))

    print(f"配置文件: {config_path}")
    print(f"目标: {user}@{host}:{port}/{database}")
    print(f"密码状态: {'已配置' if password else '为空'}")

    try:
        server_connection = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            charset=charset,
            connect_timeout=connect_timeout,
        )
    except pymysql.err.OperationalError as error:
        print(f"❌ MySQL 认证/连接失败: {error}")
        print(f"排查建议: {failure_hint(error)}")
        return 1

    try:
        with server_connection.cursor() as cursor:
            cursor.execute("SELECT VERSION(), CURRENT_USER()")
            server_version, authenticated_user = cursor.fetchone()
            print(f"✅ 服务端认证成功 | MySQL: {server_version} | 登录身份: {authenticated_user}")

            cursor.execute("SHOW DATABASES LIKE %s", (database,))
            database_exists = cursor.fetchone() is not None
            if not database_exists:
                if not create_database:
                    print(f"❌ 数据库不存在: {database}")
                    print("排查建议: 手动创建数据库，或使用 --create-database")
                    return 1
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{database}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
                print(f"✅ 已创建数据库: {database}")

        server_connection.select_db(database)
        with server_connection.cursor() as cursor:
            cursor.execute("SELECT 1, DATABASE()")
            result, current_database = cursor.fetchone()
            if result != 1 or current_database != database:
                raise RuntimeError("数据库查询结果不符合预期")

        print("✅ 目标数据库可访问，SELECT 查询正常")
        return 0
    except pymysql.err.OperationalError as error:
        print(f"❌ 目标数据库访问失败: {error}")
        print(f"排查建议: {failure_hint(error)}")
        return 1
    finally:
        server_connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 Easy Agent 的 MySQL 配置")
    parser.add_argument(
        "--config",
        help="YAML 配置路径；默认使用 EASY_CONFIG 或 AGENT_ENV 对应的环境配置",
    )
    parser.add_argument(
        "--create-database",
        action="store_true",
        help="如果目标数据库不存在则创建它",
    )
    args = parser.parse_args()

    config_path = resolve_config_path(args.config)
    if not config_path.exists():
        print(f"❌ 配置文件不存在: {config_path}")
        return 2

    try:
        return check_connection(config_path, args.create_database)
    except (ValueError, OSError) as error:
        print(f"❌ 配置加载失败: {error}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
