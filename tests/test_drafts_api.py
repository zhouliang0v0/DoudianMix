"""Legacy draft HTTP contracts using isolated, generated image data."""

import io
import uuid

from fastapi import testclient
from PIL import Image
import pytest
from starlette import formparsers

from backend import main


@pytest.fixture(name="client")
def local_client_fixture(tmp_path):
    with testclient.TestClient(
        main.create_app(data_dir=tmp_path),
        base_url="http://127.0.0.1:8766",
    ) as local_client:
        yield local_client


def _png():
    output = io.BytesIO()
    Image.new("RGB", (8, 6), "red").save(output, "PNG")
    return output.getvalue()


def test_draft_api_matches_node_contract(client):
    # Node server/routes/drafts.js -> FastAPI comparison:
    # create 201 {draftId}; upload 201 {assetId,mimeType,previewUrl};
    # preview 200 image/webp + no-store; delete 200 {ok:true}.
    created = client.post("/api/drafts")
    assert created.status_code == 201
    assert created.headers["content-type"] == "application/json; charset=utf-8"
    assert created.headers["cache-control"] == "no-store"
    draft_id = created.json()["draftId"]
    path = f"/api/drafts/{draft_id}"
    assert client.get(path).json() == {"draftId": draft_id, "images": []}
    original = _png()
    upload = client.post(
        path + "/images", files={"image": ("tea.png", original)}
    )
    assert upload.status_code == 201
    asset_id = upload.json()["assetId"]
    assert upload.json() == {
        "assetId": asset_id,
        "mimeType": "image/png",
        "previewUrl": f"{path}/images/{asset_id}/preview",
    }
    preview = client.get(upload.json()["previewUrl"])
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/webp"
    assert preview.headers["cache-control"] == "no-store"
    fetched = client.get(f"{path}/images/{asset_id}")
    assert fetched.content == original
    assert fetched.headers["content-type"] == "image/png"
    assert fetched.headers["cache-control"] == "no-store"
    assert client.get(path).json() == {
        "draftId": draft_id,
        "images": [
            {"assetId": asset_id, "previewUrl": upload.json()["previewUrl"]}
        ],
    }
    deleted = client.delete(f"{path}/images/{asset_id}")
    assert deleted.json() == {"ok": True}
    assert client.get(upload.json()["previewUrl"]).status_code == 404


@pytest.mark.parametrize(
    "case", ["wrong", "multiple", "field", "oversize", "chunked", "truncated"]
)
def test_rejects_malformed_multipart_without_leaks(
    client, tmp_path, case, monkeypatch
):
    opened = []
    original_spool = formparsers.SpooledTemporaryFile

    def track_spool(*args, **kwargs):
        uploaded = original_spool(*args, **kwargs)
        opened.append(uploaded)
        return uploaded

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", track_spool)
    created = client.post("/api/drafts")
    assert created.status_code == 201
    path = f"/api/drafts/{created.json()['draftId']}/images"
    before = set(tmp_path.rglob("*"))
    if case in ("chunked", "truncated"):

        def chunks():
            yield (
                b"--test\r\nContent-Disposition: form-data; "
                b'name="image"; filename="x.png"\r\n\r\n'
            )
            for _ in range(161 if case == "chunked" else 20):
                yield b"x" * 65536
            if case == "chunked":
                yield b"\r\n--test--\r\n"

        response = client.post(
            path,
            content=chunks(),
            headers={"Content-Type": "multipart/form-data; boundary=test"},
        )
    else:
        files = [("wrong" if case == "wrong" else "image", ("x.png", _png()))]
        if case == "multiple":
            files.append(("image", ("second.png", _png())))
        if case == "field":
            files.append(("extra", (None, "value")))
        if case == "oversize":
            files = [("image", ("big.png", b"x" * (10 * 1024 * 1024 + 1)))]
        response = client.post(path, files=files)
    assert response.status_code == (
        413 if case in ("oversize", "chunked") else 400
    )
    assert set(response.json()) == {"error"}
    leaked_files = set(tmp_path.rglob("*")) - before
    assert not leaked_files
    assert opened
    assert all(uploaded.closed for uploaded in opened)


@pytest.mark.parametrize("identifier", ["not-a-uuid", "g" * 36, "a" * 35])
def test_ineligible_ids_return_route_404(client, identifier):
    draft_id = client.post("/api/drafts").json()["draftId"]
    for method, path in (
        ("GET", f"/api/drafts/{identifier}"),
        ("POST", f"/api/drafts/{identifier}/images"),
        ("GET", f"/api/drafts/{draft_id}/images/{identifier}"),
        ("GET", f"/api/drafts/{draft_id}/images/{identifier}/preview"),
        ("DELETE", f"/api/drafts/{draft_id}/images/{identifier}"),
    ):
        response = client.request(method, path)
        assert response.status_code == 404
        assert response.json() == {"error": "未找到页面"}


def test_loose_legacy_ids_reach_store_validation(client):
    response = client.get("/api/drafts/" + "a" * 36)
    assert response.status_code == 400
    assert response.json() == {"error": "草稿 ID 无效"}


@pytest.mark.parametrize("path", ["/missing", "/api/drafts/" + "a" * 36])
def test_error_json_has_legacy_headers(client, path):
    response = client.get(path)
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"


def test_missing_and_invalid_upload_errors(client):
    missing = client.get(f"/api/drafts/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json() == {"error": "草稿不存在"}
    created = client.post("/api/drafts")
    assert created.status_code == 201
    path = f"/api/drafts/{created.json()['draftId']}/images"
    assert client.post(path, json={}).status_code == 400
    response = client.post(path, files={"image": ("x.png", b"not an image")})
    assert response.status_code == 400
    assert client.get("/data/drafts").status_code == 404
