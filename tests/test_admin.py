import re

from fastapi.testclient import TestClient

from backend.app.db import get_session_factory
from backend.app.main import app
from backend.app.models import User


def test_admin_page_requires_login() -> None:
    with TestClient(app) as client:
        response = client.get("/admin/users", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login")


def test_wrong_password_does_not_create_session() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/admin/login",
            data={"username": "admin", "password": "wrong", "next_url": "/admin/users"},
        )

    assert response.status_code == 401
    assert "用户名或密码错误" in response.text


def test_admin_can_login_search_and_disable_user() -> None:
    with TestClient(app) as client:
        session_factory = get_session_factory()
        with session_factory() as db:
            db.add(User(nickname="测试学员", phone_last4="8000"))
            db.commit()

        login = client.post(
            "/admin/login",
            data={
                "username": "admin",
                "password": "test-admin-password",
                "next_url": "/admin/users",
            },
            follow_redirects=False,
        )
        assert login.status_code == 303

        page = client.get("/admin/users?keyword=测试")
        assert page.status_code == 200
        assert "测试学员" in page.text
        assert "****8000" in page.text
        assert page.text.count("<h1>") == 1

        csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
        user_match = re.search(r'action="/admin/users/([^"]+)/toggle"', page.text)
        assert csrf_match and user_match

        toggled = client.post(
            f"/admin/users/{user_match.group(1)}/toggle",
            data={"csrf_token": csrf_match.group(1)},
            follow_redirects=False,
        )
        assert toggled.status_code == 303

        refreshed = client.get("/admin/users?keyword=测试")
        assert "已禁用" in refreshed.text


def test_admin_mutation_rejects_invalid_csrf() -> None:
    with TestClient(app) as client:
        client.post(
            "/admin/login",
            data={
                "username": "admin",
                "password": "test-admin-password",
                "next_url": "/admin/users",
            },
        )
        response = client.post(
            "/admin/users/missing/toggle",
            data={"csrf_token": "wrong"},
            follow_redirects=False,
        )

    assert response.status_code == 403

