"""Offline protocol, parsing, cancellation, and secret boundaries."""

import asyncio
import base64
import json
import logging

import httpx
import pytest

from backend import errors
from backend.providers import common
from backend.providers import text
from backend.providers import vision


def profile(protocol="openai"):
    """Return synthetic credentials that cannot access a provider."""
    return {
        "baseUrl": "https://mock.test/v1/",
        "apiKey": "sk-fake-secret",
        "model": "configured-model",
        "protocol": protocol,
    }


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_openai_and_claude_send_actual_image_bytes(protocol):
    """Verify openai and claude send actual image bytes."""
    original = b"\x00original-image\xff"

    def respond(request):
        payload = json.loads(request.content)
        assert request.extensions["timeout"]["read"] == 60
        content = payload["messages"][0]["content"]
        assert "资料" in content[0]["text"]
        if protocol == "anthropic":
            assert request.url.path == "/v1/messages"
            assert request.headers["x-api-key"] == profile()["apiKey"]
            assert request.headers["anthropic-version"] == "2023-06-01"
            assert payload["max_tokens"] == 1200
            encoded = content[1]["source"]["data"]
            assert content[1]["source"]["media_type"] == "image/png"
            response = {
                "content": [
                    {
                        "type": "text",
                        "text": '```json\n{"facts":["茶",1],"unknowns":["尺寸"],"text":"资料"}\n```',
                    }
                ]
            }
        else:
            assert request.url.path == "/v1/chat/completions"
            assert request.headers["Authorization"] == "Bearer sk-fake-secret"
            assert payload["stream"] is False
            encoded = content[1]["image_url"]["url"].split(",", 1)[1]
            response = {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": '{"facts":["茶",1],"unknowns":["尺寸"],"text":"资料"}',
                                }
                            ]
                        }
                    }
                ]
            }
        assert base64.b64decode(encoded) == original
        return httpx.Response(200, json={**response, "model": "returned-model"})

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond)
        ) as client:
            return await vision.analyze_vision(
                profile(protocol), [(original, "image/png")], "资料", {}, client
            )

    assert asyncio.run(run()) == {
        "facts": ["茶"],
        "unknowns": ["尺寸"],
        "text": "资料",
        "response_model": "returned-model",
    }


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "auth"),
        (403, "auth"),
        (404, "model"),
        (429, "rate_limit"),
        (504, "timeout"),
        (408, "timeout"),
        (400, "capability"),
        (422, "request"),
        (500, "provider"),
    ],
)
def test_classifies_auth_model_rate_limit_timeout(status, code):
    """Verify classifies auth model rate limit timeout."""
    error = errors.classify_provider_error(
        status,
        "unsupported vision" if status == 400 else "failure",
        "sk-fake-secret",
    )
    assert error.code == code
    assert error.status_code == 502
    assert error.provider_status == status


def test_redacts_provider_secret(caplog):
    """Verify redacts provider secret."""
    secret = profile()["apiKey"]

    async def run():
        transport = httpx.MockTransport(
            lambda _: httpx.Response(
                401, json={"error": {"message": f"invalid {secret}"}}
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(errors.AppError) as caught:
                await text.call_text(profile(), "test", client)
            logging.getLogger(__name__).error("%s", caught.value)
            assert secret not in str(caught.value)
            assert caught.value.__cause__ is None

    asyncio.run(run())
    assert secret not in caplog.text


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_text_protocol_and_trim(protocol):
    """Verify text protocol and trim."""

    def respond(request):
        payload = json.loads(request.content)
        assert payload["messages"] == [{"role": "user", "content": "prompt"}]
        if protocol == "anthropic":
            assert payload["max_tokens"] == 600
            assert request.headers["x-api-key"] == "sk-fake-secret"
            result = {"content": [{"type": "text", "text": " hello "}]}
        else:
            result = {"choices": [{"message": {"content": " hello "}}]}
        return httpx.Response(200, json=result)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond)
        ) as client:
            return await text.call_text(profile(protocol), "prompt", client)

    assert asyncio.run(run()) == "hello"


@pytest.mark.parametrize(
    "response",
    [{}, {"choices": [None]}, {"choices": [{"message": {"content": " "}}]}, []],
)
def test_invalid_response(response):
    """Verify invalid response."""

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=response)
            )
        ) as client:
            with pytest.raises(errors.AppError, match="模型") as caught:
                await text.call_text(profile(), "prompt", client)
            assert caught.value.code == "invalid_response"

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure,code",
    [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "network")],
)
def test_transport_errors_redact_exception(failure, code):
    """Verify transport errors redact exception."""

    def respond(request):
        raise failure("sk-fake-secret", request=request)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond)
        ) as client:
            with pytest.raises(errors.AppError) as caught:
                await text.call_text(profile(), "test", client)
            assert caught.value.code == code
            assert "sk-fake-secret" not in str(caught.value)
            assert caught.value.__suppress_context__

    asyncio.run(run())


def test_vision_plain_text_fallback_and_empty_input():
    """Verify vision plain text fallback and empty input."""

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": "商品资料"}}]},
                )
            )
        ) as client:
            result = await vision.analyze_vision(
                profile(), [(b"image", "image/jpeg")], "", {}, client
            )
            assert result == {
                "facts": [],
                "unknowns": ["商品规格、材质等尚待核实"],
                "text": "商品资料",
                "response_model": "configured-model",
            }
            with pytest.raises(errors.AppError) as caught:
                await vision.analyze_vision(profile(), [], "", {}, client)
            assert caught.value.code == "input"

    asyncio.run(run())


def test_cancellation_propagates():
    """Verify cancellation propagates."""

    async def respond(_):
        raise asyncio.CancelledError()

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond)
        ) as client:
            with pytest.raises(asyncio.CancelledError):
                await text.call_text(profile(), "test", client)

    asyncio.run(run())


def test_total_deadline_includes_response_body(monkeypatch):
    """A slow body must exceed a total deadline despite short phases."""

    monkeypatch.setattr(common, "REQUEST_TIMEOUT_SECONDS", 0.025, raising=False)

    class SlowBody(httpx.AsyncByteStream):
        """Deliver response chunks with delays below the phase timeout."""

        async def __aiter__(self):
            for chunk in (
                b'{"choices":',
                b'[{"message":',
                b'{"content":"hello"}}]}',
            ):
                await asyncio.sleep(0.015)
                yield chunk

    async def respond(_):
        await asyncio.sleep(0.015)
        return httpx.Response(200, stream=SlowBody())

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond)
        ) as client:
            with pytest.raises(errors.AppError) as caught:
                await text.call_text(profile(), "prompt", client)
            assert caught.value.code == "timeout"
            assert "sk-fake-secret" not in str(caught.value)
            assert caught.value.__suppress_context__

    asyncio.run(run())
