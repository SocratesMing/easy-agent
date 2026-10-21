"""AST/SQL scanners and repository checks for the double-write gate.

CLI lives in :mod:`scripts.quality.check_double_write`; allowlists live in
:mod:`scripts.quality.double_write_allowlists`. Keeping this module importable
lets tests exercise the scanners without spawning a subprocess.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Allowlists live in a small constant-only module so changes are easy to review.
_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[2]
if str(_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(_ROOT_FOR_IMPORTS))
from scripts.quality.double_write_allowlists import (  # noqa: E402
    FROZEN_DATABASE_METHODS,
    INFRA_DATABASE_METHODS,
)

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
SESSIONS_REL = "easy_agent/db/repositories/sessions.py"
DATABASE_REL = "easy_agent/db/database.py"

# --- SQL/AST helpers ----------------------------------------------------------

_INSERT_SESSIONS_RE = re.compile(
    r"\b(?:INSERT|REPLACE)\s+(?:IGNORE\s+)?INTO\s+[`\"']?sessions[`\"']?",
    re.IGNORECASE,
)
_UPDATE_SESSIONS_RE = re.compile(
    r"\bUPDATE\s+[`\"']?sessions[`\"']?\s+SET\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    code: str
    message: str
    function: str = ""

    def format(self) -> str:
        context = f" [{self.function}]" if self.function else ""
        return f"{self.path}:{self.line}: {self.code}{context} {self.message}"


@dataclass
class CheckResult:
    violations: list[Violation] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.violations


def _read_paren(text: str, start: int) -> tuple[str | None, int]:
    """Return the content of a balanced ``(...)`` group and the index after it."""
    if start >= len(text) or text[start] != "(":
        return None, start
    depth = 0
    quote: str | None = None
    index = start
    while index < len(text):
        char = text[index]
        if quote is not None:
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif char in "'\"`":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index], index + 1
        index += 1
    return None, start


def _split_top_level(text: str) -> list[str]:
    """Split by commas that are outside quotes and parentheses."""
    items: list[str] = []
    buffer: list[str] = []
    depth = 0
    quote: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote is not None:
            buffer.append(char)
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    buffer.append(text[index + 1])
                    index += 2
                    continue
                quote = None
        elif char in "'\"`":
            quote = char
            buffer.append(char)
        elif char == "(":
            depth += 1
            buffer.append(char)
        elif char == ")":
            depth -= 1
            buffer.append(char)
        elif char == "," and depth == 0:
            items.append("".join(buffer).strip())
            buffer = []
        else:
            buffer.append(char)
        index += 1
    tail = "".join(buffer).strip()
    if tail:
        items.append(tail)
    return items


def _clean_identifier(text: str) -> str:
    return text.strip().strip("`\"'").split(".")[-1].strip().lower()


def _is_placeholder(text: str) -> bool:
    return text.strip() in {"?", "%s"}


def _count_placeholders(text: str) -> int:
    return len(re.findall(r"%s|\?", text))


def _literal_is_empty(text: str) -> bool:
    value = text.strip()
    if len(value) >= 2 and value[0] in "'\"" and value[-1] == value[0]:
        quote = value[0]
        value = value[1:-1].replace(quote * 2, quote)
    value = value.strip()
    if value in {"", "[]"}:
        return True
    try:
        return json.loads(value) == []
    except (json.JSONDecodeError, TypeError):
        return False


def _is_fixed_empty_expr(node: ast.AST) -> bool:
    """True for values that cannot carry conversation content."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _literal_is_empty(node.value)
    if isinstance(node, (ast.List, ast.Tuple)):
        return len(node.elts) == 0
    if isinstance(node, ast.Dict):
        return len(node.keys) == 0
    if isinstance(node, ast.Call):
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "dumps"
            and isinstance(func.value, ast.Name)
            and func.value.id == "json"
            and len(node.args) == 1
        ):
            return _is_fixed_empty_expr(node.args[0])
    return False


def _references_messages(node: ast.AST) -> bool:
    """True when an expression reads a value named/shaped like messages."""
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr == "messages":
            return True
        if isinstance(child, ast.Name) and child.id == "messages":
            return True
        if isinstance(child, ast.Subscript):
            key = child.slice
            if isinstance(key, ast.Constant) and key.value == "messages":
                return True
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr == "get":
                for argument in child.args:
                    if isinstance(argument, ast.Constant) and argument.value == "messages":
                        return True
        if isinstance(child, ast.Dict):
            for key in child.keys:
                if isinstance(key, ast.Constant) and key.value == "messages":
                    return True
    return False


