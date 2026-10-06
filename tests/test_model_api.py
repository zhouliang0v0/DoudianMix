"""Capability tests and explicit copy assistance preserve legacy contracts."""

import asyncio
import base64
from concurrent import futures
import io
import json
import threading

from fastapi import testclient
import httpx
from PIL import Image
import pytest

from backend import main


@pytest.mark.parametrize("method", ["read_draft", "read_image"])
def test_blocked_copy_storage_does_not_block_unrelated_get(
    tmp_path, monkeypatch, method
):
    entered = threading.Event()
    release = threading.Event()
    app = main.create_app(
        tmp_path,
        httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"choices": [{"message": {"content": "OK"}}]}
            )
        ),
    )
    draft_id = app.state.assets.create_draft()["draftId"]
    app.state.assets.add_image(draft_id, png(), "product.png")
    original = getattr(app.state.assets, method)

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(app.state.assets, method, blocked)
    with testclient.TestClient(app, base_url="http://127.0.0.1:8766") as client:
        configure(client)
        with futures.ThreadPoolExecutor(max_workers=2) as executor:
            copy = executor.submit(
                client.post, "/api/assist-copy", json={"draftId": draft_id}
            )
            try:
                assert entered.wait(5)
                unrelated = executor.submit(client.get, "/api/roles")
                assert unrelated.result(timeout=2).status_code == 200
                assert not copy.done()
            finally:
                release.set()
            assert copy.result(timeout=5).status_code == 200


@pytest.mark.parametrize("role", ["vision", "image"])
@pytest.mark.parametrize("credentials", [True, False])
def test_reconfiguration_during_capability_test_stays_untested(
    tmp_path, role, credentials
):
    entered = threading.Event()
    release = threading.Event()

    async def provider(request):
        del request
        entered.set()
        await asyncio.to_thread(release.wait, 10)
        return httpx.Response(
            200,
            json=(
                {"data": [{"b64_json": base64.b64encode(png()).decode()}]}
                if role == "image"
                else {"choices": [{"message": {"content": "OK"}}]}
            ),
        )

    app = main.create_app(tmp_path, httpx.MockTransport(provider))
    with testclient.TestClient(app, base_url="http://127.0.0.1:8766") as client:
        configure(client, role)
        with futures.ThreadPoolExecutor(max_workers=1) as executor:
            testing = executor.submit(
                client.post, "/api/roles/test", json={"role": role}
            )
            try:
                assert entered.wait(5)
                # Even saving identical values replaces the tested profile version.
                if credentials:
                    assert configure(client, role).status_code == 200
                else:
                    assert (
                        client.post(
                            "/api/roles",
                            json={
                                "role": role,
                                "provider": "openai",
                                "baseUrl": "https://replacement.test/v1",
                                "model": "replacement",
                                "apiKey": "",
                            },
                        ).status_code
                        == 200
                    )
            finally:
                release.set()
            assert testing.result(timeout=5).status_code == 200
        assert client.get("/api/roles").json()["roles"][role]["tested"] is False


def png(color="blue"):
    """Create a synthetic product or provider image."""
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buffer, "PNG")
    return buffer.getvalue()


def configure(client, role="vision", provider="openai"):
    """Configure only synthetic credentials."""
    return client.post(
        "/api/roles",
        json={
            "role": role,
            "provider": provider,
            "baseUrl": "https://example.test/v1",
            "model": "test-model",
            "apiKey": "fake-secret",
        },
    )


