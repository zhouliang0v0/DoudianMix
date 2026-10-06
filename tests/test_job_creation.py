"""Job creation preserves the Node snapshot and credential boundaries."""

import io
import json
import time

from fastapi import testclient
from PIL import Image
import pytest

from backend import main

SELECTED = ["商品细节图", "首屏主视觉"]


@pytest.fixture(name="setup")
def setup_fixture(tmp_path):
    app = main.create_app(tmp_path)
    draft_id = app.state.assets.create_draft()["draftId"]
    output = io.BytesIO()
    Image.new("RGB", (8, 6), "red").save(output, "PNG")
    app.state.assets.add_image(draft_id, output.getvalue(), "tea.png")
    for role in ("vision", "image"):
        app.state.profiles.save_role(
            {
                "role": role,
                "provider": "openai",
                "baseUrl": "https://example.com/v1",
                "model": role + "-model",
                "apiKey": "sk-secret-value",
            }
        )
    with testclient.TestClient(app, base_url="http://127.0.0.1:8766") as client:
        yield client, app, tmp_path, {
            "draftId": draft_id,
            "brief": "Tea product",
            "modules": SELECTED,
            "settings": {"platform": "x" * 100, "ignored": "value"},
            "apiKey": "sk-injected-secret",
        }


def test_creates_snapshot_without_secret(setup):
    client, app, directory, payload = setup
    queued = []
    app.state.enqueue = queued.append
    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 201
    job_id = response.json()["jobId"]
    job_path = directory / "jobs" / job_id / "job.json"
    assert "apiKey" not in job_path.read_text("utf-8")
    assert "sk-secret-value" not in job_path.read_text("utf-8")
    assert "sk-injected-secret" not in job_path.read_text("utf-8")
    job = json.loads(job_path.read_text("utf-8"))
    assert job["modules"][0]["name"] == SELECTED[0]
    assert queued == [job_id]
    assert response.json() == {"jobId": job_id, "status": "queued"}
    assert response.headers["cache-control"] == "no-store"
    for image in job["images"]:
        for field in ("originalName", "previewName"):
            assert (
                directory / "jobs" / job_id / "originals" / image[field]
            ).read_bytes() == (
                directory / "drafts" / payload["draftId"] / image[field]
            ).read_bytes()


@pytest.mark.parametrize(
    "change",
    [
        {"brief": "  "},
        {"modules": []},
        {"modules": [SELECTED[0]] * 2},
        {"modules": [SELECTED[0]] * 17},
        {"modules": ["unknown"]},
        {"modules": [{}]},
        {"draftId": "invalid"},
    ],
)
def test_rejects_empty_brief_duplicate_or_seventeen_modules(setup, change):
    client, _, directory, payload = setup
    response = client.post("/api/jobs", json={**payload, **change})
    assert response.status_code == 400
    assert "error" in response.json()
    assert not (directory / "jobs").exists()


def test_legacy_job_shape_matches_node(setup):
    client, _, directory, payload = setup
    before = time.time_ns() // 1_000_000
    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 201
    job = json.loads(
        (directory / "jobs" / response.json()["jobId"] / "job.json").read_text(
            "utf-8"
        )
    )
    assert set(job) == {
        "id",
        "draftId",
        "brief",
        "settings",
        "visionProfile",
        "imageProfile",
        "images",
        "modules",
        "status",
        "stage",
        "analysis",
        "createdAt",
        "updatedAt",
    }
    assert before <= job["createdAt"] <= time.time_ns() // 1_000_000
    assert job["createdAt"] == job["updatedAt"]
    assert job["settings"] == {
        "platform": "x" * 80,
        "market": "",
        "language": "",
        "quality": "",
        "ratio": "",
        "style": "",
    }
    assert job["modules"] == [
        {
            "id": str(index + 1),
            "name": name,
            "status": "pending",
            "attempts": 0,
            "headline": "",
            "body": "",
            "imageFile": None,
            "error": None,
        }
        for index, name in enumerate(SELECTED)
    ]
    assert job["visionProfile"] == {
        "provider": "openai",
        "baseUrl": "https://example.com/v1",
        "model": "vision-model",
        "protocol": "openai",
    }
    assert job["imageProfile"] == {
        "provider": "openai",
        "baseUrl": "https://example.com/v1",
        "model": "image-model",
    }
    assert job["status"] == job["stage"] == "queued"
    assert job["analysis"] is None


@pytest.mark.parametrize("missing", ["vision", "image", "images"])
def test_missing_required_inputs_create_no_directory(setup, missing):
    client, app, directory, payload = setup
    if missing == "images":
        payload["draftId"] = app.state.assets.create_draft()["draftId"]
    else:
        app.state.profiles.save_role(
            {
                "role": missing,
                "provider": "openai",
                "baseUrl": "https://other.example/v1",
                "model": "model",
            }
        )
    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 400
    assert not (directory / "jobs").exists()
