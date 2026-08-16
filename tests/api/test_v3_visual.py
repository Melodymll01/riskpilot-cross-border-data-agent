"""V3 图片证据上传、文本搜图、下载与隔离测试。"""

from __future__ import annotations

import io
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from app.container import AppContainer


def _png(color: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(output, format="PNG")
    return output.getvalue()


def _setup_case(client: TestClient) -> tuple[str, str]:
    workspace = client.post(
        "/api/v3/workspaces",
        json={"name": "图片证据测试"},
    ).json()
    case = client.post(
        "/api/v3/cases",
        json={"workspace_id": workspace["workspace_id"], "title": "机房审计"},
    ).json()
    return workspace["workspace_id"], case["case_id"]


def _switch_actor(client: TestClient, actor_id: str) -> None:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    client.cookies.set(
        container.settings.cookie_name,
        container.auth.issue_jwt(actor_id),
    )


class TestVisualEvidence:
    def test_upload_search_and_download(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        _, case_id = _setup_case(client)
        content = _png((220, 20, 20))

        uploaded = client.post(
            f"/api/v3/cases/{case_id}/visual-assets",
            files={"file": ("red-server.png", content, "image/png")},
            data={"caption": "红色机柜告警灯"},
        )

        assert uploaded.status_code == 201, uploaded.text
        asset = uploaded.json()
        assert asset["width"] == 32
        assert asset["height"] == 32
        assert asset["caption"] == "红色机柜告警灯"

        searched = client.get(
            f"/api/v3/cases/{case_id}/visual-assets/search",
            params={"query": "红色机柜告警灯", "top_k": 3},
        )
        assert searched.status_code == 200
        assert searched.json()["hits"][0]["asset"]["asset_id"] == asset["asset_id"]

        downloaded = client.get(
            f"/api/v3/cases/{case_id}/visual-assets/{asset['asset_id']}/content"
        )
        assert downloaded.status_code == 200
        assert downloaded.content == content
        assert downloaded.headers["content-type"].startswith("image/png")

    def test_invalid_image_rejected(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        _, case_id = _setup_case(client)

        response = client.post(
            f"/api/v3/cases/{case_id}/visual-assets",
            files={"file": ("fake.png", b"not-an-image", "image/png")},
        )

        assert response.status_code == 400

    def test_viewer_cannot_upload_but_can_search(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        workspace_id, case_id = _setup_case(client)
        client.put(
            f"/api/v3/workspaces/{workspace_id}/members/github:viewer",
            json={"role": "viewer"},
        )
        _switch_actor(client, "github:viewer")

        upload = client.post(
            f"/api/v3/cases/{case_id}/visual-assets",
            files={"file": ("image.png", _png((0, 0, 0)), "image/png")},
        )
        assert upload.status_code == 403

        search = client.get(
            f"/api/v3/cases/{case_id}/visual-assets/search",
            params={"query": "机柜"},
        )
        assert search.status_code == 200
        assert search.json()["hits"] == []

    def test_outsider_cannot_discover_case_images(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        _, case_id = _setup_case(client)
        _switch_actor(client, "github:outsider")

        response = client.get(
            f"/api/v3/cases/{case_id}/visual-assets/search",
            params={"query": "机柜"},
        )

        assert response.status_code == 404
