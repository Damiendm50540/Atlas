import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import importlib.util

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("app_module", ROOT / "app.py")
app_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app_module)


class RoleChecksTest(unittest.TestCase):
    def setUp(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = Path(db_path)
        app_module.USER_DB_PATH = self.db_path
        app_module.ensure_user_store()
        with sqlite3.connect(app_module.USER_DB_PATH) as conn:
            conn.execute("DELETE FROM users")
            conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                ("admin", app_module._hash_password("admin123"), "admin"),
            )
            conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                ("user", app_module._hash_password("user123"), "user"),
            )
            conn.commit()

    def tearDown(self):
        pass

    def test_admin_and_user_roles_are_distinct(self):
        self.assertTrue(app_module.valid_credentials("admin", "admin123"))
        self.assertTrue(app_module.valid_credentials("user", "user123"))
        self.assertEqual(app_module.get_user_role("admin"), "admin")
        self.assertEqual(app_module.get_user_role("user"), "user")

    def test_admin_only_actions_are_enforced(self):
        self.assertTrue(app_module.user_has_role("admin", "admin"))
        self.assertFalse(app_module.user_has_role("user", "admin"))


if __name__ == "__main__":
    unittest.main()
