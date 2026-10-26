import time

from domain.models import User


def test_memory_api_requires_real_account_and_rejects_owner_injection(client, container):
    assert client.get("/api/v2/memory").status_code == 401
    client.post("/api/v2/auth/anonymous")
    assert client.get("/api/v2/memory").status_code == 403
    user = User(
        user_id="github:memory-user",
        provider="github",
        provider_id="memory-user",
        display_name="Memory User",
        created_at=time.time(),
        last_active_at=time.time(),
    )
    container.user_repo.upsert(user)
    client.cookies.set(container.settings.cookie_name, container.auth.issue_jwt(user.user_id))
    created = client.post(
        "/api/v2/memory", json={"content": "回答优先列出来源", "kind": "preference"}
    )
    assert created.status_code == 201, created.text
    memory_id = created.json()["memory_id"]
    assert client.get("/api/v2/memory").json()["memories"][0]["memory_id"] == memory_id
    assert (
        client.post("/api/v2/memory", json={"content": "偏好", "owner_id": "other"}).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/v2/memory/{memory_id}", json={"content": "回答简洁", "expected_revision": 1}
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/api/v2/memory/{memory_id}", json={"content": "旧编辑", "expected_revision": 1}
        ).status_code
        == 409
    )
    assert client.delete(f"/api/v2/memory/{memory_id}").status_code == 200
    assert client.get("/api/v2/memory").json()["memories"] == []
    fact = client.post("/api/v2/memory", json={"content": "公司在上海", "kind": "fact"}).json()
    updated = client.patch(
        f"/api/v2/memory/{fact['memory_id']}",
        json={
            "content": "公司在北京",
            "expected_revision": 1,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["expires_at"] == fact["expires_at"]
    assert (
        client.patch(
            f"/api/v2/memory/{fact['memory_id']}",
            json={
                "content": "公司在北京",
                "expected_revision": 2,
                "expires_at": None,
            },
        ).status_code
        == 400
    )
