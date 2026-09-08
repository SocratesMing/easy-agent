from contextlib import contextmanager


def test_count_users_supports_mysql_dict_cursor(db, monkeypatch):
    class Cursor:
        def execute(self, query, params=None):
            self.query = query

        def fetchone(self):
            return {"total": 3}

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def get_connection():
        yield Connection()

    monkeypatch.setattr(db, "get_connection", get_connection)

    assert db.count_users() == 3
