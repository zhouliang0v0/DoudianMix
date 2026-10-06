"""Application boundary tests."""

import pathlib
import tomllib

import pytest
from fastapi import testclient

from backend import errors
from backend import main


@pytest.fixture(name="client")
def client_fixture(tmp_path):
    """Create a local client with an isolated data directory."""
    with testclient.TestClient(
        main.create_app(data_dir=tmp_path),
        base_url="http://127.0.0.1:8766",
    ) as local_client:
        yield local_client


def test_local_pages_and_private_path(client):
    for path in (
        "/",
        "/index.html",
        "/settings.html",
        "/results.html",
        "/styles.css",
        "/script.js",
        "/settings.js",
        "/results.js",
        "/providers.json",
    ):
        assert client.get(path).status_code == 200
    for path in (
        "/data/jobs/x/job.json",
        "/server.js",
        "/docs",
        "/openapi.json",
        "/providers.js",
    ):
        response = client.get(path)
        assert response.status_code == 404
        assert "error" in response.json()


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1.evil:8766",
        "localhost.evil:8766",
        "evil:8766",
        "127.0.0.1:8765",
    ],
)
def test_rejects_lookalike_host(client, host):
    assert client.get("/", headers={"Host": host}).status_code == 403


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://localhost:8765",
        "null",
        "http://127.0.0.1:8766/",
        "https://127.0.0.1:8766",
    ],
)
def test_rejects_foreign_origin(client, origin):
    assert client.get("/", headers={"Origin": origin}).status_code == 403


def test_accepts_local_origin(client):
    for origin in ("http://127.0.0.1:8766", "http://localhost:8766"):
        assert client.get("/", headers={"Origin": origin}).status_code == 200


def test_error_contract(client):
    @client.app.get("/test-error")
    async def fail():
        raise errors.AppError("Invalid input", 409, "conflict")

    response = client.get("/test-error")
    assert response.status_code == 409
    assert response.json() == {"error": "Invalid input", "code": "conflict"}


def test_validation_error_contract(client):
    @client.app.get("/test-validation")
    async def validate(count: int):
        return {"count": count}

    response = client.get("/test-validation?count=bad")
    assert response.status_code == 400
    assert "error" in response.json()
    assert "detail" not in response.json()


def test_python_requirement():
    with pathlib.Path("pyproject.toml").open("rb") as project_file:
        project = tomllib.load(project_file)
    assert project["project"]["requires-python"] == ">=3.11,<3.12"


def test_unexpected_error_is_safe(tmp_path):
    application = main.create_app(data_dir=tmp_path)

    @application.get("/test-unexpected")
    async def fail():
        raise RuntimeError("sensitive provider content")

    with testclient.TestClient(
        application,
        base_url="http://127.0.0.1:8766",
        raise_server_exceptions=False,
    ) as local_client:
        response = local_client.get("/test-unexpected")
    assert response.status_code == 500
    assert response.json() == {"error": "请求失败"}
