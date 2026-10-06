"""In-memory role configuration and public catalog contracts."""

import json
import pathlib

import pytest
from fastapi import testclient

from backend import errors
from backend import main
from backend import profile_store
from backend import provider_catalog

ROOT = pathlib.Path(__file__).resolve().parent.parent


def make_store():
    """Load the public presets into an independent store."""

    return profile_store.ProfileStore(
        provider_catalog.load_catalog(ROOT / "public/providers.json")
    )


def config(role="vision", **changes):
    """Build a synthetic role configuration."""
    return {
        "role": role,
        "provider": "openai",
        "baseUrl": "https://example.test/v1",
        "model": "test-model",
        "apiKey": "fake-secret-" + role,
        **changes,
    }


def test_provider_catalog_matches_existing_options():
    """Keep every preset field equal to the captured Node catalog."""
    catalog = provider_catalog.load_catalog(ROOT / "public/providers.json")
    old = (ROOT / "tests/fixtures/provider_catalog.json").read_text("utf-8")
    assert len(catalog) == 13
    assert catalog == json.loads(old)


def test_role_configs_are_separate_and_secret_is_not_public():
    store = make_store()
    store.save_role(config())
    public = store.save_role(config("image"))
    assert (
        store.get_role("vision")["apiKey"] != store.get_role("image")["apiKey"]
    )
    assert "apiKey" not in json.dumps(public)
    assert "fake-secret" not in json.dumps(store.public_roles())
    assert public["roles"]["image"]["hasKey"] is True
    assert make_store().public_roles() == {"roles": {}}


def test_reuses_key_only_for_same_provider_and_url_and_resets_tested():
    store = make_store()
    store.save_role(config())
    store.mark_tested("vision")
    assert store.public_roles()["roles"]["vision"]["tested"] is True
    store.save_role(config(model="different", apiKey=" "))
    assert store.get_role("vision")["apiKey"] == "fake-secret-vision"
    assert store.public_roles()["roles"]["vision"]["tested"] is False
    for change in (
        {"baseUrl": "https://other.test/v1"},
        {"provider": "custom"},
    ):
        store.save_role(config())
        store.save_role(config(apiKey="", **change))
        assert store.public_roles()["roles"]["vision"]["hasKey"] is False
        with pytest.raises(errors.AppError) as caught:
            store.get_role("vision")
        assert caught.value.code == "credentials"


@pytest.mark.parametrize(
    "url",
    [
        "invalid",
        "http://example.test",
        "ftp://localhost",
        "https://user:pass@example.test",
        "https://example.test?a=1",
        "https://example.test#part",
        "https:///missing",
        "https://example.test:bad",
    ],
)
def test_rejects_invalid_urls(url):
    with pytest.raises(errors.AppError):
        make_store().save_role(config(baseUrl=url))


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/v1",
        "http://localhost:9000/v1",
        "http://127.0.0.1:9000",
    ],
)
def test_accepts_https_and_local_http(url):
    assert (
        make_store().save_role(config(baseUrl=url))["roles"]["vision"][
            "baseUrl"
        ]
        == url
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"role": "text"},
        {"provider": "missing"},
        {"role": "image", "provider": "anthropic"},
        {"model": " "},
    ],
)
def test_rejects_invalid_role_provider_or_model(changes):
    with pytest.raises(errors.AppError):
        make_store().save_role(config(**changes))


def test_missing_credentials_and_anthropic_protocol():
    store = make_store()
    with pytest.raises(errors.AppError) as caught:
        store.mark_tested("vision")
    assert caught.value.code == "credentials"
    store.save_role(config(provider="anthropic"))
    assert store.get_role("vision")["protocol"] == "anthropic"


def test_role_api_and_public_catalog(tmp_path):

    with testclient.TestClient(
        main.create_app(data_dir=tmp_path), base_url="http://127.0.0.1:8766"
    ) as client:
        assert client.get("/api/roles").json() == {"roles": {}}
        response = client.post("/api/roles", json=config())
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert "charset=utf-8" in response.headers["content-type"]
        assert "fake-secret" not in response.text
        assert set(response.json()) == {"roles"}
        assert set(response.json()["roles"]["vision"]) == {
            "role",
            "provider",
            "baseUrl",
            "model",
            "hasKey",
            "tested",
        }
        assert client.get("/api/roles").json() == response.json()
        assert len(client.get("/providers.json").json()) == 13
        assert client.get("/providers.js").status_code == 404
        assert (
            client.post(
                "/api/roles", json=config(role="image", provider="anthropic")
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/roles",
                content="bad",
                headers={"Content-Type": "application/json"},
            ).status_code
            == 400
        )
        assert not list(tmp_path.rglob("*"))


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ([{"apiKey": "fake-http-secret"}], "请求参数无效"),
        (
            config(baseUrl="invalid", apiKey="fake-http-secret"),
            "接口地址格式不正确",
        ),
        (
            config(baseUrl="http://example.test", apiKey="fake-http-secret"),
            "模型 ID 或接口地址无效",
        ),
    ],
)
def test_role_http_errors_never_echo_submitted_key(tmp_path, body, message):
    with testclient.TestClient(
        main.create_app(data_dir=tmp_path), base_url="http://127.0.0.1:8766"
    ) as client:
        response = client.post("/api/roles", json=body)
        assert response.status_code == 400
        assert response.json() == {"error": message}
        assert "fake-http-secret" not in response.text
        assert client.get("/api/roles").json() == {"roles": {}}
