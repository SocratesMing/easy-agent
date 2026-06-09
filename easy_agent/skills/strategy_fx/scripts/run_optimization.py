"""
run_optimization.py
发起参数寻优任务并监控进度

用法:
    python run_optimization.py --strategy-name Boll_06 --plan-name 默认方案 \
        --opt-params '[{"name":"ma_period","min":5,"max":50,"step":1,"type":"discrete"}]' \
        --opt-count 100 --user-id szm
"""
import argparse
import json
import logging
import sys
import time
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


def setup_logging(strategy_name: str, workspace_path: str, config: dict, run_id: str = "") -> logging.Logger:
    """配置日志"""
    log_config = config.get("logging", {})
    log_dir = Path(workspace_path) / strategy_name
    log_dir.mkdir(parents=True, exist_ok=True)
    log_suffix = f"optimize_{run_id}" if run_id else f"optimize_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    log_file = log_dir / f"{log_suffix}.log"

    logger = logging.getLogger(f"run_optimization_{run_id}")
    logger.setLevel(getattr(logging, log_config.get("level", "INFO")))

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(log_config.get("format", "%(asctime)s [%(levelname)s] %(message)s")))

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(log_config.get("format", "%(asctime)s [%(levelname)s] %(message)s")))

    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.info("日志文件: %s", log_file)
    return logger


