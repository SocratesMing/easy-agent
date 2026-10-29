"""Import graph scanner core (AST, SCC, whitelist comparison, layer direction).

CLI lives in :mod:`scripts.quality.import_graph`; this module is importable by
tests and by the CLI, keeping the command-line surface small.
"""

from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACKAGE = "easy_agent"
DEFAULT_WHITELIST = Path(__file__).with_name("import_graph_whitelist.json")
DEFAULT_LAYERS = Path(__file__).with_name("import_layers.json")

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
    #: ``import_module(...)`` calls whose module name is not an absolute literal
    warnings: list[str] = field(default_factory=list)

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


def _is_import_module_call(node: ast.Call) -> bool:
    """``importlib.import_module(...)`` or a bare ``import_module(...)``."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr == "import_module"
    return isinstance(func, ast.Name) and func.id == "import_module"


def _dynamic_import_edges(
    tree: ast.AST, module: str, modules: dict[str, Path]
) -> tuple[list[Edge], list[str]]:
    """Treat ``import_module("pkg.x")`` with a literal name as an ordinary edge.

    A name that is not an absolute string literal (an f-string, a variable, a
    relative name) cannot be resolved; it is reported as a warning instead.
    """
    edges: list[Edge] = []
    warnings: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_import_module_call(node)):
            continue
        arg = node.args[0] if node.args else None
        if (
            isinstance(arg, ast.Constant)
            and isinstance(arg.value, str)
            and not arg.value.startswith(".")
        ):
            target = _resolve_module(arg.value, modules)
            if target and target != module:
                edges.append(Edge(module, target, node.lineno, f'import_module("{arg.value}")'))
        else:
            warnings.append(
                f"{module}:{node.lineno} 动态导入的模块名不是绝对字面量，门禁看不到这条边"
            )
    return edges, warnings


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
        dynamic_edges, warnings = _dynamic_import_edges(tree, module, graph.modules)
        graph.edges.extend(dynamic_edges)
        graph.warnings.extend(warnings)
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


def load_layers(
    path: str | Path, package: str = DEFAULT_PACKAGE
) -> tuple[dict[str, int], list[dict]]:
    """Load and validate the layer model and its allow list.

    Canonical format::

        {
          "schema_version": 1,
          "layers": {"pkg": 6, "pkg.db": 1},
          "allow": [{"from": "pkg.a", "to": "pkg.b", "reason": "..."}]
        }

    The root package itself must have a layer so that every module gets one
    (the longest matching prefix wins, see :func:`layer_of`).
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("layer config root must be a JSON object")
    raw_layers = data.get("layers")
    if not isinstance(raw_layers, dict) or not raw_layers:
        raise ValueError('layer config must map module prefixes to layers under "layers"')

    layers: dict[str, int] = {}
    for prefix, level in raw_layers.items():
        if isinstance(level, bool) or not isinstance(level, int) or level < 0:
            raise ValueError(f"layers[{prefix!r}] must be a non-negative integer")
        layers[_normalize_module(str(prefix), package)] = level
    if package not in layers:
        raise ValueError(
            f'layers must assign the root package "{package}" so every module has a layer'
        )

    raw_allow = data.get("allow", [])
    if not isinstance(raw_allow, list):
        raise ValueError('"allow" must be a list')
    allow: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for position, raw in enumerate(raw_allow):
        if not isinstance(raw, dict):
            raise ValueError(f"allow[{position}] must be an object")
        source = _normalize_module(str(raw.get("from", "")), package)
        target = _normalize_module(str(raw.get("to", "")), package)
        if not source or not target:
            raise ValueError(f'allow[{position}] must name both "from" and "to"')
        reason = str(raw.get("reason", "")).strip()
        if not reason:
            raise ValueError(
                f"allow[{position}] must document a non-empty reason "
                "(allowed violations are frozen debt, not a mute button)"
            )
        if (source, target) in seen:
            raise ValueError(f"allow[{position}] duplicates {source} -> {target}")
        seen.add((source, target))
        allow.append({"from": source, "to": target, "reason": reason})
    return layers, allow


def layer_of(module: str, layers: dict[str, int]) -> int | None:
    """Layer of ``module``: the longest matching module prefix wins."""
    best: str | None = None
    for prefix in layers:
        if module == prefix or module.startswith(f"{prefix}."):
            if best is None or len(prefix) > len(best):
                best = prefix
    return layers[best] if best is not None else None


def check_layers(
    graph: ImportGraph, layers: dict[str, int], allow: list[dict]
) -> list[str]:
    """Return gate errors for imports from a lower layer into a higher one.

    Imports within a layer are fine. Every violating module pair must be in
    ``allow``; an allow entry that no longer matches a violation is stale and
    fails too, so the list only shrinks.
    """
    violations: dict[tuple[str, str], set[int]] = {}
    for edge in graph.edges:
        source_layer = layer_of(edge.source, layers)
        target_layer = layer_of(edge.target, layers)
        if source_layer is None or target_layer is None:
            continue
        if source_layer < target_layer:
            violations.setdefault((edge.source, edge.target), set()).add(edge.line)

    allowed = {(entry["from"], entry["to"]) for entry in allow}
    errors: list[str] = []
    for (source, target), lines in sorted(violations.items()):
        if (source, target) in allowed:
            continue
        errors.append(
            f"下层依赖上层：{source}（L{layer_of(source, layers)}）导入 "
            f"{target}（L{layer_of(target, layers)}），第 "
            + ", ".join(str(line) for line in sorted(lines))
            + " 行"
        )
    for entry in allow:
        if (entry["from"], entry["to"]) not in violations:
            errors.append(
                f"白名单过期：{entry['from']} → {entry['to']} 已不再违规；"
                "请删除该条目（白名单只减不增）"
            )
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
        "warnings": list(graph.warnings),
    }


