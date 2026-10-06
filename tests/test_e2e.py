"""Offline HTTP acceptance and executable PowerShell quality-gate checks."""

import base64
import contextlib
from email import parser
from email import policy
from http import server as http_server
import io
import json
import os
import pathlib
import shutil
import socket
import subprocess
import threading
import time
import zipfile

import httpx
from PIL import Image
import pytest
import uvicorn

from backend import main

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_MODULES = ["首屏主视觉", "商品细节图", "品牌故事图"]
_SECRET = "offline-test-key"
_COMMANDS = [
    "uv run --locked pyink --check backend tests",
    "uv run --locked pylint --rcfile=config/google.pylintrc backend",
    "uv run --locked pytest -q",
    "npm run lint",
]


@pytest.fixture(name="session")
def local_session_fixture(tmp_path):
    """Provide real loopback app/provider servers and isolated storage."""
    provider = _LocalProvider()
    provider_thread = threading.Thread(
        target=provider.serve_forever, daemon=True
    )
    provider_thread.start()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    application = main.create_app(tmp_path)
    runtime = uvicorn.Server(
        uvicorn.Config(application, log_level="error", ws="none", workers=1)
    )
    app_thread = threading.Thread(
        target=runtime.run, kwargs={"sockets": [listener]}, daemon=True
    )
    app_thread.start()
    try:
        deadline = time.monotonic() + 10
        while not runtime.started and app_thread.is_alive():
            assert time.monotonic() < deadline, "Loopback app startup timed out"
            time.sleep(0.01)
        assert runtime.started, "Loopback app failed to start"
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False
        ) as client:
            yield client, provider, tmp_path
    finally:
        provider.release.set()
        runtime.should_exit = True
        app_thread.join(10)
        listener.close()
        provider.shutdown()
        provider.server_close()
        provider_thread.join(10)
        assert not app_thread.is_alive(), "Loopback app did not stop"
        assert not provider_thread.is_alive(), "Mock provider did not stop"
        assert not provider.errors, provider.errors


class _LocalProvider(http_server.ThreadingHTTPServer):
    """Protocol-faithful loopback service with one failure and a cancel latch."""

    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _ProviderHandler)
        self.url = f"http://127.0.0.1:{self.server_port}/v1"
        self.requests = []
        self.originals = []
        self.attempts = {}
        self.pause_module = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.errors = []


class _ProviderHandler(http_server.BaseHTTPRequestHandler):
    """Validate real provider payloads before responding to the adapters."""

    def log_message(self, *_):
        """Keep provider request logs out of acceptance output."""

    def do_POST(self):  # pylint: disable=invalid-name
        """Serve compatible vision/text and OpenAI Images Edits endpoints."""
        provider = self.server
        body = self.rfile.read(int(self.headers["Content-Length"]))
        try:
            assert self.headers["Authorization"] == f"Bearer {_SECRET}"
            if self.path == "/v1/chat/completions":
                status, response = self._chat(json.loads(body))
            elif self.path == "/v1/images/edits":
                status, response = self._edit(body)
            else:
                raise AssertionError(f"Unexpected provider route: {self.path}")
        except (AssertionError, KeyError, ValueError) as error:
            provider.errors.append(str(error))
            status, response = 400, {
                "error": {"message": "Invalid mock request"}
            }
        content = json.dumps(response).encode()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    def _chat(self, payload):
        assert payload["model"] == "offline-vision"
        content = payload["messages"][0]["content"]
        if isinstance(content, list):
            images = [
                base64.b64decode(item["image_url"]["url"].split(",", 1)[1])
                for item in content
                if item["type"] == "image_url"
            ]
            assert images == self.server.originals
            assert all(
                item["image_url"]["url"].startswith("data:image/png;base64,")
                for item in content
                if item["type"] == "image_url"
            )
            self.server.requests.append("vision")
            result = dict(
                facts=["茶叶礼盒"],
                unknowns=["规格"],
                text="茶叶礼盒，规格待补充",
            )
        else:
            assert "规格" in content
            self.server.requests.append("plan")
            result = dict(headline="茶叶礼盒", body="规格待补充")
        return 200, {
            "model": "offline-vision",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(result),
                    }
                }
            ],
        }

    def _edit(self, body):
        message = parser.BytesParser(policy=policy.default).parsebytes(
            f'Content-Type: {self.headers["Content-Type"]}\r\n\r\n'.encode()
            + body
        )
        fields = {}
        images = []
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            value = part.get_payload(decode=True)
            if name == "image[]":
                assert part.get_content_type() == "image/png"
                images.append(value)
            else:
                fields[name] = value.decode()
        assert images == self.server.originals
        assert fields["model"] == "offline-image"
        assert fields["quality"] == "medium"
        assert fields["output_format"] == "png"
        index = next(
            index
            for index, name in enumerate(_MODULES, 1)
            if f"模块「{name}」" in fields["prompt"]
        )
        assert (
            fields["size"]
            == {1: "1536x864", 2: "1024x1024", 3: "1024x1280"}[index]
        )
        module_id = str(index)
        provider = self.server
        provider.requests.append(module_id)
        provider.attempts[module_id] = provider.attempts.get(module_id, 0) + 1
        if module_id == provider.pause_module:
            provider.entered.set()
            assert provider.release.wait(
                10
            ), "Canceled request was not released"
        if module_id == "2" and provider.attempts[module_id] == 1:
            return 429, {"error": {"message": f"Rate limit {_SECRET}"}}
        return 200, {
            "model": "offline-image",
            "data": [
                {
                    "b64_json": base64.b64encode(
                        _png(["red", "green", "blue"][index - 1])
                    ).decode()
                }
            ],
        }


