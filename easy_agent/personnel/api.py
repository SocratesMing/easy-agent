"""Admin-only personnel management API."""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from ..db import Database, get_database
from ..middleware import get_current_username
from .excel_import import PersonnelExcelError, parse_personnel_excel
from .models import (
    PersonnelCreateRequest,
    PersonnelImportResponse,
    PersonnelListResponse,
    PersonnelRecord,
    PersonnelUpdateRequest,
)
from .repository import (
    PersonnelConflictError,
    PersonnelNotFoundError,
    create_personnel,
    import_personnel,
    list_personnel,
    update_personnel,
)
from ..knowledge.operations_repository import KnowledgeOperationsRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/personnel", tags=["Personnel"])
MAX_EXCEL_BYTES = 5 * 1024 * 1024


def _audit(
    request: Request,
    db: Database,
    admin: str,
    action: str,
    object_id: str,
    details: dict | None = None,
) -> None:
    user = db.get_user_by_username(admin)
    KnowledgeOperationsRepository(db).record_audit(
        request_id=str(getattr(request.state, "request_id", "unknown")),
        actor_user_id=user.user_id if user else admin,
        actor_username=admin,
        action=action,
        object_type="personnel",
        object_id=object_id,
        details=details or {},
    )


def require_admin(
    request: Request,
    username: Annotated[str, Depends(get_current_username)],
    db: Annotated[Database, Depends(get_database)],
) -> str:
    if username != "admin":
        raise HTTPException(status_code=403, detail="仅 admin 账户可以配置人员信息")
    user = db.get_user_by_username(username)
    request.state.actor_user_id = user.user_id if user else username
    request.state.actor_username = username
    return username


@router.get("/users", response_model=PersonnelListResponse, summary="查询人员信息")
def get_personnel_users(
    _: Annotated[str, Depends(require_admin)],
    db: Annotated[Database, Depends(get_database)],
    keyword: str = Query(default="", max_length=100),
    account_status: str = Query(default="", pattern="^(|active|disabled)$"),
    department_id: str = Query(default="", max_length=255),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    total, items = list_personnel(
        db,
        keyword=keyword.strip(),
        account_status=account_status,
        department_id=department_id.strip(),
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    return PersonnelListResponse(
        total=total,
        items=[PersonnelRecord(**item) for item in items],
        page=page,
        page_size=page_size,
    )


@router.post("/users", response_model=PersonnelRecord, status_code=201, summary="手动新增人员")
def add_personnel_user(
    payload: PersonnelCreateRequest,
    request: Request,
    admin: Annotated[str, Depends(require_admin)],
    db: Annotated[Database, Depends(get_database)],
):
    try:
        record = create_personnel(db, payload)
    except PersonnelConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info("[人员管理] 手动新增 | 管理员: %s | 账号: %s | 来源: %s", admin, payload.username, payload.source)
    _audit(request, db, admin, "personnel.create", str(record["user_id"]), {"source": payload.source})
    return PersonnelRecord(**record)


@router.put("/users/{user_id}", response_model=PersonnelRecord, summary="编辑人员信息")
def edit_personnel_user(
    user_id: str,
    payload: PersonnelUpdateRequest,
    request: Request,
    admin: Annotated[str, Depends(require_admin)],
    db: Annotated[Database, Depends(get_database)],
):
    try:
        record = update_personnel(db, user_id, payload)
    except PersonnelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PersonnelConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info("[人员管理] 编辑 | 管理员: %s | user_id: %s | 来源: %s", admin, user_id, payload.source)
    _audit(request, db, admin, "personnel.update", user_id, {"source": payload.source})
    return PersonnelRecord(**record)


@router.post("/import", response_model=PersonnelImportResponse, summary="Excel 导入人员")
def import_personnel_excel(
    request: Request,
    admin: Annotated[str, Depends(require_admin)],
    db: Annotated[Database, Depends(get_database)],
    file: UploadFile = File(...),
    source: str = Form(..., min_length=1, max_length=255),
):
    source = source.strip()
    if not source:
        raise HTTPException(status_code=422, detail="人员信息来源不能为空")
    filename = (file.filename or "").lower()
    if not filename.endswith(".xlsx"):
        raise HTTPException(status_code=415, detail="仅支持 .xlsx 格式")
    # A synchronous route is deliberately used here. FastAPI executes it in its
    # worker thread pool, so openpyxl and bcrypt cannot block the main event loop.
    content = file.file.read(MAX_EXCEL_BYTES + 1)
    if len(content) > MAX_EXCEL_BYTES:
        raise HTTPException(status_code=413, detail="Excel 文件不能超过 5MB")
    try:
        items = parse_personnel_excel(content, source)
        created, updated = import_personnel(db, items)
    except PersonnelExcelError as exc:
        raise HTTPException(status_code=422, detail={"message": "Excel 校验失败", "errors": exc.errors}) from exc
    except PersonnelConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info(
        "[人员管理] Excel导入 | 管理员: %s | 来源: %s | 新增: %s | 更新: %s",
        admin, source, created, updated,
    )
    _audit(
        request, db, admin, "personnel.import", "excel-batch",
        {"source": source, "total": len(items), "created": created, "updated": updated},
    )
    return PersonnelImportResponse(
        total=len(items), created=created, updated=updated, source=source
    )


@router.get("/import-template", summary="下载人员导入模板")
def download_import_template(_: Annotated[str, Depends(require_admin)]):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "人员信息"
    headers = ["账号", "姓名", "部门编号", "部门名称", "员工编号", "邮箱", "岗位", "手机号", "状态"]
    sheet.append(headers)
    sheet.append(["zhangsan", "张三", "dept-market", "金融市场部", "E0001", "zhangsan@example.com", "分析师", "13800000000", "启用"])
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2563EB")
    for column in "ABCDEFGHI":
        sheet.column_dimensions[column].width = 18
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="personnel_import_template.xlsx"'},
    )
