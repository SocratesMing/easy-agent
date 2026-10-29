"""文档 SQL 增量同步手动入口（运维/回填用）。

用法：
    uv run python easy_agent/knowledges/scripts/sync_source_docs.py --dry-run --limit 20
    uv run python easy_agent/knowledges/scripts/sync_source_docs.py --start-date 2026-01-01
    uv run python easy_agent/knowledges/scripts/sync_source_docs.py

依赖配置：config.yaml / .env.{AGENT_ENV} 的 ``knowledge.source_sync`` 段；
自动同步由后端启动钩子按 ``interval_minutes`` 调度，本脚本用于预检与补跑。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from easy_agent.bootstrap.initialization import initialize_runtime  # noqa: E402
from easy_agent.db.database import init_database  # noqa: E402
from easy_agent.knowledges.core.config import KnowledgeConfig  # noqa: E402
from easy_agent.knowledges.core.original_store import LocalOriginalStore  # noqa: E402
from easy_agent.knowledges.ragflow import RagflowClient  # noqa: E402
from easy_agent.knowledges.runtime.source_sync import run_source_sync  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="文档 SQL → 知识库增量同步")
    parser.add_argument(
        "--start-date",
        default="",
        help="首次回填起始日期（YYYY-MM-DD）；为空时使用配置 start_date / 最近窗口",
    )
    parser.add_argument("--limit", type=int, default=0, help="本次最多扫描多少条（0=不限）")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将入库的记录，不写库、不上传",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    app_config = initialize_runtime().config
    if app_config is None:
        print("ERROR: 配置未加载，无法同步", file=sys.stderr)
        return 2
    config = KnowledgeConfig.load()
    if not config.enabled:
        print("知识库模块未启用（knowledge.enabled=false），退出")
        return 2
    if not config.source_sync.enabled:
        print("文档同步未启用（knowledge.source_sync.enabled=false），退出")
        return 2

    # 知识库建表函数和默认管理员策略在 import 时注册进 db（应用启动时由 app.py 导入）。
    # 前者随上面的 easy_agent.knowledges 导入已注册，后者要导入 personnel，
    # 否则 init_database 最后报「默认管理员策略未注册」。
    import easy_agent.knowledges.personnel  # noqa: F401

    db = init_database(app_config.database.model_dump())
    ragflow = RagflowClient(config.base_url, config.api_key.get_secret_value())
    store = LocalOriginalStore()
    app = SimpleNamespace(
        state=SimpleNamespace(
            knowledge_config=config,
            db=db,
            ragflow_client=ragflow,
            knowledge_original_store=store,
        )
    )
    stats = await run_source_sync(
        app,
        start_date=args.start_date or None,
        limit=args.limit or None,
        dry_run=args.dry_run,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    return asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
