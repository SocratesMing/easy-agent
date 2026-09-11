"""Personnel administration: authorization, validation and atomic import."""

from io import BytesIO

import pytest
from openpyxl import Workbook

from easy_agent.app import app
from easy_agent.middleware import get_current_username
from easy_agent.models.db import UserModel
from easy_agent.personnel import repository as personnel_repository


def _xlsx(headers, rows) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _as_admin(client):
    app.dependency_overrides[get_current_username] = lambda: "admin"
    return client


def _payload(**overrides):
    data = {
        "username": "zhangsan",
        "display_name": "张三",
        "department_id": "dept-market",
        "department_name": "金融市场部",
        "employee_id": "E0001",
        "email": "zhangsan@example.com",
        "position": "分析师",
        "mobile": "13800000000",
        "account_status": "active",
        "source": "HR导出_2026-09",
    }
    data.update(overrides)
    return data


def test_only_admin_can_access_personnel(client):
    response = client.get("/api/personnel/users")
    assert response.status_code == 403
    response = client.post("/api/personnel/users", json=_payload())
    assert response.status_code == 403


def test_admin_can_create_filter_and_edit_personnel(client, db):
    client = _as_admin(client)
    created = client.post("/api/personnel/users", json=_payload())
    assert created.status_code == 201, created.text
    record = created.json()
    assert record["department_id"] == "dept-market"
    assert record["source"] == "HR导出_2026-09"
    assert db.verify_user_password("zhangsan", "123456") is not None
    assert db.get_user_by_username("zhangsan").organization_id == "dept-market"

    listed = client.get("/api/personnel/users", params={"keyword": "张三"})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    update = _payload(
        username=None,
        department_id="dept-risk",
        department_name="风险管理部",
        account_status="disabled",
        source="管理员手工调整",
    )
    update.pop("username")
    edited = client.put(f"/api/personnel/users/{record['user_id']}", json=update)
    assert edited.status_code == 200, edited.text
    assert edited.json()["account_status"] == "disabled"
    assert db.get_user_by_username("zhangsan").organization_id == "dept-risk"


def test_personnel_username_cannot_collide_in_workspace_path(client):
    client = _as_admin(client)
    response = client.post(
        "/api/personnel/users",
        json=_payload(username="john.doe", employee_id="E-DOT"),
    )
    assert response.status_code == 422
    assert "下划线或连字符" in response.text


def test_personnel_list_is_paginated(client, db):
    client = _as_admin(client)
    for index in range(3):
        db.create_user(
            UserModel(
                user_id=f"page-user-{index}",
                username=f"page_user_{index}",
                password_hash="",
                display_name=f"分页用户{index}",
                department_id="dept-page",
                organization_id="dept-page",
                created_at=f"2026-09-08T00:00:0{index}",
                updated_at=f"2026-09-08T00:00:0{index}",
            )
        )

    first = client.get(
        "/api/personnel/users",
        params={"keyword": "page_user", "page": 1, "page_size": 2},
    )
    second = client.get(
        "/api/personnel/users",
        params={"keyword": "page_user", "page": 2, "page_size": 2},
    )
    assert first.status_code == 200
    assert first.json()["total"] == 3
    assert first.json()["page"] == 1
    assert len(first.json()["items"]) == 2
    assert second.json()["page"] == 2
    assert len(second.json()["items"]) == 1


def test_disabled_personnel_cannot_login(auth_client, db):
    client = _as_admin(auth_client)
    created = client.post(
        "/api/personnel/users", json=_payload(username="disabled_user", account_status="disabled")
    )
    assert created.status_code == 201
    app.dependency_overrides.pop(get_current_username, None)
    login = client.post(
        "/api/auth/login", json={"username": "disabled_user", "password": "123456"}
    )
    assert login.status_code == 403
    assert login.json()["detail"] == "账号已停用，请联系管理员"
    assert db.increment_user_token_version(
        "disabled_user", require_active=True
    ) == 0


def test_admin_cannot_be_overwritten(client):
    client = _as_admin(client)
    response = client.post("/api/personnel/users", json=_payload(username="admin"))
    assert response.status_code == 409


