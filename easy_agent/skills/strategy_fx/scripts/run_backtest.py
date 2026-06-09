"""
run_backtest.py
发起回测并定时查询回测进度

用法:
    python run_backtest.py --strategy-name Boll_06 --plan-name 默认方案 --user-id szm
"""
import argparse
import json
import logging
import sys
import time
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


def load_hostname() -> str:
    """从项目根目录 cfg.env 中读取 HOSTNAME"""
    env_path = Path(__file__).resolve().parent.parent.parent.parent.parent / "cfg.env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and line.startswith("HOSTNAME="):
                    return line.split("=", 1)[1].strip()
    return "fmqt-pm-sbp-fast_backtest"


def setup_logging(strategy_name: str, workspace_path: str, config: dict, run_id: str = "") -> logging.Logger:
    """配置日志到 skill_log/skill.log"""
    log_config = config.get("logging", {})
    log_dir = Path(workspace_path) / strategy_name / "skill_log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "skill.log"

    logger = logging.getLogger(f"run_backtest_{run_id}") if run_id else logging.getLogger("run_backtest")
    logger.setLevel(getattr(logging, log_config.get("level", "INFO")))

    # 清除已有handlers，避免重复
    logger.handlers.clear()

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(log_config.get("format", "%(asctime)s [%(levelname)s] %(message)s")))

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(log_config.get("format", "%(asctime)s [%(levelname)s] %(message)s")))

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


def start_backtest(strategy_name: str, plan_name: str, user_id: str,
                   config: dict, logger: logging.Logger) -> Optional[str]:
    """
    发起回测任务 - 只需传策略名和方案名，后端自动解析方案参数

    Returns:
        成功返回run_id，失败返回None
    """
    base_url = config["service"]["base_url"]
    api_path = config["backtest"]["start"]
    url = f"{base_url}{api_path}"

    hostname = load_hostname()
    payload = {
        "strategyName": strategy_name,
        "plan": plan_name,
        "userId": user_id,
        "backType": 3,
        "hostName": hostname,
    }

    logger.info("发起回测请求: %s", url)
    logger.info("请求参数: strategyName=%s, plan=%s, userId=%s", strategy_name, plan_name, user_id)

    try:
        resp = requests.post(url, json=payload, timeout=30)
        logger.info("响应状态码: %d", resp.status_code)
        logger.info("响应体: %s", resp.text)

        if resp.status_code == 200:
            result = resp.json()
            if result.get("code") == 0:
                run_id = result.get("data", {}).get("runId", "")
                logger.info("回测任务已发起: runId=%s", run_id)
                return run_id
            else:
                logger.error("回测发起失败: %s", result.get("message", "未知错误"))
                return None
        else:
            logger.error("HTTP请求失败: %d - %s", resp.status_code, resp.text)
            return None

    except requests.exceptions.RequestException as e:
        logger.error("请求异常: %s", str(e))
        return None


def poll_progress(run_id: str, config: dict, logger: logging.Logger,
                  max_retries: int = 0, timeout: int = 0,
                  poll_interval: int = 3) -> Optional[dict]:
    """
    轮询回测进度

    Args:
        run_id: 回测运行ID
        config: 配置
        logger: 日志器
        max_retries: 最大轮询次数（0表示不限制）
        timeout: 超时时间秒（0表示不限制）
        poll_interval: 轮询间隔秒

    Returns:
        最终进度数据，包含 percent, err_msg 等
    """
    base_url = config["service"]["base_url"]
    api_path = config["backtest"]["progress"].format(run_id=run_id)
    url = f"{base_url}{api_path}"

    start_time = time.time()
    retry_count = 0
    last_percent = -1

    logger.info("开始轮询回测进度: runId=%s, interval=%ds", run_id, poll_interval)

    while True:
        # 超时检查
        if timeout > 0 and (time.time() - start_time) > timeout:
            logger.error("回测轮询超时: runId=%s, 已等待%ds", run_id, timeout)
            return {"runId": run_id, "percent": -1, "err_msg": "回测轮询超时"}

        # 最大重试检查
        if max_retries > 0 and retry_count >= max_retries:
            logger.error("回测轮询达到最大次数: runId=%s, maxRetries=%d", run_id, max_retries)
            return {"runId": run_id, "percent": -1, "err_msg": "达到最大轮询次数"}

        try:
            resp = requests.get(url, timeout=10)
            logger.info("进度查询 [%d]: HTTP %d", retry_count + 1, resp.status_code)

            if resp.status_code == 200:
                result = resp.json()
                data = result.get("data", {})

                if data:
                    percent = data.get("percent", 0)
                    err_msg = data.get("err_msg", "")
                    status = data.get("status", "unknown")

                    if percent != last_percent:
                        logger.info("进度更新: %.1f%% - status=%s", percent * 100, status)
                        last_percent = percent

                    # 回测完成
                    if percent >= 1.0:
                        logger.info("回测完成: runId=%s, percent=%.1f%%", run_id, percent * 100)
                        return data

                    # 回测出错
                    if err_msg:
                        logger.error("回测出错: runId=%s, err_msg=%s", run_id, err_msg)
                        return data

            elif resp.status_code == 404:
                logger.info("进度文件尚未生成，继续等待...")
            else:
                logger.warning("进度查询返回非200: %d - %s", resp.status_code, resp.text)

        except requests.exceptions.RequestException as e:
            logger.warning("进度查询请求异常: %s", str(e))

        retry_count += 1
        time.sleep(poll_interval)


