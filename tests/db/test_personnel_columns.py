def test_new_user_has_personnel_columns(db):
    db.register_user("alice", "secret123", employee_id="E001")
    u = db.get_user_by_username("alice")
    assert u.account_status == "active"
    assert u.display_name == "" and u.department_id == "" and u.department_name == ""
    assert u.position == "" and u.mobile == "" and u.personnel_source == ""
