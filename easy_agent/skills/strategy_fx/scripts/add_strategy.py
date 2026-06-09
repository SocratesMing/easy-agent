"""
add_strategy.py
调用SBM新增策略接口，将策略记录添加到策略列表中

用法:
    python add_strategy.py --strategy-name Boll_06 --biz-type FX --user-id szm
"""
import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
import yaml


def load_config() -> dict:
    """加载config.yaml配置"""
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(strategy_name: str, workspace_path: str, config: dict) -> logging.Logger:
    """配置日志到 skill_log/skill.log"""
    log_config = config.get("logging", {})
    log_dir = Path(workspace_path) / strategy_name / "skill_log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "skill.log"

    logger = logging.getLogger("add_strategy")
    logger.setLevel(getattr(logging, log_config.get("level", "INFO")))

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(log_config.get("format", "%(asctime)s [%(levelname)s] %(message)s")))

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(log_config.get("format", "%(asctime)s [%(levelname)s] %(message)s")))

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


def read_plan_content(strategy_name: str, plan_name: str, workspace_path: str) -> Optional[dict]:
    """读取策略plan文件内容"""
    plan_dir = Path(workspace_path) / strategy_name / "config" / "plan"
    plan_file = plan_dir / f"{plan_name}.json"
    if plan_file.exists():
        with open(plan_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def add_strategy(strategy_name: str, workspace_path: str, biz_type: str = "FX",
                 user_id: str = "default", plan_name: str = "默认方案",
                 desc: str = "") -> dict:
    """
    调用SBM接口新增策略记录

    Args:
        strategy_name: 策略名称
        workspace_path: 策略工作目录根路径
        biz_type: 业务类型（默认FX）
        user_id: 用户ID
        plan_name: 方案名称
        desc: 策略描述

    Returns:
        API响应结果
    """
    config = load_config()
    logger = setup_logging(strategy_name, workspace_path, config)
    logger.info("=" * 60)
    logger.info("开始新增策略记录: %s", strategy_name)

    # 构建请求
    base_url = config["service"]["base_url"]
    api_path = config["strategy"]["add"]
    url = f"{base_url}{api_path}"

    # 读取plan内容
    plan_content = read_plan_content(strategy_name, plan_name, workspace_path)

    payload = {
        "strategyName": strategy_name,
        "bizType": biz_type,
        "userId": user_id,
        "plan": plan_name,
        "planContent": plan_content,
        "desc": desc or f"{strategy_name} 外汇策略",
        "createTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    logger.info("请求URL: %s", url)
    logger.info("请求体: %s", json.dumps(payload, ensure_ascii=False, indent=2))

    try:
        resp = requests.post(url, json=payload, timeout=30)
        logger.info("响应状态码: %d", resp.status_code)
        logger.info("响应体: %s", resp.text)

        if resp.status_code == 200:
            result = resp.json()
            if result.get("code") == 0:
                logger.info("策略记录添加成功: %s", strategy_name)
            else:
                logger.error("策略记录添加失败: %s", result.get("message", "未知错误"))
            logger.info("=" * 60)
            return result
        else:
            logger.error("HTTP请求失败: %d", resp.status_code)
            logger.info("=" * 60)
            return {"code": -1, "message": f"HTTP {resp.status_code}: {resp.text}"}

    except requests.exceptions.RequestException as e:
        logger.error("请求异常: %s", str(e))
        logger.info("=" * 60)
        return {"code": -1, "message": str(e)}


def main():
    parser = argparse.ArgumentParser(description="调用SBM接口新增策略记录")
    parser.add_argument("--strategy-name", type=str, required=True, help="策略名称")
    parser.add_argument("--workspace-path", type=str, required=True, help="策略工作目录根路径")
    parser.add_argument("--biz-type", type=str, default="FX", help="业务类型（默认FX）")
    parser.add_argument("--user-id", type=str, default="default", help="用户ID")
    parser.add_argument("--plan-name", type=str, default="默认方案", help="方案名称")
    parser.add_argument("--desc", type=str, default="", help="策略描述")
    args = parser.parse_args()

    result = add_strategy(args.strategy_name, args.workspace_path, args.biz_type,
                          args.user_id, args.plan_name, args.desc)
    if result.get("code") == 0:
        print(f"SUCCESS: 策略 {args.strategy_name} 已添加到策略列表")
    else:
        print(f"ERROR: 添加失败 - {result.get('message', '未知错误')}")
        sys.exit(1)


if __name__ == "__main__":
    main()