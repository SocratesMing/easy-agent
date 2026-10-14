"""Personnel-owned extension of the host users table; additive migration only."""

USER_FIELDS = {
    "display_name": "",
    "department_id": "",
    "department_name": "",
    "division_name": "",
    "team_name": "",
    "position": "",
    "mobile": "",
    "account_status": "active",
    "personnel_source": "",
}


def ensure_user_fields(db, cursor):
    for name, default in USER_FIELDS.items():
        db._ensure_column(cursor, "users", name, f"VARCHAR(255) DEFAULT '{default}'")
    db._execute(cursor, """UPDATE users SET department_id=organization_id
        WHERE (department_id IS NULL OR department_id='')
        AND organization_id IS NOT NULL AND organization_id<>''""")


def read_user_fields(row):
    values = {name: row.get(name) or default for name, default in USER_FIELDS.items()}
    values["department_id"] = values["department_id"] or row.get("organization_id", "")
    return values


def save_user_fields(db, cursor, user):
    values = [getattr(user, name) for name in USER_FIELDS]
    values[1] = user.department_id or user.organization_id
    db._execute(cursor,
        "UPDATE users SET " + ", ".join(f"{name}=?" for name in USER_FIELDS) + " WHERE user_id=?",
        tuple(values) + (user.user_id,))