def _param_nodes(node: ast.AST | None) -> list[ast.AST]:
    if node is None:
        return []
    if isinstance(node, (ast.Tuple, ast.List)):
        return list(node.elts)
    return [node]


def _string_value(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                return None
        return "".join(parts)
    return None


def _sql_args(call: ast.Call) -> tuple[ast.AST | None, ast.AST | None]:
    """Return (sql, params) for ``db._execute(...)`` / ``cursor.execute(...)``."""
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None, None
    if func.attr == "_execute":
        sql = call.args[1] if len(call.args) >= 2 else None
        params = call.args[2] if len(call.args) >= 3 else None
        for keyword in call.keywords:
            if keyword.arg == "sql":
                sql = keyword.value
            elif keyword.arg == "params":
                params = keyword.value
        return sql, params
    if func.attr == "execute":
        sql = call.args[0] if call.args else None
        params = call.args[1] if len(call.args) >= 2 else None
        for keyword in call.keywords:
            if keyword.arg == "sql":
                sql = keyword.value
            elif keyword.arg == "params":
                params = keyword.value
        return sql, params
    return None, None


def _parse_insert_statements(sql: str) -> list[dict]:
    statements: list[dict] = []
    for match in _INSERT_SESSIONS_RE.finditer(sql):
        position = match.end()
        while position < len(sql) and sql[position].isspace():
            position += 1
        columns: list[str] | None = None
        if position < len(sql) and sql[position] == "(":
            content, end = _read_paren(sql, position)
            if content is not None:
                columns = [item for item in _split_top_level(content) if item]
                position = end

        rest = sql[position:]
        values_match = re.search(r"\bVALUES\b", rest, re.IGNORECASE)
        set_match = re.search(r"\bSET\b", rest, re.IGNORECASE)
        kind = None
        keyword_match = None
        if values_match and (set_match is None or values_match.start() <= set_match.start()):
            kind = "values"
            keyword_match = values_match
        elif set_match is not None:
            kind = "set"
            keyword_match = set_match

        if kind is None or keyword_match is None:
            statements.append(
                {"columns": columns, "kind": None, "items": [], "assignments": [], "multi": False}
            )
            continue

        after = position + keyword_match.end()
        if kind == "values":
            while after < len(sql) and sql[after].isspace():
                after += 1
            content, end = _read_paren(sql, after)
            items = _split_top_level(content) if content is not None else []
            multi = end < len(sql) and sql[end:].lstrip().startswith(",")
            statements.append(
                {"columns": columns, "kind": kind, "items": items, "assignments": [], "multi": multi}
            )
        else:
            set_text = sql[after:]
            where_match = re.search(r"\bWHERE\b", set_text, re.IGNORECASE)
            if where_match is not None:
                set_text = set_text[: where_match.start()]
            statements.append(
                {
                    "columns": columns,
                    "kind": kind,
                    "items": [],
                    "assignments": _split_top_level(set_text),
                    "multi": False,
                }
            )
    return statements


def _parse_update_statements(sql: str) -> list[list[str]]:
    statements: list[list[str]] = []
    for match in _UPDATE_SESSIONS_RE.finditer(sql):
        rest = sql[match.end() :]
        where_match = re.search(r"\bWHERE\b", rest, re.IGNORECASE)
        if where_match is not None:
            rest = rest[: where_match.start()]
        statements.append(_split_top_level(rest))
    return statements


def _check_set_assignments(
    assignments: list[str],
    params: list[ast.AST],
    path: str,
    line: int,
    function: str,
) -> list[Violation]:
    violations: list[Violation] = []
    seen_placeholders = 0
    for assignment in assignments:
        if "=" not in assignment:
            continue
        left, right = assignment.split("=", 1)
        name = _clean_identifier(left)
        value = right.strip()
        if name == "messages":
            if _is_placeholder(value):
                node = (
                    params[seen_placeholders]
                    if seen_placeholders < len(params)
                    else None
                )
                if node is None or not _is_fixed_empty_expr(node):
                    violations.append(
                        Violation(
                            path,
                            line,
                            "DW101",
                            "SET/INSERT 写入 sessions.messages 的值不是固定空数组（疑似双写）",
                            function,
                        )
                    )
            elif not _literal_is_empty(value):
                violations.append(
                    Violation(
                        path,
                        line,
                        "DW101",
                        "SET/INSERT 向 sessions.messages 写入非空字面量（疑似双写）",
                        function,
                    )
                )
        seen_placeholders += _count_placeholders(value)
    return violations


def _check_insert_statement(
    statement: dict,
    params: list[ast.AST],
    path: str,
    line: int,
    function: str,
) -> list[Violation]:
    violations: list[Violation] = []
    columns = statement["columns"]
    kind = statement["kind"]

    if kind == "set":
        return _check_set_assignments(
            statement["assignments"], params, path, line, function
        )

    if columns is None:
        # Positional INSERT has no column list; the schema forces messages to be
        # written, so it cannot be proven safe.
        violations.append(
            Violation(
                path,
                line,
                "DW103",
                "INSERT INTO sessions 未显式列出列名，无法证明未写 messages（请写列名并用固定 '[]'）",
                function,
            )
        )
        return violations

    lowered = [_clean_identifier(column) for column in columns]
    if "messages" not in lowered:
        return violations

    if kind != "values":
        violations.append(
            Violation(
                path,
                line,
                "DW103",
                "INSERT INTO sessions 含 messages 列但不是 VALUES 形式，无法静态校验",
                function,
            )
        )
        return violations
    if statement.get("multi"):
        violations.append(
            Violation(
                path,
                line,
                "DW105",
                "多行 INSERT INTO sessions 暂不支持静态校验，请拆成单行",
                function,
            )
        )
        return violations

    index = lowered.index("messages")
    items = statement["items"]
    if index >= len(items):
        violations.append(
            Violation(
                path,
                line,
                "DW104",
                "列清单与 VALUES 数量不匹配，无法定位 messages 取值",
                function,
            )
        )
        return violations

    value = items[index]
    if _is_placeholder(value):
        param_index = sum(1 for item in items[:index] if _is_placeholder(item))
        node = params[param_index] if param_index < len(params) else None
        if node is None or not _is_fixed_empty_expr(node):
            violations.append(
                Violation(
                    path,
                    line,
                    "DW101",
                    "INSERT INTO sessions 写入 messages 列的值不是固定空数组（疑似双写）",
                    function,
                )
            )
    elif not _literal_is_empty(value):
        violations.append(
            Violation(
                path,
                line,
                "DW101",
                "INSERT INTO sessions 向 messages 列写入非空字面量（疑似双写）",
                function,
            )
        )
    return violations


def _check_execute_call(
    call: ast.Call,
    tree: ast.AST,
    path: str,
) -> list[Violation]:
    sql_node, params_node = _sql_args(call)
    sql = _string_value(sql_node)
    if sql is None:
        return []
    params = _param_nodes(params_node)
    function = _enclosing_function(tree, call.lineno)
    violations: list[Violation] = []
    normalized = sql
    for statement in _parse_insert_statements(normalized):
        violations.extend(
            _check_insert_statement(statement, params, path, call.lineno, function)
        )
    for assignments in _parse_update_statements(normalized):
        violations.extend(
            _check_set_assignments(assignments, params, path, call.lineno, function)
        )
    return violations


def _enclosing_function(tree: ast.AST, line: int) -> str:
    candidate: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = getattr(node, "end_lineno", node.lineno)
        if node.lineno <= line <= end:
            if candidate is None or node.lineno > candidate.lineno:
                candidate = node
    return candidate.name if candidate is not None else "<module>"


def _check_json_dumps(call: ast.Call, tree: ast.AST, path: str) -> list[Violation]:
    func = call.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr == "dumps"
        and isinstance(func.value, ast.Name)
        and func.value.id == "json"
    ):
        return []
    targets = list(call.args[:1])
    targets.extend(keyword.value for keyword in call.keywords if keyword.arg == "obj")
    if not any(_references_messages(target) for target in targets):
        return []
    return [
        Violation(
            path,
            call.lineno,
            "DW102",
            "json.dumps 将 messages 序列化进 legacy sessions.messages（A2 后只允许行表写入）",
            _enclosing_function(tree, call.lineno),
        )
    ]


