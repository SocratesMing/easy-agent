#!/usr/bin/env python3
"""easy-agent import graph scanner + SCC gate (Phase 0.1).

Builds the intra-package import graph with :mod:`ast` only (no third-party
dependencies), computes strongly connected components (Tarjan) and compares
them with ``scripts/quality/import_graph_whitelist.json``.

Gate semantics (``--check-scc``):

* Every SCC with more than one module must match a whitelist entry *exactly*
  (same module set).  A new cycle, a changed module set, or a stale whitelist
  entry all fail the gate.
* An empty whitelist therefore means "SCC must be 0".
* Whitelist entries must carry a human-readable ``reason``; the whitelist is
  expected to shrink only (fix the cycle, then delete the entry).

Usage::

    python scripts/quality/import_graph.py                 # human readable report
    python scripts/quality/import_graph.py --json          # machine readable report
    python scripts/quality/import_graph.py --check-scc     # CI gate (exit 1 on drift)

The ``--root`` / ``--package`` / ``--whitelist`` options make the scanner
testable against temporary fixture packages.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[2]
if str(_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(_ROOT_FOR_IMPORTS))

from scripts.quality.import_graph_core import (  # noqa: E402
    DEFAULT_PACKAGE,
    DEFAULT_ROOT,
    DEFAULT_WHITELIST,
    build_graph,
    compare_sccs,
    graph_report,
    load_whitelist,
)

def _print_human(report: dict, errors: list[str], check: bool) -> None:
    print(
        "import graph: {n_modules} modules, {n_edges} internal edges, "
        "{n_sccs} SCC(s) / {n_scc_modules} modules".format(**report)
    )
    for position, scc in enumerate(report["sccs"], start=1):
        modules = ", ".join(module.replace(f"{report['package']}.", "") for module in scc["modules"])
        print(f"  SCC-{position} [{scc['size']}]: {modules}")
    if check:
        if errors:
            print("[check-scc] FAIL")
            for error in errors:
                print(f"  - {error}")
        else:
            print("[check-scc] OK — 实际 SCC 与白名单完全一致")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan the easy-agent import graph and enforce the SCC whitelist.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="project root (default: repository root)",
    )
    parser.add_argument(
        "--package",
        default=DEFAULT_PACKAGE,
        help="package to scan relative to --root (default: easy_agent)",
    )
    parser.add_argument(
        "--whitelist",
        type=Path,
        default=DEFAULT_WHITELIST,
        help="SCC whitelist JSON (default: scripts/quality/import_graph_whitelist.json)",
    )
    parser.add_argument(
        "--check-scc",
        action="store_true",
        help="enforce the whitelist and exit 1 on any drift",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the full module/edge/SCC report as JSON",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="also write the JSON report to this path",
    )
    args = parser.parse_args(argv)

    try:
        graph = build_graph(args.root, args.package)
    except (FileNotFoundError, SyntaxError, UnicodeDecodeError) as exc:
        print(f"[check-scc] ERROR: cannot build import graph: {exc}", file=sys.stderr)
        return 2

    report = graph_report(graph)
    errors: list[str] = []
    if args.check_scc:
        try:
            entries = load_whitelist(args.whitelist, args.package)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"[check-scc] ERROR: invalid whitelist: {exc}", file=sys.stderr)
            return 2
        errors = compare_sccs(graph.cycles(), entries, args.package)

    report["check"] = {"enabled": args.check_scc, "ok": not errors, "errors": errors}

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    if args.json:
        try:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        except BrokenPipeError:
            # `--json | head` is a normal debugging workflow; do not traceback.
            return 0
    else:
        _print_human(report, errors, args.check_scc)

    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
