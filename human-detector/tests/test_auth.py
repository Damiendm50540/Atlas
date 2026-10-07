import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as app_module

client = TestClient(app_module.app)


def test_unauthenticated_status_requires_login():
    response = client.get("/api/status")
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentification requise"


def test_valid_login_sets_cookie_and_redirects():
    response = client.post(
        "/api/login",
        data={"username": app_module.APP_USERNAME, "password": app_module.APP_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "argus_session" in response.cookies


def test_logout_clears_session_cookie_and_redirects():
    client.post(
        "/api/login",
        data={"username": app_module.APP_USERNAME, "password": app_module.APP_PASSWORD},
        follow_redirects=False,
    )
    response = client.post("/api/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert response.cookies["argus_session"].expires is not None


if __name__ == "__main__":
    test_unauthenticated_status_requires_login()
    test_valid_login_sets_cookie_and_redirects()
    test_logout_clears_session_cookie_and_redirects()
    print("auth-ok")