def test_excel_import_creates_and_updates_with_frontend_source(client, db):
    client = _as_admin(client)
    headers = ["账号", "姓名", "部门编号", "部门名称", "员工编号", "状态"]
    first = _xlsx(
        headers,
        [
            ["alice_personnel", "爱丽丝", "dept-a", "部门A", "E1001", "启用"],
            ["bob_personnel", "鲍勃", "dept-b", "部门B", "E1002", "停用"],
        ],
    )
    response = client.post(
        "/api/personnel/import",
        data={"source": "月度花名册"},
        files={"file": ("人员.xlsx", first, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "total": 2,
        "created": 2,
        "updated": 0,
        "source": "月度花名册",
        "default_password": "123456",
    }
    assert db.get_user_by_username("alice_personnel").personnel_source == "月度花名册"

    second = _xlsx(
        headers,
        [["alice_personnel", "Alice", "dept-c", "部门C", "E1001", "active"]],
    )
    updated = client.post(
        "/api/personnel/import",
        data={"source": "HR接口补录"},
        files={"file": ("人员.xlsx", second, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["updated"] == 1
    user = db.get_user_by_username("alice_personnel")
    assert user.department_id == "dept-c"
    assert user.organization_id == "dept-c"
    assert user.personnel_source == "HR接口补录"


def test_excel_import_hashes_the_shared_initial_password_once(client, monkeypatch):
    client = _as_admin(client)
    calls = []

    def fake_hash(password):
        calls.append(password)
        return "not-a-real-hash"

    monkeypatch.setattr(personnel_repository, "hash_password", fake_hash)
    content = _xlsx(
        ["账号", "姓名", "部门编号", "部门名称"],
        [
            ["batch_user_1", "批量用户1", "dept-a", "部门A"],
            ["batch_user_2", "批量用户2", "dept-a", "部门A"],
        ],
    )
    response = client.post(
        "/api/personnel/import",
        data={"source": "批量性能测试"},
        files={"file": ("人员.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200, response.text
    assert calls == ["123456"]


def test_excel_validation_is_atomic(client, db):
    client = _as_admin(client)
    content = _xlsx(
        ["账号", "姓名", "部门编号", "部门名称"],
        [
            ["valid_user", "有效用户", "dept-a", "部门A"],
            ["bad user", "无效用户", "dept-a", "部门A"],
        ],
    )
    response = client.post(
        "/api/personnel/import",
        data={"source": "测试来源"},
        files={"file": ("人员.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 422
    assert db.get_user_by_username("valid_user") is None


def test_excel_rejects_wrong_format_and_missing_headers(client):
    client = _as_admin(client)
    wrong_type = client.post(
        "/api/personnel/import",
        data={"source": "测试"},
        files={"file": ("人员.xls", b"old excel", "application/vnd.ms-excel")},
    )
    assert wrong_type.status_code == 415

    corrupt = client.post(
        "/api/personnel/import",
        data={"source": "测试"},
        files={"file": ("人员.xlsx", b"not-a-workbook", "application/octet-stream")},
    )
    assert corrupt.status_code == 422
    assert "有效的 .xlsx" in str(corrupt.json()["detail"])

    missing = _xlsx(["账号", "姓名"], [["alice", "爱丽丝"]])
    response = client.post(
        "/api/personnel/import",
        data={"source": "测试"},
        files={"file": ("人员.xlsx", missing, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 422
    assert "部门编号" in str(response.json()["detail"])


@pytest.mark.parametrize("unsafe_kind", ["hidden-row", "formula"])
def test_excel_rejects_concealed_or_formula_accounts(client, unsafe_kind):
    client = _as_admin(client)
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["账号", "姓名", "部门编号", "部门名称"])
    sheet.append(["concealed_user", "隐藏用户", "dept-a", "部门A"])
    if unsafe_kind == "hidden-row":
        sheet.row_dimensions[2].hidden = True
    else:
        sheet["A2"] = '=CONCAT("formula", "_user")'
    output = BytesIO()
    workbook.save(output)
    workbook.close()

    response = client.post(
        "/api/personnel/import",
        data={"source": "安全校验"},
        files={"file": ("人员.xlsx", output.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 422
    expected = "隐藏行" if unsafe_kind == "hidden-row" else "公式"
    assert expected in str(response.json()["detail"])


def test_template_is_admin_only_and_valid_xlsx(client):
    assert client.get("/api/personnel/import-template").status_code == 403
    client = _as_admin(client)
    response = client.get("/api/personnel/import-template")
    assert response.status_code == 200
    assert response.content.startswith(b"PK")
    assert "personnel_import_template.xlsx" in response.headers["content-disposition"]