def check_sessions_source(source: str, path: str = "<sessions>") -> list[Violation]:
    """Check sessions repository source; returns deduplicated violations."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return [Violation(path, exc.lineno or 0, "DW000", f"sessions 文件语法错误: {exc.msg}")]

    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        violations.extend(_check_execute_call(node, tree, path))
        violations.extend(_check_json_dumps(node, tree, path))

    deduplicated: list[Violation] = []
    seen: set[tuple[int, str, str]] = set()
    for violation in violations:
        key = (violation.line, violation.code, violation.message)
        if key not in seen:
            seen.add(key)
            deduplicated.append(violation)
    return sorted(deduplicated, key=lambda item: (item.line, item.code))


def _database_repo_aliases(tree: ast.AST) -> set[str]:
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        # 兼容相对导入（from .repositories import x）与重构后的绝对导入
        # （from easy_agent.db.repositories import x）。
        is_repositories = (
            module == ""
            or module.split(".")[-1] == "repositories"
            or module.startswith("easy_agent.db.repositories")
        )
        if is_repositories:
            aliases.update(alias.asname or alias.name for alias in node.names)
    return aliases


def _is_pure_delegation(
    function: ast.FunctionDef | ast.AsyncFunctionDef, aliases: set[str]
) -> tuple[bool, str]:
    body = list(function.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if len(body) != 1:
        return False, ""
    statement = body[0]
    if isinstance(statement, ast.Return):
        call = statement.value
    elif isinstance(statement, ast.Expr):
        call = statement.value
    else:
        return False, ""
    if not isinstance(call, ast.Call):
        return False, ""
    func = call.func
    if not (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id in aliases
    ):
        return False, ""
    return True, f"{func.value.id}.{func.attr}"


def _database_class(tree: ast.AST) -> ast.ClassDef | None:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Database":
            return node
    return None


def check_database_source(
    source: str, path: str = "<database>", stats: dict[str, int] | None = None
) -> list[Violation]:
    """Check the Database facade against the frozen method allowlist."""
    if stats is not None:
        stats.setdefault("database_methods", 0)
        stats.setdefault("database_delegations", 0)
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return [Violation(path, exc.lineno or 0, "DW000", f"database 文件语法错误: {exc.msg}")]

    database = _database_class(tree)
    if database is None:
        return [Violation(path, 0, "DW200", "未找到 class Database")]

    aliases = _database_repo_aliases(tree)
    violations: list[Violation] = []
    for node in database.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if stats is not None:
            stats["database_methods"] = stats.get("database_methods", 0) + 1
        name = node.name
        if name not in FROZEN_DATABASE_METHODS:
            violations.append(
                Violation(
                    path,
                    node.lineno,
                    "DW201",
                    f"Database 新增了未冻结的方法 {name!r}（业务写入应放 db/repositories/*，"
                    "确需保留请评审后更新 FROZEN_DATABASE_METHODS）",
                )
            )
            continue
        if name in INFRA_DATABASE_METHODS:
            continue
        delegated, target = _is_pure_delegation(node, aliases)
        if delegated:
            if stats is not None:
                stats["database_delegations"] = stats.get("database_delegations", 0) + 1
            continue
        violations.append(
            Violation(
                path,
                node.lineno,
                "DW202",
                f"Database.{name} 不是纯委托（应在 db/repositories/* 或 db/schema.py 实现，"
                f"Database 只保留连接/迁移；可委托模块: {sorted(aliases)}）",
            )
        )
    return violations


def check_sessions_file(path: str | Path, display_path: str | None = None) -> list[Violation]:
    path = Path(path)
    name = display_path or str(path)
    source = path.read_text(encoding="utf-8", errors="replace")
    return check_sessions_source(source, name)


def check_database_file(
    path: str | Path,
    display_path: str | None = None,
    stats: dict[str, int] | None = None,
) -> list[Violation]:
    path = Path(path)
    name = display_path or str(path)
    source = path.read_text(encoding="utf-8", errors="replace")
    return check_database_source(source, name, stats)


def check_repository(root: str | Path = DEFAULT_ROOT) -> CheckResult:
    """Run both freeze checks against a repository root."""
    root = Path(root)
    result = CheckResult()
    targets = (
        (SESSIONS_REL, check_sessions_file),
        (DATABASE_REL, check_database_file),
    )
    for relative, checker in targets:
        result.files.append(relative)
        path = root / relative
        if not path.is_file():
            result.violations.append(
                Violation(relative, 0, "DW000", "门禁目标文件不存在")
            )
            continue
        if checker is check_database_file:
            result.violations.extend(checker(path, relative, result.stats))
        else:
            result.violations.extend(checker(path, relative))
    return result
