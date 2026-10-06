"""Result delivery and ZIP preserve legacy bytes and private boundaries."""

import io
import json
import uuid
import zipfile

from fastapi import testclient
import pytest

from backend import main


@pytest.fixture(name="setup")
def setup_fixture(tmp_path):
    """Persist a partial job with completed and failed image bytes."""
    app = main.create_app(tmp_path)
    job_id = str(uuid.uuid4())
    asset_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "status": "partial",
        "createdAt": 1,
        "settings": {"platform": "淘宝"},
        "analysis": {"product": "茶"},
        "apiKey": "sk-private-value",
        "imageProfile": {"apiKey": "sk-private-value"},
        "images": [
            {
                "assetId": asset_id,
                "originalName": asset_id + ".webp",
                "mimeType": "image/webp",
            }
        ],
        "modules": [
            {
                "id": "2",
                "name": "细节",
                "status": "completed",
                "headline": "香",
                "body": "茶香",
                "error": None,
                "imageFile": "2.png",
            },
            {
                "id": "1",
                "name": "首屏",
                "status": "failed",
                "headline": "",
                "body": "",
                "error": "失败",
                "imageFile": "1.jpg",
            },
        ],
    }
    app.state.jobs.create(job)
    directory = app.state.jobs.job_dir(job_id)
    (directory / "results").mkdir()
    (directory / "originals").mkdir()
    (directory / "results" / "2.png").write_bytes(b"completed")
    (directory / "results" / "1.jpg").write_bytes(b"failed")
    (directory / "originals" / (asset_id + ".webp")).write_bytes(b"original")
    with testclient.TestClient(app, base_url="http://127.0.0.1:8766") as client:
        yield client, app, job, directory


def test_zip_contains_manifest_copy_and_completed_images(setup):
    """Keep the legacy ZIP names, module order, copy and credential boundary."""
    client, _, job, _ = setup
    response = client.get(f"/api/jobs/{job['id']}/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="product-detail-{job["id"]}.zip"'
    )
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == [
            "manifest.json",
            "文案与失败清单.txt",
            "images/01-2.png",
        ]
        assert "images/02-1.jpg" not in archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        assert [item["name"] for item in manifest["modules"]] == [
            "细节",
            "首屏",
        ]
        assert manifest["modules"][1]["image"] == "images/02-1.jpg"
        assert archive.read("images/01-2.png") == b"completed"
        assert "失败原因：失败" in archive.read("文案与失败清单.txt").decode()
        assert all(
            b"sk-private-value" not in archive.read(name)
            for name in archive.namelist()
        )


def test_image_mime_and_deleted_result_404(setup):
    """Deliver unchanged images and return 404 after files or jobs disappear."""
    client, app, job, directory = setup
    url = f"/api/jobs/{job['id']}/images/2"
    image = client.get(url)
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.headers["cache-control"] == "no-store"
    assert image.content == b"completed"
    original = client.get(
        f"/api/jobs/{job['id']}/originals/{job['images'][0]['assetId']}"
    )
    assert original.status_code == 200
    assert original.headers["content-type"] == "image/webp"
    assert original.content == b"original"
    assert client.get(f"/api/jobs/{job['id']}/images/1").status_code == 404
    (directory / "results" / "2.png").unlink()
    assert client.get(url).status_code == 404
    app.state.jobs.remove(job["id"])
    assert client.get(url).status_code == 404
    assert client.get(f"/api/jobs/{job['id']}/export").status_code == 404


@pytest.mark.parametrize(
    "filename",
    ["../../secret.png", "..\\..\\secret.png", "C:\\secret.png", "/secret.png"],
)
def test_legacy_metadata_cannot_escape_job_dir(setup, filename):
    """Reject both platform path syntaxes in old result and original records."""
    client, app, job, _ = setup

    def mutate(current):
        current["modules"][0]["imageFile"] = filename
        current["images"][0]["originalName"] = filename

    app.state.jobs.update(job["id"], mutate)
    assert client.get(f"/api/jobs/{job['id']}/images/2").status_code == 404
    assert (
        client.get(
            f"/api/jobs/{job['id']}/originals/{job['images'][0]['assetId']}"
        ).status_code
        == 404
    )
    response = client.get(f"/api/jobs/{job['id']}/export")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert not any(
            name.startswith("images/") for name in archive.namelist()
        )


@pytest.mark.parametrize(
    ("route", "identifier", "message"),
    [
        ("originals", "missing", "原图不存在"),
        ("images", "missing", "结果图不存在"),
        ("images", "1", "结果图不存在"),
    ],
)
def test_missing_record_error_matches_legacy_route(
    setup, route, identifier, message
):
    """Preserve errors for absent records and incomplete modules."""
    client, _, job, _ = setup
    response = client.get(f"/api/jobs/{job['id']}/{route}/{identifier}")
    assert response.status_code == 404
    assert response.json() == {"error": message}


@pytest.mark.parametrize("original", [False, True])
def test_missing_file_error_matches_legacy_route(setup, original):
    """Preserve the original/result label when a recorded file disappears."""
    client, _, job, directory = setup
    asset_id = job["images"][0]["assetId"]
    route = "originals" if original else "images"
    identifier = asset_id if original else "2"
    name = asset_id + ".webp" if original else "2.png"
    (directory / ("originals" if original else "results") / name).unlink()
    response = client.get(f"/api/jobs/{job['id']}/{route}/{identifier}")
    assert response.status_code == 404
    assert response.json() == {
        "error": "原图不存在" if original else "结果图不存在"
    }
