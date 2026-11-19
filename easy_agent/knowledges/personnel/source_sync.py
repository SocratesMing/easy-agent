"""人员接口同步适配层：从 config.yaml 的 ``personnel.sync.url`` 拉取组织树并转为人员模型。

上游响应固定为 ``data.rootOrg`` 组织树，各节点自带 ``users``；本模块递归收集人员，
按层级生成部门/处室/团队，并复用 Excel 导入同一条 ``upsert_personnel`` 落库路径。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from pydantic import ValidationError

from easy_agent.bootstrap.config_models import PersonnelSyncConfig
from easy_agent.knowledges.personnel.models import PersonnelCreateRequest
from easy_agent.utils.auth import is_super_admin

logger = logging.getLogger(__name__)

# 与 PersonnelCreateRequest 的字段保持一致；source 由配置统一覆盖，不从外部读取。
_SYNC_FIELDS = (
    "username",
    "display_name",
    "department_id",
    "department_name",
    "division_name",
    "team_name",
    "employee_id",
    "email",
    "position",
    "systems",
    "role_systems",
    "mobile",
    "account_status",
)

DEFAULT_SOURCE_NAME = "接口同步"
_REQUEST_TIMEOUT_SECONDS = 600.0


class PersonnelSyncError(RuntimeError):
    """人员接口同步失败；``status_code`` 直接映射为 API 响应状态码。"""

    def __init__(self, message: str, *, status_code: int = 502, errors: list[str] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.errors = errors or []


# 组织树接口以 code/msg 信封返回，失败时不再继续展开。
def _assert_success(payload: Any) -> None:
    if isinstance(payload, dict) and "code" in payload and str(payload.get("code")) != "0":
        message = str(payload.get("msg") or "未知错误")
        raise PersonnelSyncError(f"人员接口返回失败：{message}", status_code=502)


# 上游仅见 status=0（按启用处理）；出现停用枚举时在此扩展。
_DISABLED_STATUS = {"disabled", "inactive", "停用", "禁用"}


def _map_account_status(value: Any) -> str:
    text = str(value).strip().lower() if value is not None else ""
    return "disabled" if text in _DISABLED_STATUS else "active"


def _relative_org_name(name: Any, root_name: str) -> str:
    """去掉组织名中的部门前缀，处室/团队只保留相对名称。"""
    text = str(name or "").strip()
    if root_name and text != root_name and text.startswith(root_name):
        text = text[len(root_name):].strip()
    return text


# 上游“系统”字段名未完全固定，按常见命名兼容取值。
_SYSTEM_KEYS = (
    "systems",
    "system",
    "apps",
    "app",
    "systemList",
    "systemNames",
    "systemName",
)
_SYSTEM_SPLIT = re.compile(r"[/,，;；、|｜]")


def _clean_roles(user: dict) -> str:
    """角色只取 roles[].roleName（多个用 / 分隔）；roles 为空就是空，不回退 posts。"""
    roles = user.get("roles")
    names: list[str] = []
    if isinstance(roles, list):
        for item in roles:
            name = (
                str(item.get("roleName") or "").strip()
                if isinstance(item, dict)
                else str(item or "").strip()
            )
            if name and name not in names:
                names.append(name)
    return "/".join(names)[:255]


def _clean_systems(user: dict) -> str:
    """系统取 roles[].appCode（多个去重）；没有 roles 时兼容独立系统字段。"""
    roles = user.get("roles")
    codes: list[str] = []
    if isinstance(roles, list):
        for item in roles:
            if not isinstance(item, dict):
                continue
            code = str(item.get("appCode") or "").strip()
            if code and code not in codes:
                codes.append(code)
    if codes:
        return "/".join(codes)[:255]

    raw: Any = None
    for key in _SYSTEM_KEYS:
        if key in user and user.get(key) not in (None, "", []):
            raw = user.get(key)
            break
    if raw is None:
        return ""
    parts = raw if isinstance(raw, list) else _SYSTEM_SPLIT.split(str(raw))
    cleaned: list[str] = []
    for part in parts:
        text = str(part or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return "/".join(cleaned)[:255]


def _clean_role_systems(user: dict) -> str:
    """系统↔角色配对（roles[].appCode + roleName）存成 JSON，供按系统收窄角色筛选。

    上游一个角色挂在某个系统下，一个人可能有多个系统；只存配对、不改变
    ``position`` / ``systems`` 两列的展示语义。
    """
    roles = user.get("roles")
    pairs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    if isinstance(roles, list):
        for item in roles:
            if not isinstance(item, dict):
                continue
            system = str(item.get("appCode") or "").strip()
            role = str(item.get("roleName") or "").strip()
            if not role or (system, role) in seen:
                continue
            seen.add((system, role))
            pairs.append({"system": system, "role": role})
    text = json.dumps(pairs, ensure_ascii=False)
    # 列宽 1000：超出时逐个丢弃末尾配对，保证始终是合法 JSON。
    while pairs and len(text) > 1000:
        pairs.pop()
        text = json.dumps(pairs, ensure_ascii=False)
    return text if pairs else ""


def _map_tree_user(
    user: dict, root_org_id: str, root_name: str, division: str, team: str
) -> dict:
    username = ""
    for key in ("loginNo", "ssoLoginNo", "iamLoginNo"):
        username = str(user.get(key) or "").strip()
        if username:
            break
    return {
        "username": username,
        "display_name": str(user.get("name") or "").strip(),
        "employee_id": str(user.get("iamEmpLoginNo") or "").strip(),
        "department_id": root_org_id,
        "department_name": root_name,
        "division_name": division,
        "team_name": team,
        "position": _clean_roles(user),
        "systems": _clean_systems(user),
        "role_systems": _clean_role_systems(user),
        "mobile": str(user.get("mobileNo") or "").strip(),
        "account_status": _map_account_status(user.get("status")),
    }


def _flatten_org_tree(root: dict) -> list[dict]:
    """展开组织树：各节点 users 先序收集，按账号去重并优先保留更具体的层级。"""
    root_name = str(root.get("orgName") or "").strip()
    root_org_id = str(root.get("orgId") or "").strip()
    records: list[dict] = []
    index_by_username: dict[str, int] = {}

    def depth(record: dict) -> int:
        return int(bool(record.get("division_name"))) + int(bool(record.get("team_name")))

    def walk(node: dict, division: str, team: str) -> None:
        users = node.get("users")
        if isinstance(users, list):
            for user in users:
                if not isinstance(user, dict):
                    continue
                record = _map_tree_user(user, root_org_id, root_name, division, team)
                username = record["username"]
                if username and username in index_by_username:
                    index = index_by_username[username]
                    if depth(record) > depth(records[index]):
                        records[index] = record
                    continue
                if username:
                    index_by_username[username] = len(records)
                records.append(record)
        children = node.get("children")
        if not isinstance(children, list):
            return
        for child in children:
            if not isinstance(child, dict):
                continue
            child_name = _relative_org_name(child.get("orgName"), root_name)
            if division:
                walk(child, division, _relative_org_name(child_name, division))
            else:
                walk(child, child_name, "")

    walk(root, "", "")
    return records


def fetch_source_records(cfg: PersonnelSyncConfig) -> list[dict]:
    """拉取组织树并展开为人员记录；URL 未配置返回 422，上游异常返回 502/504。"""
    url = cfg.url.strip()
    if not url:
        raise PersonnelSyncError(
            "人员接口地址未配置（personnel.sync.url）", status_code=422
        )
    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = client.get(url)
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException as exc:
        logger.warning(
            "[人员同步] 调用上游超时 | url=%s | 超时=%.0fs | %s",
            url,
            _REQUEST_TIMEOUT_SECONDS,
            exc,
        )
        raise PersonnelSyncError(
            f"调用人员接口超时（{_REQUEST_TIMEOUT_SECONDS:.0f}s）：{url}", status_code=504
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning(
            "[人员同步] 调用上游失败 | url=%s | %s: %s", url, type(exc).__name__, exc
        )
        raise PersonnelSyncError(f"调用人员接口失败：{exc}", status_code=502) from exc
    except ValueError as exc:
        raise PersonnelSyncError("人员接口返回的不是合法 JSON", status_code=502) from exc

    _assert_success(payload)
    data = payload.get("data") if isinstance(payload, dict) else None
    root = data.get("rootOrg") if isinstance(data, dict) else None
    if not isinstance(root, dict):
        raise PersonnelSyncError(
            "人员接口响应缺少组织树根节点（data.rootOrg）", status_code=502
        )
    records = _flatten_org_tree(root)
    expected = data.get("totalUserCount")
    if isinstance(expected, int) and expected != len(records):
        logger.warning(
            "[人员同步] 展开人数与上游 totalUserCount 不一致 | 上游: %s | 实际: %s",
            expected,
            len(records),
        )
    return records


def _map_record(record: dict) -> dict[str, Any]:
    """组织树展开结果已使用本地字段名，这里只保留白名单字段。"""
    return {name: record.get(name) for name in _SYNC_FIELDS}


def _format_validation_error(index: int, username: str, exc: ValidationError) -> str:
    messages = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error.get("loc", ()))
        message = str(error.get("msg", "")).strip()
        messages.append(f"{field}: {message}" if field else message)
    who = f"（{username}）" if username else ""
    return f"第 {index} 条{who}：{'；'.join(messages)}"


def build_personnel_items(
    records: list[dict],
) -> tuple[list[PersonnelCreateRequest], int, list[str]]:
    """映射并校验全部记录；返回 (有效记录, 跳过数, 错误列表)。

    调用方应在有错误时整体中止，保持与 Excel 导入一致的全量校验语义。
    """
    items: list[PersonnelCreateRequest] = []
    errors: list[str] = []
    skipped = 0
    for index, record in enumerate(records, start=1):
        mapped = _map_record(record)
        username = str(mapped.get("username") or "").strip()
        if is_super_admin(username):
            skipped += 1
            continue
        mapped["source"] = DEFAULT_SOURCE_NAME
        try:
            items.append(PersonnelCreateRequest(**mapped))
        except ValidationError as exc:
            errors.append(_format_validation_error(index, username, exc))
    return items, skipped, errors