def run_backtest(strategy_name: str, workspace_path: str, plan_name: str = "默认方案",
                 user_id: str = "default") -> dict:
    """
    完整的回测流程：发起回测 -> 轮询进度 -> 返回结果

    Returns:
        {"success": bool, "run_id": str, "percent": float, "err_msg": str}
    """
    config = load_config()
    logger = setup_logging(strategy_name, workspace_path, config)

    logger.info("=" * 60)
    logger.info("开始回测流程: 策略=%s, 方案=%s, 用户=%s", strategy_name, plan_name, user_id)

    # 1. 发起回测
    run_id = start_backtest(strategy_name, plan_name, user_id, config, logger)
    if run_id is None:
        logger.error("回测发起失败")
        return {"success": False, "run_id": "", "percent": 0, "err_msg": "回测发起失败"}

    # 重新设置日志（带上run_id）
    logger = setup_logging(strategy_name, workspace_path, config, run_id)
    logger.info("回测已发起: runId=%s", run_id)

    # 2. 轮询进度
    poll_config = config.get("backtest_poll", {})
    interval = poll_config.get("interval", 3)
    max_retries = poll_config.get("max_retries", 0)
    timeout = poll_config.get("timeout", 0)

    final_data = poll_progress(run_id, config, logger,
                               max_retries=max_retries,
                               timeout=timeout,
                               poll_interval=interval)

    # 3. 返回结果
    if final_data:
        percent = final_data.get("percent", 0)
        err_msg = final_data.get("err_msg", "")

        if percent >= 1.0 and not err_msg:
            logger.info("回测成功完成: runId=%s", run_id)
            result = {"success": True, "run_id": run_id, "percent": percent, "err_msg": ""}
        elif err_msg:
            logger.error("回测出错: %s", err_msg)
            result = {"success": False, "run_id": run_id, "percent": percent, "err_msg": err_msg}
        else:
            logger.warning("回测未完成: percent=%.1f%%", percent * 100)
            result = {"success": False, "run_id": run_id, "percent": percent,
                      "err_msg": "回测未正常完成"}
    else:
        result = {"success": False, "run_id": run_id, "percent": 0,
                  "err_msg": "无法获取回测结果"}

    logger.info("回测流程结束: success=%s, runId=%s", result["success"], result["run_id"])
    logger.info("=" * 60)
    return result


def main():
    parser = argparse.ArgumentParser(description="发起回测并监控进度")
    parser.add_argument("--strategy-name", type=str, required=True, help="策略名称")
    parser.add_argument("--workspace-path", type=str, required=True, help="策略工作目录根路径")
    parser.add_argument("--plan-name", type=str, default="默认方案", help="方案名称")
    parser.add_argument("--user-id", type=str, default="default", help="用户ID")
    args = parser.parse_args()

    result = run_backtest(args.strategy_name, args.workspace_path, args.plan_name, args.user_id)

    # 输出JSON结果供大模型解析
    print(json.dumps(result, ensure_ascii=False))

    if result["success"]:
        print(f"\nSUCCESS: 回测完成 runId={result['run_id']}")
    else:
        print(f"\nERROR: 回测失败 - {result['err_msg']}")
        sys.exit(1)


if __name__ == "__main__":
    main()