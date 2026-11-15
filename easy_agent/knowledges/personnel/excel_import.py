"""Strict, side-effect-free Excel parsing for personnel imports."""

from __future__ import annotations

from io import BytesIO
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook

from easy_agent.knowledges.personnel.models import PersonnelCreateRequest
from easy_agent.utils.auth import is_super_admin

# 旧版列式模板：一行一个人员，部门为单级编号/名称。
LEGACY_EXCEL_COLUMNS = {
    "账号": "username",
    "姓名": "display_name",
    "部门编号": "department_id",
    "部门名称": "department_name",
    "员工编号": "employee_id",
    "邮箱": "email",
    "岗位": "position",
    "手机号": "mobile",
    "状态": "account_status",
}
LEGACY_REQUIRED_FIELDS = {"username", "display_name", "department_id", "department_name"}
LEGACY_FIELD_LABELS = {
    "username": "账号",
    "display_name": "姓名",
    "department_id": "部门编号",
    "department_name": "部门名称",
}

# 新版部门结构模板：覆盖人员管理界面的全部字段（列序即下载模板顺序）。
# 「部门」是上一版模板中「部门名称」的别名，保留以兼容存量文件。
STRUCTURE_EXCEL_COLUMNS = {
    "姓名": "display_name",
    "sso账号": "username",
    "员工编号": "employee_id",
    "账号状态": "account_status",
    "部门编号": "department_id",
    "部门名称": "department_name",
    "部门": "department_name",
    "处室": "division_name",
    "团队": "team_name",
    "岗位": "position",
    "手机号": "mobile",
    "邮箱": "email",
}
STRUCTURE_REQUIRED_FIELDS = {"display_name", "username", "department_name"}
STRUCTURE_FIELD_LABELS = {
    "display_name": "姓名",
    "username": "sso账号",
    "department_name": "部门名称",
}
MAX_IMPORT_ROWS = 5000
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 1000


class PersonnelExcelError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("；".join(errors))
        self.errors = errors


def _normalize_header(value: object) -> str:
    """表头允许用 * 标注界面必填项，匹配时忽略空白、星号和英文大小写。"""

    return str(value or "").strip().rstrip("*＊").strip().casefold()


def _validate_xlsx_container(content: bytes) -> None:
    """Reject malformed or highly inflated Office archives before parsing."""
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if "[Content_Types].xml" not in archive.namelist():
                raise PersonnelExcelError(["文件不是有效的 .xlsx 工作簿"])
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise PersonnelExcelError(["Excel 内部文件数量异常，已拒绝解析"])
            if sum(entry.file_size for entry in entries) > MAX_UNCOMPRESSED_BYTES:
                raise PersonnelExcelError(["Excel 解压后内容不能超过 50MB"])
            _reject_hidden_or_formula_content(archive)
    except (BadZipFile, ElementTree.ParseError, RuntimeError) as exc:
        raise PersonnelExcelError(["文件不是有效的 .xlsx 工作簿"]) from exc


def _reject_hidden_or_formula_content(archive: ZipFile) -> None:
    """Do not let a workbook conceal accounts or derive fields from formulas."""

    workbook_entries = [
        entry for entry in archive.infolist() if entry.filename == "xl/workbook.xml"
    ]
    worksheet_entries = [
        entry
        for entry in archive.infolist()
        if entry.filename.startswith("xl/worksheets/")
        and entry.filename.endswith(".xml")
    ]
    for entry in [*workbook_entries, *worksheet_entries]:
        with archive.open(entry) as stream:
            for _, element in ElementTree.iterparse(stream, events=("end",)):
                tag = element.tag.rsplit("}", 1)[-1]
                hidden = str(element.attrib.get("hidden", "")).strip().lower()
                state = str(element.attrib.get("state", "")).strip().lower()
                if tag in {"row", "col"} and hidden in {"1", "true"}:
                    raise PersonnelExcelError(["Excel 不能包含隐藏行或隐藏列"])
                if tag == "sheet" and state in {"hidden", "veryhidden"}:
                    raise PersonnelExcelError(["Excel 不能包含隐藏工作表"])
                if tag == "f":
                    raise PersonnelExcelError(["Excel 不能包含公式，请先粘贴为纯文本值"])
                element.clear()


