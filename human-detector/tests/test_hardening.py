import sys
from pathlib import Path

import unittest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as app_module


def login(username, password):
    c = TestClient(app_module.app)
    r = c.post("/api/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code == 303
    return c


def user_client():
    return login(app_module.APP_USER_USERNAME, app_module.APP_USER_PASSWORD)


def admin_client():
    return login(app_module.APP_ADMIN_USERNAME, app_module.APP_ADMIN_PASSWORD)


class HardeningTests(unittest.TestCase):
    def test_plain_user_cannot_control_detector(self):
        for path, body in [
            ("/api/start", {"url": "rtsp://192.0.2.1/x"}), ("/api/stop", None), ("/api/pause", None),
            ("/api/resume", None), ("/api/seek", {"t": 1}),
            ("/api/alarm/arm", {"code": "1234"}), ("/api/alarm/disarm", {"code": "1234"}),
        ]:
            c = user_client()
            r = c.post(path, json=body) if body is not None else c.post(path)
            self.assertEqual(r.status_code, 403, path)

    def test_start_rejects_non_rtsp_or_local_targets(self):
        for url in ["file:///etc/passwd", "/etc/passwd", "http://example.com/x",
                    "rtsp://127.0.0.1/stream", "rtsp://169.254.169.254/x"]:
            self.assertEqual(admin_client().post("/api/start", json={"url": url}).status_code, 400, url)

    def test_upload_rejects_bad_extension(self):
        r = admin_client().post("/api/upload", files={"file": ("x.sh", b"echo", "text/plain")})
        self.assertEqual(r.status_code, 400)

    def test_disarm_is_rate_limited(self):
        c = admin_client()
        app_module.DISARM_ATTEMPTS.clear()
        codes = [c.post("/api/alarm/disarm", json={"code": "0000"}).status_code
                 for _ in range(app_module.MAX_LOGIN_ATTEMPTS + 1)]
        self.assertEqual(codes[-1], 429)

    def test_openapi_docs_disabled(self):
        self.assertEqual(admin_client().get("/openapi.json").status_code, 404)

    def test_captures_and_people_photos_require_login(self):
        c = TestClient(app_module.app)
        self.assertEqual(c.get("/captures/x.jpg", follow_redirects=False).status_code, 303)
        self.assertEqual(c.get("/people-photos/x.jpg", follow_redirects=False).status_code, 303)

    def test_user_cannot_read_people_photos_or_export(self):
        c = user_client()
        self.assertEqual(c.get("/people-photos/x.jpg").status_code, 403)
        self.assertEqual(c.get("/api/events/export.csv").status_code, 403)

    def test_logout_revokes_session_server_side(self):
        c = admin_client()
        token = c.cookies.get("argus_session")
        self.assertEqual(c.get("/api/me").status_code, 200)
        c.post("/api/logout", follow_redirects=False)
        stale = TestClient(app_module.app)
        stale.cookies.set("argus_session", token)
        self.assertEqual(stale.get("/api/me").status_code, 401)

    def test_forged_cookie_rejected(self):
        c = TestClient(app_module.app)
        c.cookies.set("argus_session", "YWRtaW46MTox")
        self.assertEqual(c.get("/api/me").status_code, 401)

    def test_csv_formula_injection_neutralised(self):
        from history import History
        h = History.__new__(History)
        import threading, sqlite3
        h.lock = threading.Lock()
        h.db = sqlite3.connect(":memory:")
        h.db.row_factory = sqlite3.Row
        h.db.execute("CREATE TABLE events (id, started_at, ended_at, source, source_type, video_time, faces, score, alarm, photo)")
        h.db.execute("INSERT INTO events VALUES (1, 0, 1, '=HYPERLINK(\"x\")', 'flux', NULL, 1, .9, 0, 'a.jpg')")
        h._where = lambda a, s, u: ("", [])
        self.assertIn("'=HYPERLINK", h.export_csv())


if __name__ == "__main__":
    unittest.main()