def _png(color):
    output = io.BytesIO()
    Image.new("RGB", (8, 6), color).save(output, "PNG")
    return output.getvalue()


def _prepare_job(client, provider):
    """Upload two originals and configure the two roles through public APIs."""
    created = client.post("/api/drafts")
    assert created.status_code == 201
    draft_id = created.json()["draftId"]
    originals = [_png("red"), _png("blue")]
    assets = []
    for index, original in enumerate(originals):
        uploaded = client.post(
            f"/api/drafts/{draft_id}/images",
            files={"image": (f"tea-{index}.png", original, "image/png")},
        )
        assert uploaded.status_code == 201
        assets.append(uploaded.json()["assetId"])
        preview = client.get(uploaded.json()["previewUrl"])
        assert preview.status_code == 200
        assert preview.headers["content-type"] == "image/webp"
    for role in ("vision", "image"):
        saved = client.post(
            "/api/roles",
            json=dict(
                role=role,
                provider="openai",
                baseUrl=provider.url,
                model=f"offline-{role}",
                apiKey=_SECRET,
            ),
        )
        assert saved.status_code == 200
    assert _SECRET not in client.get("/api/roles").text
    assert provider.requests == []
    provider.originals = originals
    return (
        draft_id,
        assets,
        dict(
            draftId=draft_id,
            brief="茶叶礼盒，包装颜色以原图为准，规格待补充。",
            modules=_MODULES,
            settings=dict(platform="淘宝", language="中文", ratio="自动适配"),
        ),
    )


def _wait_job(client, job_id, statuses):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in statuses:
            return job
        time.sleep(0.01)
    pytest.fail(f"Job did not reach {statuses}: {job}")