def read_plan_content(strategy_name: str, plan_name: str, workspace_path: str) -> Optional[dict]:
    """读取策略plan文件内容"""
    plan_dir = Path(workspace_path) / strategy_name / "config" / "plan"
    plan_file = plan_dir / f"{plan_name}.json"
    if plan_file.exists():
        with open(plan_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def start_optimization(strategy_name: str, plan_name: str, user_id: str,
                       opt_params: list, opt_count: int,
                       config: dict, logger: logging.Logger,
                       workspace_path: str) -> Optional[str]:
    """
    发起寻优任务

    Args:
        strategy_name: 策略名称
        plan_name: 方案名称
        user_id: 用户ID
        opt_params: 寻优参数列表 [{"name":"xxx","min":1,"max":10,"step":1,"type":"discrete"}]
        opt_count: 寻优次数
        config: 配置
        logger: 日志器

    Returns:
        成功返回run_id，失败返回None
    """
    base_url = config["optimize"]["base_url"]
    api_path = config["optimize"]["start"]
    url = f"{base_url}{api_path}"

    plan_content = read_plan_content(strategy_name, plan_name, workspace_path)
    if plan_content is None:
        logger.error("找不到方案文件: %s/%s", strategy_name, plan_name)
        return None

    run_id = f"opt_{strategy_name}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

    # 在plan_content中注入优化参数
    if "config" not in plan_content:
        plan_content["config"] = {}
    plan_content["config"]["optimize"] = {
        "param": [opt_params],
        "count": opt_count,
    }

    payload = {
        "param": {
            "runId": run_id,
            "strategyName": strategy_name,
            "strategyVersion": "v1",
            "userId": user_id,
            "folder": strategy_name,
            "book": strategy_name,
            "bizType": "FX",
            "plan": plan_name,
            "planContent": plan_content,
            "sha1": "",
        },
        "backType": 1,  # 1=寻优模式
        "hostName": "fast-backtest-local",
    }

    logger.info("发起寻优请求: %s", url)
    logger.info("寻优参数: %s", json.dumps(opt_params, ensure_ascii=False))
    logger.info("寻优次数: %d", opt_count)
    logger.info("runId: %s", run_id)

    try:
        resp = requests.post(url, json=payload, timeout=30)
        logger.info("响应状态码: %d", resp.status_code)
        logger.info("响应体: %s", resp.text)

        if resp.status_code == 200:
            result = resp.json()
            if result.get("code") == 0:
                logger.info("寻优任务已发起: runId=%s", run_id)
                return run_id
            else:
                logger.error("寻优发起失败: %s", result.get("message", "未知错误"))
                return None
        else:
            logger.error("HTTP请求失败: %d - %s", resp.status_code, resp.text)
            return None

    except requests.exceptions.RequestException as e:
        logger.error("请求异常: %s", str(e))
        return None


def poll_optimization_progress(run_id: str, config: dict, logger: logging.Logger,
                               max_retries: int = 0, timeout: int = 0,
                               poll_interval: int = 5) -> Optional[dict]:
    """
    轮询寻优进度

    Args:
        run_id: 寻优运行ID
        config: 配置
        logger: 日志器
        max_retries: 最大轮询次数（0表示不限制）
        timeout: 超时时间秒（0表示不限制）
        poll_interval: 轮询间隔秒

    Returns:
        最终进度数据
    """
    base_url = config["optimize"]["base_url"]
    api_path = config["optimize"]["progress"].format(run_id=run_id)
    url = f"{base_url}{api_path}"

    start_time = time.time()
    retry_count = 0
    last_percent = -1

    logger.info("开始轮询寻优进度: runId=%s, interval=%ds", run_id, poll_interval)

    while True:
        # 超时检查（寻优通常较慢，默认超时设为2小时）
        effective_timeout = timeout if timeout > 0 else 7200
        if (time.time() - start_time) > effective_timeout:
            logger.error("寻优轮询超时: runId=%s, 已等待%ds", run_id, effective_timeout)
            return {"runId": run_id, "percent": -1, "err_msg": "寻优轮询超时"}

        if max_retries > 0 and retry_count >= max_retries:
            logger.error("寻优轮询达到最大次数: runId=%s", run_id)
            return {"runId": run_id, "percent": -1, "err_msg": "达到最大轮询次数"}

        try:
            resp = requests.get(url, timeout=10)
            logger.info("寻优进度查询 [%d]: HTTP %d", retry_count + 1, resp.status_code)

            if resp.status_code == 200:
                result = resp.json()
                data = result.get("data", {})

                if data:
                    percent = data.get("percent", 0)
                    err_msg = data.get("err_msg", "")

                    if percent != last_percent:
                        logger.info("寻优进度: %.1f%%", percent * 100)
                        last_percent = percent

                    if percent >= 1.0:
                        logger.info("寻优完成: runId=%s", run_id)
                        return data

                    if err_msg:
                        logger.error("寻优出错: %s", err_msg)
                        return data

            elif resp.status_code == 404:
                logger.info("进度文件尚未生成，继续等待...")

        except requests.exceptions.RequestException as e:
            logger.warning("寻优进度查询异常: %s", str(e))

        retry_count += 1
        time.sleep(poll_interval)


def get_optimization_result(run_id: str, config: dict,
                            logger: logging.Logger) -> Optional[dict]:
    """获取寻优最终结果"""
    base_url = config["optimize"]["base_url"]
    api_path = config["optimize"]["result"].format(run_id=run_id)
    url = f"{base_url}{api_path}"

    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            result = resp.json()
            logger.info("寻优结果: %s", json.dumps(result, ensure_ascii=False, indent=2))
            return result
        else:
            logger.warning("获取寻优结果失败: HTTP %d", resp.status_code)
            return None
    except requests.exceptions.RequestException as e:
        logger.error("获取寻优结果异常: %s", str(e))
        return None


def run_optimization(strategy_name: str, workspace_path: str, plan_name: str = "默认方案",
                     user_id: str = "default",
                     opt_params: Optional[list] = None,
                     opt_count: int = 100) -> dict:
    """
    完整的寻优流程：发起寻优 -> 轮询进度 -> 返回结果

    Args:
        strategy_name: 策略名称
        workspace_path: 策略工作目录根路径
        plan_name: 方案名称
        user_id: 用户ID
        opt_params: 寻优参数范围
        opt_count: 寻优次数

    Returns:
        {"success": bool, "run_id": str, "percent": float, "err_msg": str, "result": dict}
    """
    config = load_config()
    logger = setup_logging(strategy_name, workspace_path, config)

    logger.info("=" * 60)
    logger.info("开始寻优流程: 策略=%s, 方案=%s, 次数=%d", strategy_name, plan_name, opt_count)

    if opt_params is None:
        logger.error("未指定寻优参数范围")
        return {"success": False, "run_id": "", "percent": 0,
                "err_msg": "未指定寻优参数范围，请通过 --opt-params 指定"}

    # 1. 发起寻优
    run_id = start_optimization(strategy_name, plan_name, user_id,
                                opt_params, opt_count, config, logger, workspace_path)
    if run_id is None:
        logger.error("寻优发起失败")
        return {"success": False, "run_id": "", "percent": 0, "err_msg": "寻优发起失败"}

    logger = setup_logging(strategy_name, workspace_path, config, run_id)
    logger.info("寻优已发起: runId=%s", run_id)

    # 2. 轮询进度（寻优较慢，轮询间隔默认5秒）
    final_data = poll_optimization_progress(run_id, config, logger,
                                            poll_interval=5)

    # 3. 获取结果
    if final_data:
        percent = final_data.get("percent", 0)
        err_msg = final_data.get("err_msg", "")

        if percent >= 1.0 and not err_msg:
            logger.info("寻优成功完成: runId=%s", run_id)
            opt_result = get_optimization_result(run_id, config, logger)
            result = {"success": True, "run_id": run_id, "percent": percent,
                      "err_msg": "", "result": opt_result}
        elif err_msg:
            logger.error("寻优出错: %s", err_msg)
            result = {"success": False, "run_id": run_id, "percent": percent,
                      "err_msg": err_msg, "result": None}
        else:
            logger.warning("寻优未完成: percent=%.1f%%", percent * 100)
            result = {"success": False, "run_id": run_id, "percent": percent,
                      "err_msg": "寻优未正常完成", "result": None}
    else:
        result = {"success": False, "run_id": run_id, "percent": 0,
                  "err_msg": "无法获取寻优结果", "result": None}

    logger.info("寻优流程结束: success=%s", result["success"])
    logger.info("=" * 60)
    return result


def main():
    parser = argparse.ArgumentParser(description="发起参数寻优并监控进度")
    parser.add_argument("--strategy-name", type=str, required=True, help="策略名称")
    parser.add_argument("--workspace-path", type=str, required=True, help="策略工作目录根路径")
    parser.add_argument("--plan-name", type=str, default="默认方案", help="方案名称")
    parser.add_argument("--user-id", type=str, default="default", help="用户ID")
    parser.add_argument("--opt-params", type=str, required=True,
                        help='寻优参数JSON，如 [{"name":"ma_period","min":5,"max":50,"step":1,"type":"discrete"}]')
    parser.add_argument("--opt-count", type=int, default=100, help="寻优次数（默认100）")
    args = parser.parse_args()

    try:
        opt_params = json.loads(args.opt_params)
    except json.JSONDecodeError as e:
        print(f"ERROR: 寻优参数JSON解析失败: {e}")
        sys.exit(1)

    result = run_optimization(args.strategy_name, args.workspace_path, args.plan_name,
                              args.user_id, opt_params, args.opt_count)
    print(json.dumps(result, ensure_ascii=False))

    if result["success"]:
        print(f"\nSUCCESS: 寻优完成 runId={result['run_id']}")
    else:
        print(f"\nERROR: 寻优失败 - {result['err_msg']}")
        sys.exit(1)


if __name__ == "__main__":
    main()