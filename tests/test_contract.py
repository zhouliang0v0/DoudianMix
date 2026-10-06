"""Compare real FastAPI replies with frozen, previously verified Node replies."""

import json
import pathlib

from fastapi import testclient
import httpx
import pytest

from backend import main

_ROOT = pathlib.Path(__file__).resolve().parent.parent


class _FrozenNodeResponses:
    """Read a test's ordered response oracle without executing Node."""

    def __init__(self, records):
        self._records = iter(records)

    def request(self, method, route, **kwargs):
        record = next(self._records)
        assert (method.upper(), route) == (record["method"], record["route"])
        assert {
            key: value for key, value in kwargs.items() if value is not None
        } == record["request"]
        return httpx.Response(
            record["status"],
            headers={"content-type": record["content_type"]},
            **({"json": record["json"]} if "json" in record else {}),
        )

    def get(self, route, **kwargs):
        return self.request("GET", route, **kwargs)

    def post(self, route, **kwargs):
        return self.request("POST", route, **kwargs)

    def assert_consumed(self):
        assert next(self._records, None) is None


@pytest.fixture(name="clients")
def clients_fixture(tmp_path, request):
    frozen = json.loads(
        (_ROOT / "tests/fixtures/node_contract_responses.json").read_text(
            "utf-8"
        )
    )
    records = [
        record
        for record in frozen["responses"]
        if record["test"] == request.node.nodeid
    ]
    assert records, "No captured Node oracle for this contract case"
    expected = _FrozenNodeResponses(records)
    app = main.create_app(
        tmp_path / "python",
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"choices": [{"message": {"content": "连接成功"}}]},
            )
        ),
    )
    with testclient.TestClient(
        app,
        base_url="http://127.0.0.1:8766",
        raise_server_exceptions=False,
    ) as python:
        yield expected, python
    expected.assert_consumed()


def test_legacy_text_api_matches_node(clients):
    node, python = clients
    config = {
        "provider": "openai",
        "baseUrl": "https://api.example/v1",
        "model": "test",
        "apiKey": "secret-test",
    }
    for method, route, body in [
        ("GET", "/api/config", None),
        ("POST", "/api/config", config),
        ("GET", "/api/config", None),
        ("POST", "/api/test", {}),
        ("POST", "/api/generate-copy", {"source": "茶叶"}),
        ("POST", "/api/config", {**config, "apiKey": ""}),
    ]:
        node_reply = node.request(method, route, json=body)
        python_reply = python.request(method, route, json=body)
        assert python_reply.status_code == node_reply.status_code
        assert python_reply.json() == node_reply.json()
        assert "secret-test" not in python_reply.text


@pytest.mark.parametrize(
    "route,content,status",
    [
        ("/api/config", "{", 400),
        ("/api/config", "{}", 400),
        ("/api/generate-copy", "{}", 400),
        ("/api/roles", "[]", 400),
        ("/api/jobs", "{}", 400),
        ("/api/config", '{"source":"' + "x" * 10000 + '"}', 413),
    ],
)
def test_all_api_error_shapes_are_page_readable(
    clients, route, content, status
):
    node, python = clients
    node_reply = node.post(route, content=content)
    reply = python.post(route, content=content)
    assert reply.status_code == node_reply.status_code == status
    assert "error" in reply.json()
    assert "detail" not in reply.json()
    if route != "/api/roles":
        assert reply.json() == node_reply.json()


@pytest.mark.parametrize(
    "route",
    [
        "/",
        "/settings.html",
        "/styles.css",
        "/script.js",
        "/api/roles",
        "/api/unknown",
    ],
)
def test_contract_status_and_content_types(clients, route):
    node, python = clients
    node_reply = node.get(route)
    python_reply = python.get(route)
    assert python_reply.status_code == node_reply.status_code
    assert (
        python_reply.headers["content-type"]
        == node_reply.headers["content-type"]
    )
    assert python_reply.headers["cache-control"] == "no-store"


def test_legacy_profile_key_reuse_and_role_isolation(clients):
    """Changing text URL clears SK and leaves role profiles independent."""
    node, python = clients
    config = {
        "provider": "openai",
        "baseUrl": "https://api.example/v1",
        "model": "test",
        "apiKey": "secret-test",
    }
    for client in (node, python):
        client.post("/api/config", json=config)
        reply = client.post("/api/config", json={**config, "apiKey": ""})
        assert reply.json()["profiles"]["openai"]["hasKey"]
        assert client.get("/api/roles").json() == {"roles": {}}
        reply = client.post(
            "/api/config",
            json={
                **config,
                "apiKey": "",
                "baseUrl": "https://other.example/v1",
            },
        )
        assert not reply.json()["profiles"]["openai"]["hasKey"]
        assert client.post("/api/test", json={}).status_code == 400
    fresh = main.create_app()
    assert fresh.state.profiles.public_config() == {
        "activeProvider": "openai",
        "profiles": {},
    }


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json",
        "application/json; note=multipart/form-data",
        "APPLICATION/JSON; NOTE=MULTIPART/FORM-DATA",
        "multipart/form-data; boundary=not-an-upload",
    ],
)
def test_oversized_json_cannot_bypass_limit_with_content_type(
    clients, content_type
):
    """Only an actual image upload may use the larger multipart limit."""
    node, python = clients
    body = (
        '{"provider":"openai","baseUrl":"https://api.example/v1",'
        '"model":"test","padding":"' + "x" * 10001 + '"}'
    )
    node_reply = node.post(
        "/api/config", content=body, headers={"Content-Type": content_type}
    )
    reply = python.post(
        "/api/config", content=body, headers={"Content-Type": content_type}
    )
    assert reply.status_code == node_reply.status_code == 413
    assert reply.json() == node_reply.json()


@pytest.mark.parametrize("provider", [[], {}])
def test_invalid_provider_containers_preserve_config(clients, provider):
    """Invalid provider types return the Node error and preserve the store."""
    node, python = clients
    config = {
        "provider": "openai",
        "baseUrl": "https://api.example/v1",
        "model": "test",
        "apiKey": "secret-test",
    }
    for client in (node, python):
        assert client.post("/api/config", json=config).status_code == 200
    before = python.get("/api/config").json()
    node_reply = node.post("/api/config", json={**config, "provider": provider})
    reply = python.post("/api/config", json={**config, "provider": provider})
    assert reply.status_code == node_reply.status_code == 400
    assert reply.json() == node_reply.json()
    assert (
        python.get("/api/config").json()
        == node.get("/api/config").json()
        == before
    )
    assert python.post("/api/test").status_code == 200


@pytest.mark.parametrize(
    "route,status", [("/api/unknown", 404), ("/api/test", 200)]
)
def test_ignored_body_routes_keep_status_with_oversized_json(
    clients, route, status
):
    """Routes that ignore request bodies must not inherit JSON validation."""
    node, python = clients
    config = {
        "provider": "openai",
        "baseUrl": "https://api.example/v1",
        "model": "test",
        "apiKey": "secret-test",
    }
    for client in (node, python):
        assert client.post("/api/config", json=config).status_code == 200
    body = '{"padding":"' + "x" * 10001 + '"}'
    node_reply = node.post(
        route, content=body, headers={"Content-Type": "application/json"}
    )
    reply = python.post(
        route, content=body, headers={"Content-Type": "application/json"}
    )
    assert reply.status_code == node_reply.status_code == status
    assert reply.json() == node_reply.json()
