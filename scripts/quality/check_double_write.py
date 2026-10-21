#!/usr/bin/env python3
"""Phase 0.2 double-write freeze for easy-agent.

The Architecture Improvement Plan (2026-10-18, phase 0) freezes two legacy
write paths so new code cannot add more of them while phase A/B/C remove the
existing ones:

1. ``easy_agent/db/repositories/sessions.py`` must not write conversation
   bodies into the legacy ``sessions.messages`` JSON column.  Row storage in
   ``session_messages`` is the single authoritative message path (A2).
   - ``INSERT/REPLACE INTO sessions`` may keep the column only with a *fixed
     empty* value (``'[]'`` / ``''`` / an empty list literal), because the
     current schema declares ``messages`` NOT NULL.  Any dynamic value
     (``session.messages``, ``json.dumps(...messages...)`` ...) fails.
   - ``UPDATE sessions SET messages=...`` is treated the same way: only a
     fixed empty value is tolerated; dynamic writes fail.
   - ``SELECT messages`` / ``json.loads`` legacy reads are explicitly allowed.
2. ``easy_agent/db/database.py`` must not grow new business-domain methods.
   ``Database`` is reduced to connection/transaction/migration plumbing plus
   *pure delegations* to ``db/repositories/*`` and ``db/schema.py`` (C1).
   The method list below is a frozen allowlist: add a new method and the gate
   fails until the allowlist is deliberately updated in review.

Usage::

    python scripts/quality/check_double_write.py            # human readable
    python scripts/quality/check_double_write.py --json     # machine readable

Exit codes: 0 = clean, 1 = violations, 2 = the checker itself could not run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[2]
if str(_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(_ROOT_FOR_IMPORTS))

from scripts.quality.double_write_scanner import (  # noqa: E402
    DEFAULT_ROOT,
    check_database_file,
    check_repository,
    check_sessions_file,
)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Freeze legacy double-write paths (sessions.messages / Database facade).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="repository root (default: repository root)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print a machine readable result",
    )
    args = parser.parse_args(argv)

    try:
        result = check_repository(args.root)
    except OSError as exc:
        print(f"[double-write] ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "ok": result.ok,
                    "files": result.files,
                    "stats": result.stats,
                    "violations": [
                        {
                            "path": violation.path,
                            "line": violation.line,
                            "code": violation.code,
                            "function": violation.function,
                            "message": violation.message,
                        }
                        for violation in result.violations
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for relative in result.files:
            file_violations = [
                violation
                for violation in result.violations
                if violation.path == relative
            ]
            status = "FAIL" if file_violations else "OK"
            print(f"[{status}] {relative}")
            for violation in file_violations:
                print(f"  {violation.format()}")
        if result.ok:
            print(
                "[double-write] OK — legacy sessions.messages 写入与 Database 业务方法均已冻结"
            )
            if result.stats:
                print(
                    "  Database: {database_methods} methods, {database_delegations} pure delegations".format(
                        database_methods=result.stats.get("database_methods", 0),
                        database_delegations=result.stats.get("database_delegations", 0),
                    )
                )
        else:
            print(
                f"[double-write] FAIL — {len(result.violations)} violation(s); "
                "见上列文件:行号"
            )

    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
