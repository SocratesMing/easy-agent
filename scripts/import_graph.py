#!/usr/bin/env python3
"""easy-agent import graph scanner + SCC gate (Phase 0.1).

Builds the intra-package import graph with :mod:`ast` only (no third-party
dependencies), computes strongly connected components (Tarjan) and compares
them with ``scripts/import_graph_whitelist.json``.

Gate semantics (``--check-scc``):

* Every SCC with more than one module must match a whitelist entry *exactly*
  (same module set).  A new cycle, a changed module set, or a stale whitelist
  entry all fail the gate.
* An empty whitelist therefore means "SCC must be 0".
* Whitelist entries must carry a human-readable ``reason``; the whitelist is
  expected to shrink only (fix the cycle, then delete the entry).

Usage::

    python scripts/import_graph.py                 # human readable report
    python scripts/import_graph.py --json          # machine readable report
    python scripts/import_graph.py --check-scc     # CI gate (exit 1 on drift)

The ``--root`` / ``--package`` / ``--whitelist`` options make the scanner
testable against temporary fixture packages.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGE = "easy_agent"
DEFAULT_WHITELIST = Path(__file__).with_name("import_graph_whitelist.json")

# Directories that never contain importable runtime modules.
_SKIP_DIRS = {
    "__pycache__",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "node_modules",
    "static",
}


@dataclass(frozen=True)
class Edge:
    """One import statement edge (``source`` imports ``target``)."""

    source: str
    target: str
    line: int
    text: str


@dataclass
class ImportGraph:
    package: str
    root: Path
    modules: dict[str, Path] = field(default_factory=dict)
    init_modules: set[str] = field(default_factory=set)
    edges: list[Edge] = field(default_factory=list)

    def adjacency(self) -> dict[str, set[str]]:
        graph = {module: set() for module in self.modules}
        for edge in self.edges:
            graph[edge.source].add(edge.target)
        return graph

    def cycles(self) -> list[list[str]]:
        """Return SCCs with more than one module, each sorted by name."""
        return find_cycles(self.adjacency())


def _module_name(path: Path, root: Path) -> tuple[str, bool]:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    is_init = bool(parts) and parts[-1] == "__init__"
    if is_init:
        parts.pop()
    return ".".join(parts), is_init


def _iter_python_files(package_dir: Path):
    for dirpath, dirnames, filenames in os.walk(package_dir):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                yield Path(dirpath) / filename


def _resolve_module(name: str, modules: dict[str, Path]) -> str | None:
    """Resolve ``a.b.c`` to the longest existing module prefix."""
    parts = name.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in modules:
            return candidate
    return None


def _package_of(module: str, is_init: bool) -> str:
    if is_init:
        return module
    return module.rsplit(".", 1)[0] if "." in module else ""


def _absolute_from_target(node: ast.ImportFrom, module: str, is_init: bool) -> str:
    """Resolve the base module of a ``from ... import ...`` statement."""
    if node.level == 0:
        return node.module or ""
    package = _package_of(module, is_init)
    parts = package.split(".") if package else []
    up = node.level - 1
    if up > 0:
        parts = parts[: max(0, len(parts) - up)]
    base = ".".join(parts)
    if not node.module:
        return base
    return f"{base}.{node.module}" if base else node.module


def _import_edges(
    tree: ast.AST,
    module: str,
    is_init: bool,
    modules: dict[str, Path],
) -> list[Edge]:
    edges: list[Edge] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = _resolve_module(alias.name, modules)
                if target and target != module:
                    edges.append(Edge(module, target, node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            full = _absolute_from_target(node, module, is_init)
            names = ", ".join(alias.name for alias in node.names)
            raw = f"from {'.' * node.level}{node.module or ''} import {names}"
            targets: set[str] = set()
            for alias in node.names:
                if alias.name == "*":
                    continue
                candidate = f"{full}.{alias.name}" if full else alias.name
                if candidate in modules:
                    targets.add(candidate)
            if not targets and full:
                resolved = _resolve_module(full, modules)
                if resolved:
                    targets.add(resolved)
            for target in sorted(targets):
                if target != module:
                    edges.append(Edge(module, target, node.lineno, raw))
    return edges


def build_graph(root: str | Path, package: str = DEFAULT_PACKAGE) -> ImportGraph:
    """Parse every module of ``package`` under ``root`` and build the graph."""
    root = Path(root).resolve()
    package_dir = root.joinpath(*package.split("."))
    if not package_dir.is_dir():
        raise FileNotFoundError(f"package directory not found: {package_dir}")

    graph = ImportGraph(package=package, root=root)
    for path in _iter_python_files(package_dir):
        module, is_init = _module_name(path, root)
        if not module:
            continue
        graph.modules[module] = path
        if is_init:
            graph.init_modules.add(module)

    for module, path in sorted(graph.modules.items()):
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
        graph.edges.extend(
            _import_edges(tree, module, module in graph.init_modules, graph.modules)
        )
    return graph


def find_cycles(adjacency: dict[str, set[str]]) -> list[list[str]]:
    """Iterative Tarjan; returns non-trivial SCCs sorted deterministically."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[list[str]] = []
    counter = 0

    for start in sorted(adjacency):
        if start in index:
            continue
        index[start] = low[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)
        # Frame: (node, iterator over sorted neighbours)
        work: list[tuple[str, object]] = [(start, iter(sorted(adjacency.get(start, ()))))]

        while work:
            node, neighbours = work[-1]
            advanced = False
            for neighbour in neighbours:  # type: ignore[union-attr]
                if neighbour not in index:
                    index[neighbour] = low[neighbour] = counter
                    counter += 1
                    stack.append(neighbour)
                    on_stack.add(neighbour)
                    work.append(
                        (
                            neighbour,
                            iter(sorted(adjacency.get(neighbour, ()))),
                        )
                    )
                    advanced = True
                    break
                if neighbour in on_stack:
                    low[node] = min(low[node], index[neighbour])
            if advanced:
                continue

            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    result.append(sorted(component))

    return sorted(result)