def parse_personnel_excel(content: bytes, source: str) -> list[PersonnelCreateRequest]:
    _validate_xlsx_container(content)
    try:
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:
        raise PersonnelExcelError(["文件不是有效的 .xlsx 工作簿"]) from exc

    try:
        sheet = workbook.active
        iterator = sheet.iter_rows(values_only=True)
        raw_headers = next(iterator, None)
        if not raw_headers:
            raise PersonnelExcelError(["Excel 不能为空"])

        headers = [_normalize_header(value) for value in raw_headers]
        # 按表头选择格式：新版部门结构模板（含界面全部字段）带
        # 「sso账号/部门/处室/团队」任一特征列即按新格式校验；否则按旧版列式
        # 模板解析，存量 Excel 仍可导入。
        if any(marker in set(headers) for marker in ("sso账号", "部门", "处室", "团队")):
            source_columns = STRUCTURE_EXCEL_COLUMNS
            required, labels = STRUCTURE_REQUIRED_FIELDS, STRUCTURE_FIELD_LABELS
        else:
            source_columns = LEGACY_EXCEL_COLUMNS
            required, labels = LEGACY_REQUIRED_FIELDS, LEGACY_FIELD_LABELS
        columns = {_normalize_header(key): field for key, field in source_columns.items()}
        present = {columns[header] for header in headers if header in columns}
        missing = [labels[field] for field in sorted(required - present)]
        unknown = [header for header in headers if header and header not in columns]
        header_errors = []
        if missing:
            header_errors.append(f"缺少必填列：{'、'.join(missing)}")
        if unknown:
            header_errors.append(f"存在不支持的列：{'、'.join(unknown)}")
        if len([header for header in headers if header]) != len(set(filter(None, headers))):
            header_errors.append("列名不能重复")
        if header_errors:
            raise PersonnelExcelError(header_errors)

        items: list[PersonnelCreateRequest] = []
        errors: list[str] = []
        seen_usernames: set[str] = set()
        for excel_row, values in enumerate(iterator, start=2):
            if excel_row > MAX_IMPORT_ROWS + 1:
                errors.append(f"最多允许导入 {MAX_IMPORT_ROWS} 行")
                break
            if not any(value not in (None, "") for value in values):
                continue
            # 跳过模板示例行（姓名标注「（示例）」），防止原样导入模板建出假账号。
            first_cell = str(values[0] or "").strip() if values else ""
            if first_cell.endswith("（示例）") or first_cell.endswith("(示例)"):
                continue
            raw = {
                columns[header]: values[index]
                for index, header in enumerate(headers)
                if header and index < len(values)
            }
            raw["source"] = source
            raw.setdefault("account_status", "active")
            try:
                item = PersonnelCreateRequest.model_validate(raw)
            except Exception as exc:
                details = getattr(exc, "errors", lambda: [])()
                message = ", ".join(
                    str(error.get("msg", "格式错误")).removeprefix("Value error, ")
                    for error in details
                ) or "格式错误"
                errors.append(f"第 {excel_row} 行：{message}")
                continue
            if item.username in seen_usernames:
                errors.append(f"第 {excel_row} 行：账号 {item.username} 在文件中重复")
                continue
            if is_super_admin(item.username):
                errors.append(f"第 {excel_row} 行：超级管理员账号不可通过导入修改")
                continue
            seen_usernames.add(item.username)
            items.append(item)
    finally:
        workbook.close()
    if errors:
        raise PersonnelExcelError(errors[:100])
    if not items:
        raise PersonnelExcelError(["Excel 中没有可导入的人员记录"])
    return items