@pytest.mark.parametrize("role", ["vision", "image"])
def test_role_test_uses_built_in_png_and_marks_only_success(tmp_path, role):
    sent = []
    fail = [True]

    def provider(request):
        payload = request.read()
        sent.append(payload)
        if fail[0]:
            return httpx.Response(
                400,
                json={"error": {"message": "image unsupported fake-secret"}},
            )
        if role == "image":
            return httpx.Response(
                200,
                json={
                    "model": "actual-image",
                    "data": [
                        {"b64_json": base64.b64encode(png("red")).decode()}
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "model": "actual-vision",
                "choices": [{"message": {"content": "红色"}}],
            },
        )

    app = main.create_app(tmp_path, httpx.MockTransport(provider))
    with testclient.TestClient(app, base_url="http://127.0.0.1:8766") as client:
        draft_id = app.state.assets.create_draft()["draftId"]
        product_image = png()
        app.state.assets.add_image(draft_id, product_image, "product.png")
        configure(client, role)
        response = client.post("/api/roles/test", json={"role": role})
        assert response.status_code == 502
        assert "fake-secret" not in response.text
        assert client.get("/api/roles").json()["roles"][role]["tested"] is False
        fail[0] = False
        response = client.post("/api/roles/test", json={"role": role})
        assert response.json() == {
            "ok": True,
            "role": role,
            "responseModel": "actual-" + role,
        }
        assert client.get("/api/roles").json()["roles"][role]["tested"] is True
        if role == "vision":
            content = json.loads(sent[-1])["messages"][0]["content"]
            sent_image = base64.b64decode(
                content[1]["image_url"]["url"].split(",")[1]
            )
            assert "红色测试色块，请说明已知和未知信息" in content[0]["text"]
        else:
            sent_image = (
                sent[-1]
                .split(b"Content-Type: image/png\r\n\r\n")[1]
                .split(b"\r\n--")[0]
            )
        assert sent_image != product_image
        with Image.open(io.BytesIO(sent_image)) as image:
            assert image.size == (4, 4)
            assert image.getpixel((0, 0)) == (255, 0, 0)


@pytest.mark.parametrize("provider_name", ["openai", "anthropic"])
def test_assist_copy_sends_draft_images_and_returns_no_replacement_on_error(
    tmp_path, provider_name
):
    sent = []
    fail = [False]

    def provider(request):
        sent.append(json.loads(request.read()))
        if fail[0]:
            return httpx.Response(
                401, json={"error": {"message": "bad fake-secret"}}
            )
        text = json.dumps(
            {
                "facts": ["蓝色包装"],
                "unknowns": ["容量"],
                "text": "产品名称：茶叶",
            }
        )
        result = {"model": "actual-model"}
        result.update(
            {"content": [{"type": "text", "text": text}]}
            if provider_name == "anthropic"
            else {"choices": [{"message": {"content": text}}]}
        )
        return httpx.Response(200, json=result)

    app = main.create_app(tmp_path, httpx.MockTransport(provider))
    with testclient.TestClient(app, base_url="http://127.0.0.1:8766") as client:
        draft_id = app.state.assets.create_draft()["draftId"]
        originals = [png(), png("green")]
        for original in originals:
            app.state.assets.add_image(draft_id, original, "product.png")
        configure(client, provider=provider_name)
        before = app.state.assets.read_draft(draft_id)
        assert not sent
        body = {
            "draftId": draft_id,
            "source": "用户原文",
            "platform": "亚马逊",
            "language": "中文",
        }
        reply = client.post("/api/assist-copy", json=body).json()
        assert reply == {
            "text": "产品名称：茶叶\n待补充：容量",
            "facts": ["蓝色包装"],
            "unknowns": ["容量"],
            "provider": provider_name,
            "responseModel": "actual-model",
            "imageCount": 2,
        }
        content = sent[0]["messages"][0]["content"]
        images = [
            base64.b64decode(
                item["source"]["data"]
                if provider_name == "anthropic"
                else item["image_url"]["url"].split(",")[1]
            )
            for item in content[1:]
        ]
        assert images == originals
        assert "用户原文" in content[0]["text"]
        fail[0] = True
        response = client.post("/api/assist-copy", json=body)
        assert response.status_code == 502
        assert response.json()["code"] == "auth"
        assert "text" not in response.json()
        assert "fake-secret" not in response.text
        assert app.state.assets.read_draft(draft_id) == before
        assert body["source"] == "用户原文"


def test_missing_images_credentials_and_invalid_role(tmp_path):
    def provider(request):
        pytest.fail(f"Unexpected provider request: {request.url}")

    app = main.create_app(tmp_path, httpx.MockTransport(provider))
    with testclient.TestClient(app, base_url="http://127.0.0.1:8766") as client:
        draft_id = app.state.assets.create_draft()["draftId"]
        response = client.post("/api/assist-copy", json={"draftId": draft_id})
        assert response.status_code == 400
        assert response.json() == {"error": "请先上传至少一张商品原图"}
        app.state.assets.add_image(draft_id, png(), "product.png")
        response = client.post("/api/assist-copy", json={"draftId": draft_id})
        assert response.json() == {
            "error": "请先配置视觉理解模型和 SK",
            "code": "credentials",
        }
        assert client.post("/api/roles/test", json={"role": "text"}).json() == {
            "error": "模型角色无效"
        }
