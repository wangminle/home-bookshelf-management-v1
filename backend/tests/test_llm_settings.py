"""Owner 后台多模态模型配置：密钥不回显，Member 不可读写。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import app
from app.models import Member, OperationLog
from app.services import agent_access

SECRET = "sk-test-secret-key-9911"


def _member_client(db_session: Session) -> TestClient:
    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    return TestClient(app, client=("127.0.0.1", 50000))


def test_owner_get_defaults(client: TestClient) -> None:
    r = client.get("/api/v1/settings/llm")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["enabled"] is False
    assert data["api_key_configured"] is False
    assert data["image_detail"] == "auto"
    assert "api_key" not in data


def test_owner_save_masks_key_and_keeps_it(client: TestClient, db_session: Session) -> None:
    body = {
        "enabled": True,
        "display_name": "识书",
        "base_url": "https://api.example.com/v1/",
        "model_id": "gpt-4o",
        "api_key": SECRET,
        "timeout_seconds": 30,
        "max_tokens": 512,
        "temperature": 0.2,
        "image_detail": "high",
    }
    r = client.put("/api/v1/settings/llm", json=body)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert SECRET not in r.text
    assert data["api_key_configured"] is True
    assert data["api_key_hint"] == "····9911"
    assert data["base_url"] == "https://api.example.com/v1"
    assert data["image_detail"] == "high"

    r = client.put("/api/v1/settings/llm", json={"display_name": "识书-改"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["api_key_hint"] == "····9911"
    assert r.json()["data"]["display_name"] == "识书-改"

    row = db_session.query(OperationLog).filter(OperationLog.action == "llm_settings.update").all()
    assert row
    for item in row:
        assert SECRET not in (item.payload or "")
        payload = json.loads(item.payload or "{}")
        assert payload.get("api_key") in (None, "set")


def _seed_enabled_with_key(client: TestClient) -> None:
    r = client.put(
        "/api/v1/settings/llm",
        json={
            "enabled": True,
            "display_name": "识书",
            "base_url": "https://api.example.com/v1",
            "model_id": "gpt-4o",
            "api_key": SECRET,
            "timeout_seconds": 30,
        },
    )
    assert r.status_code == 200, r.text


def test_explicit_null_api_key_keeps_stored_key(client: TestClient, db_session: Session) -> None:
    _seed_enabled_with_key(client)
    r = client.put("/api/v1/settings/llm", json={"api_key": None})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["api_key_configured"] is True
    assert data["api_key_hint"] == "····9911"

    rows = db_session.query(OperationLog).filter(OperationLog.action == "llm_settings.update").all()
    assert rows
    for item in rows:
        payload = json.loads(item.payload or "{}")
        assert payload.get("api_key") != "cleared"


def test_explicit_null_fields_are_noops(client: TestClient, db_session: Session) -> None:
    _seed_enabled_with_key(client)
    before = client.get("/api/v1/settings/llm").json()["data"]

    r = client.put(
        "/api/v1/settings/llm",
        json={
            "display_name": None,
            "base_url": None,
            "model_id": None,
            "image_detail": None,
            "enabled": None,
            "timeout_seconds": None,
            "max_tokens": None,
            "temperature": None,
        },
    )
    assert r.status_code == 200, r.text

    after = r.json()["data"]
    for key in (
        "display_name",
        "base_url",
        "model_id",
        "image_detail",
        "enabled",
        "timeout_seconds",
        "max_tokens",
        "temperature",
        "api_key_hint",
    ):
        assert after[key] == before[key], key
    assert after["api_key_configured"] is True

    log = db_session.query(OperationLog).filter(OperationLog.action == "llm_settings.update").order_by(OperationLog.id.desc()).first()
    payload = json.loads(log.payload or "{}") if log else {}
    assert "api_key" not in payload


def test_omitted_api_key_unchanged(client: TestClient) -> None:
    _seed_enabled_with_key(client)
    r = client.put("/api/v1/settings/llm", json={"display_name": "识书-改"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["api_key_hint"] == "····9911"
    assert r.json()["data"]["display_name"] == "识书-改"


def test_normal_update_still_works(client: TestClient) -> None:
    _seed_enabled_with_key(client)
    r = client.put(
        "/api/v1/settings/llm",
        json={
            "display_name": "新名字",
            "timeout_seconds": 45,
            "image_detail": "low",
            "enabled": False,
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["display_name"] == "新名字"
    assert data["timeout_seconds"] == 45
    assert data["image_detail"] == "low"
    assert data["enabled"] is False
    assert data["api_key_hint"] == "····9911"


def test_empty_api_key_clears(client: TestClient) -> None:
    r = client.put(
        "/api/v1/settings/llm",
        json={
            "enabled": True,
            "base_url": "https://api.example.com/v1",
            "model_id": "gpt-4o",
            "api_key": SECRET,
        },
    )
    assert r.status_code == 200, r.text
    r = client.put("/api/v1/settings/llm", json={"enabled": False, "api_key": ""})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["api_key_configured"] is False


def test_enable_without_key_rejected(client: TestClient) -> None:
    r = client.put(
        "/api/v1/settings/llm",
        json={"enabled": True, "base_url": "https://api.example.com/v1", "model_id": "gpt-4o"},
    )
    assert r.status_code == 422


def test_rejects_key_inside_base_url(client: TestClient) -> None:
    r = client.put(
        "/api/v1/settings/llm",
        json={"base_url": "https://user:secret@api.example.com/v1"},
    )
    assert r.status_code == 422


def test_anonymous_and_member_forbidden(anon_client: TestClient, client: TestClient, db_session: Session) -> None:
    assert anon_client.get("/api/v1/settings/llm").status_code == 401
    member = Member(name="模型配置成员", role="member")
    db_session.add(member)
    db_session.commit()
    agent_access.set_member_password(db_session, member, "member-pass-12345")
    c = _member_client(db_session)
    login = c.post("/auth/login", json={"username": member.username, "password": "member-pass-12345"})
    assert login.status_code == 200, login.text
    assert c.get("/api/v1/settings/llm").status_code == 403
    assert c.put(
        "/api/v1/settings/llm",
        json={"display_name": "不行"},
        headers={"Origin": "http://127.0.0.1"},
    ).status_code == 403