def _archive(client, job_id):
    response = client.get(f"/api/jobs/{job_id}/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    return zipfile.ZipFile(io.BytesIO(response.content))


def test_two_images_three_modules_retry_zip_delete(session):
    """Catch lost successful results, broad retry, broken ZIP and asset leaks."""
    client, provider, data_dir = session
    draft_id, assets, payload = _prepare_job(client, provider)
    created = client.post("/api/jobs", json=payload)
    assert created.status_code == 201
    assert created.json()["status"] == "queued"
    job_id = created.json()["jobId"]
    job = _wait_job(client, job_id, {"partial", "failed", "completed"})
    assert job["status"] == "partial"
    assert [module["status"] for module in job["modules"]] == [
        "completed",
        "failed",
        "completed",
    ]
    assert job["modules"][1]["errorCode"] == "rate_limit"
    assert _SECRET not in json.dumps(job)
    for asset_id, original in zip(assets, provider.originals):
        assert (
            client.get(f"/api/jobs/{job_id}/originals/{asset_id}").content
            == original
        )
    with _archive(client, job_id) as archive:
        assert archive.namelist() == [
            "manifest.json",
            "文案与失败清单.txt",
            "images/01-1.png",
            "images/03-3.png",
        ]
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["modules"][1]["status"] == "failed"
        assert "失败原因" in archive.read("文案与失败清单.txt").decode()
    retry = client.post(f"/api/jobs/{job_id}/retry", json={"moduleId": "2"})
    assert retry.status_code == 200
    assert retry.json() == {"status": "queued"}
    before = job
    job = _wait_job(client, job_id, {"partial", "failed", "completed"})
    assert job["status"] == "completed"
    assert [module["attempts"] for module in job["modules"]] == [1, 2, 1]
    assert job["modules"][0] == before["modules"][0]
    assert job["modules"][2] == before["modules"][2]
    assert job["analysis"] == before["analysis"]
    assert provider.requests.count("vision") == 1
    assert [item for item in provider.requests if item.isdigit()] == [
        "1",
        "2",
        "3",
        "2",
    ]
    with _archive(client, job_id) as archive:
        assert "manifest.json" in archive.namelist()
        assert archive.namelist() == [
            "manifest.json",
            "文案与失败清单.txt",
            "images/01-1.png",
            "images/02-2.png",
            "images/03-3.png",
        ]
        manifest = json.loads(archive.read("manifest.json"))
        assert [item["name"] for item in manifest["modules"]] == _MODULES
        assert all(
            item["status"] == "completed" for item in manifest["modules"]
        )
        for index, color in enumerate(
            ((255, 0, 0), (0, 128, 0), (0, 0, 255)), 1
        ):
            result = client.get(f"/api/jobs/{job_id}/images/{index}")
            assert result.status_code == 200
            assert result.headers["content-type"] == "image/png"
            assert (
                archive.read(f"images/{index:02}-{index}.png") == result.content
            )
            with Image.open(io.BytesIO(result.content)) as image:
                assert image.getpixel((0, 0))[:3] == color
        for name in archive.namelist():
            assert _SECRET.encode() not in archive.read(name)
    assert client.get("/api/jobs").json()["jobs"][0]["completed"] == 3
    for path in data_dir.rglob("*"):
        if path.is_file():
            assert _SECRET.encode() not in path.read_bytes()
    assert client.delete(f"/api/jobs/{job_id}").json() == {"ok": True}
    for suffix in ("", "/export", "/images/1", f"/originals/{assets[0]}"):
        assert client.get(f"/api/jobs/{job_id}{suffix}").status_code == 404
    assert client.get(f"/api/drafts/{draft_id}").status_code == 404
    assert not (data_dir / "jobs" / job_id).exists()
    assert not (data_dir / "drafts" / draft_id).exists()
    assert client.get("/api/jobs").json() == {"jobs": []}


def test_cancel_preserves_completed_image_and_stops_next_module(session):
    """Cancel during the second image request without generating the third."""
    client, provider, _ = session
    _, _, payload = _prepare_job(client, provider)
    provider.pause_module = "2"
    job_id = client.post("/api/jobs", json=payload).json()["jobId"]
    assert provider.entered.wait(10)
    response = client.post(f"/api/jobs/{job_id}/cancel")
    assert response.status_code == 200
    assert response.json() == {"status": "canceled"}
    provider.release.set()
    job = _wait_job(client, job_id, {"canceled"})
    assert [module["attempts"] for module in job["modules"]] == [1, 1, 0]
    assert job["modules"][0]["status"] == "completed"
    assert job["modules"][2]["status"] == "pending"
    assert client.get(f"/api/jobs/{job_id}/images/1").status_code == 200
    assert (
        client.post(
            f"/api/jobs/{job_id}/retry", json={"moduleId": "2"}
        ).status_code
        == 400
    )
    with _archive(client, job_id) as archive:
        assert archive.namelist() == [
            "manifest.json",
            "文案与失败清单.txt",
            "images/01-1.png",
        ]
    assert client.delete(f"/api/jobs/{job_id}").status_code == 200
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert [item for item in provider.requests if item.isdigit()] == ["1", "2"]


@pytest.mark.parametrize("failure", ["pyink", "pylint", "pytest", "npm", None])
def test_check_script_propagates_failure(tmp_path, failure):
    """Execute the gate with native command doubles, failing each stage."""
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if os.name != "nt" or not shell:
        pytest.skip("Quality gate requires Windows PowerShell")
    source = _ROOT / "scripts" / "check.ps1"
    assert source.is_file(), "Unified quality gate is missing"
    repository = tmp_path / "repository"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(source, scripts / "check.ps1")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    log = tmp_path / "commands.txt"
    for command, argument in (("uv", "%3"), ("npm", "npm")):
        (binaries / f"{command}.cmd").write_text(
            f'@echo off\necho {command} %*>>"{log}"\n'
            f'if "{argument}"=="{failure}" exit /b 17\nexit /b 0\n',
            encoding="utf-8",
        )
    environment = dict(os.environ)
    environment["PATH"] = str(binaries) + os.pathsep + environment["PATH"]
    process = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(scripts / "check.ps1"),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = process.stdout + process.stderr
    if failure:
        assert process.returncode != 0, output
        assert process.returncode == 17, output
        count = ["pyink", "pylint", "pytest", "npm"].index(failure) + 1
    else:
        assert process.returncode == 0, output
        count = 4
    assert log.read_text().splitlines() == _COMMANDS[:count]