def _normalize_module(name: str, package: str) -> str:
    cleaned = name.strip().rstrip(".")
    if not cleaned:
        return cleaned
    if cleaned == package or cleaned.startswith(f"{package}."):
        return cleaned
    return f"{package}.{cleaned}"


def load_whitelist(path: str | Path, package: str = DEFAULT_PACKAGE) -> list[dict]:
    """Load and validate the SCC whitelist.

    Canonical format::

        {
          "schema_version": 1,
          "sccs": [
            {"id": "...", "reason": "...", "modules": ["pkg.a", "pkg.b"]}
          ]
        }
    """
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("whitelist root must be a JSON object")
    raw_entries = data.get("sccs")
    if not isinstance(raw_entries, list):
        raise ValueError('whitelist must contain a list under "sccs"')

    entries: list[dict] = []
    owner: dict[str, str] = {}
    for position, raw in enumerate(raw_entries, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"sccs[{position - 1}] must be an object")
        modules = raw.get("modules")
        if not isinstance(modules, list) or not modules:
            raise ValueError(f"sccs[{position - 1}].modules must be a non-empty list")
        normalized = sorted(
            {_normalize_module(str(module), package) for module in modules}
        )
        if len(normalized) != len(modules):
            # Duplicate module names (including shorthand/full-name aliases) in
            # one entry are a config smell, not a cycle.
            raise ValueError(f"sccs[{position - 1}].modules contains duplicates")
        reason = str(raw.get("reason", "")).strip()
        if not reason:
            raise ValueError(
                f"sccs[{position - 1}] must document a non-empty reason "
                "(whitelist entries are frozen debt, not a mute button)"
            )
        for module in normalized:
            if module in owner:
                raise ValueError(
                    f"module {module} appears in both {owner[module]} and "
                    f"sccs[{position - 1}]"
                )
            owner[module] = f"sccs[{position - 1}]"
        entries.append(
            {
                "id": str(raw.get("id") or f"scc-{position}"),
                "reason": reason,
                "modules": normalized,
            }
        )
    return entries


def compare_sccs(
    cycles: list[list[str]],
    entries: list[dict],
    package: str = DEFAULT_PACKAGE,
) -> list[str]:
    """Return human-readable gate errors; empty list means the gate passes."""
    errors: list[str] = []
    actual_cycles = [sorted(cycle) for cycle in cycles]
    remaining = list(entries)

    for cycle in sorted(actual_cycles):
        cycle_set = set(cycle)
        exact = next(
            (entry for entry in remaining if set(entry["modules"]) == cycle_set),
            None,
        )
        if exact is not None:
            remaining.remove(exact)
            continue

        best = max(
            remaining,
            key=lambda entry: len(cycle_set & set(entry["modules"])),
            default=None,
        )
        overlap = len(cycle_set & set(best["modules"])) if best else 0
        if best is not None and overlap > 0:
            added = sorted(cycle_set - set(best["modules"]))
            removed = sorted(set(best["modules"]) - cycle_set)
            errors.append(
                f"环模块集合变化（白名单项 {best['id']}）：新增 {added}，减少 {removed}"
            )
            remaining.remove(best)
        else:
            errors.append("新增未白名单环: " + ", ".join(cycle))

    for entry in sorted(remaining, key=lambda item: item["id"]):
        errors.append(
            f"白名单过期环（{entry['id']}）："
            + ", ".join(entry["modules"])
            + " 已不在 SCC 中；请删除该白名单项（白名单只减不增）"
        )

    if not entries and actual_cycles:
        errors.insert(0, "白名单为空，要求 SCC=0，实际发现 %d 个环" % len(actual_cycles))
    return errors


def graph_report(graph: ImportGraph) -> dict:
    adjacency = graph.adjacency()
    cycles = graph.cycles()
    edges = sorted(
        graph.edges, key=lambda edge: (edge.source, edge.line, edge.target, edge.text)
    )
    return {
        "package": graph.package,
        "root": str(graph.root),
        "n_modules": len(graph.modules),
        "n_edges": sum(len(targets) for targets in adjacency.values()),
        "n_sccs": len(cycles),
        "n_scc_modules": sum(len(cycle) for cycle in cycles),
        "modules": {module: sorted(adjacency[module]) for module in sorted(adjacency)},
        "edges": [
            {
                "from": edge.source,
                "to": edge.target,
                "line": edge.line,
                "import": edge.text,
            }
            for edge in edges
        ],
        "sccs": [
            {"size": len(cycle), "modules": cycle} for cycle in cycles
        ],
    }


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
        help="SCC whitelist JSON (default: scripts/import_graph_whitelist.json)",
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
