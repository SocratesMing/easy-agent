#!/usr/bin/env python3
"""Phase 0 多副本能力门禁。

默认模式只展示当前特性取值；`EASY_MULTI_REPLICA_STRICT=1` 且生产多副本时，
任何未外置能力都会导致 exit 1（Phase 2/3 完成前的预期行为）。
"""

from __future__ import annotations

import json
import sys

from easy_agent.env import env_str
from easy_agent.runtime_features import load_runtime_features, multi_replica_errors
from easy_agent.utils.deployment import detect_worker_count


def main() -> int:
    features = load_runtime_features()
    workers = detect_worker_count()
    agent_env = env_str("AGENT_ENV", "dev")
    errors = multi_replica_errors(
        agent_env=agent_env, workers=workers, features=features
    )
    payload = {
        "agent_env": agent_env,
        "workers": workers,
        "features": features.as_dict(),
        "errors": errors,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
