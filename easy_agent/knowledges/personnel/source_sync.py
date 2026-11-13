"""人员接口同步适配层：从 config.yaml 的 ``personnel.sync`` 拉取数据并转为人员模型。

外部接口尚未固定，因此只暴露最小可配置面：地址、鉴权、人员数组路径、字段映射与
可选分页。字段校验与落库复用 Excel 导入同一条 ``upsert_personnel`` 路径。
"""

from __future__ import annotations

import logging
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
    "mobile",
    "account_status",
)

DEFAULT_SOURCE_NAME = "接口同步"


class PersonnelSyncError(RuntimeError):
    """人员接口同步失败；``status_code`` 直接映射为 API 响应状态码。"""

    def __init__(self, message: str, *, status_code: int = 502, errors: list[str] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.errors = errors or []


def _extract_path(payload: Any, path: str) -> Any:
    """按点号路径取值；空路径返回原对象，数字段可索引列表。"""
    current = payload
    for part in (path or "").split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
    return current


def _request_page(client: httpx.Client, cfg: PersonnelSyncConfig, page: int) -> Any:
    params: dict[str, Any] = {}
    if cfg.page.enabled:
        params[cfg.page.param] = page
        params[cfg.page.size_param] = max(1, int(cfg.page.size or 1))
    headers: dict[str, str] = {}
    if cfg.token:
        headers[cfg.token_header or "Authorization"] = f"{cfg.token_prefix}{cfg.token}"
    response = client.get(cfg.url, params=params, headers=headers)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise PersonnelSyncError("人员接口返回的不是合法 JSON", status_code=502) from exc


def fetch_source_records(cfg: PersonnelSyncConfig) -> list[dict]:
    """拉取外部人员数组；配置缺失返回 422，上游异常返回 502。"""
    if not cfg.enabled:
        raise PersonnelSyncError(
            "人员接口同步未启用，请先在配置中开启 personnel.sync", status_code=422
        )
    if not cfg.url:
        raise PersonnelSyncError(
            "人员接口地址未配置（personnel.sync.url）", status_code=422
        )

    records: list[dict] = []
    try:
        with httpx.Client(timeout=cfg.timeout_seconds) as client:
            page = 1
            while True:
                items = _extract_path(_request_page(client, cfg, page), cfg.items_path)
                if not isinstance(items, list):
                    raise PersonnelSyncError(
                        f"人员接口响应缺少人员数组（items_path={cfg.items_path}）",
                        status_code=502,
                    )
                records.extend(item for item in items if isinstance(item, dict))
                if not cfg.page.enabled or not items or len(items) < cfg.page.size:
                    break
                page += 1
    except httpx.HTTPError as exc:
        raise PersonnelSyncError(f"调用人员接口失败：{exc}", status_code=502) from exc
    return records


def _map_record(record: dict, field_mapping: dict[str, str]) -> dict[str, Any]:
    """默认按本地字段同名读取，``field_mapping`` 中的字段改读外部字段名。"""
    mapped = {name: record.get(name) for name in _SYNC_FIELDS}
    for local_name, external_name in (field_mapping or {}).items():
        if local_name in mapped and external_name:
            mapped[local_name] = _extract_path(record, external_name)
    return mapped


def _format_validation_error(index: int, username: str, exc: ValidationError) -> str:
    messages = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error.get("loc", ()))
        message = str(error.get("msg", "")).strip()
        messages.append(f"{field}: {message}" if field else message)
    who = f"（{username}）" if username else ""
    return f"第 {index} 条{who}：{'；'.join(messages)}"


def build_personnel_items(
    records: list[dict], cfg: PersonnelSyncConfig
) -> tuple[list[PersonnelCreateRequest], int, list[str]]:
    """映射并校验全部记录；返回 (有效记录, 跳过数, 错误列表)。

    调用方应在有错误时整体中止，保持与 Excel 导入一致的全量校验语义。
    """
    items: list[PersonnelCreateRequest] = []
    errors: list[str] = []
    skipped = 0
    source = cfg.source_name.strip() or DEFAULT_SOURCE_NAME
    for index, record in enumerate(records, start=1):
        mapped = _map_record(record, cfg.field_mapping)
        username = str(mapped.get("username") or "").strip()
        if is_super_admin(username):
            skipped += 1
            continue
        mapped["source"] = source
        try:
            items.append(PersonnelCreateRequest(**mapped))
        except ValidationError as exc:
            errors.append(_format_validation_error(index, username, exc))
    return items, skipped, errors
