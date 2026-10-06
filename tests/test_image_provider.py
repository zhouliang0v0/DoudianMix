"""Contract checks for the OpenAI image editing adapter."""

import asyncio
import base64
from email import policy
from email.parser import BytesParser
import io

import httpx
from PIL import Image
import pytest

from backend import errors
from backend.providers import image

PROFILE = {
    "baseUrl": "https://example.test/v1///",
    "model": "gpt-image-1",
    "apiKey": "fictional-secret",
}


def pixels(fmt="PNG", size=(32, 48)):
    output = io.BytesIO()
    Image.new("RGB", size, "green").save(output, format=fmt)
    return output.getvalue()


def run_generate(handler, references=None, ratio="1:1", profile=None):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            return await image.generate_image(
                profile or PROFILE,
                references
                if references is not None
                else [(pixels(), "image/png")],
                "商品绿色茶叶",
                ratio,
                "高级A+",
                client,
            )

    return asyncio.run(run())


@pytest.mark.parametrize(
    "model,fidelity",
    [
        ("gpt-image-1", "high"),
        ("gpt-image-1.5", "high"),
        ("gpt-image-1-mini", None),
        ("gpt-image-2", None),
    ],
)
def test_edits_request_contains_each_reference_and_settings(model, fidelity):
    references = [
        (pixels(), "image/png"),
        (pixels("JPEG"), "image/jpeg"),
        (pixels("WEBP"), "image/webp"),
    ]

    def handler(request):
        assert str(request.url) == "https://example.test/v1/images/edits"
        assert request.headers["authorization"] == "Bearer fictional-secret"
        assert request.extensions["timeout"]["read"] == 180
        body = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: "
            + request.headers["content-type"].encode()
            + b"\r\n\r\n"
            + request.content
        )
        parts = list(body.iter_parts())
        sent_images = [
            part
            for part in parts
            if part.get_param("name", header="content-disposition") == "image[]"
        ]
        assert len(sent_images) == len(references)
        assert [
            (part.get_payload(decode=True), part.get_content_type())
            for part in sent_images
        ] == references
        fields = {
            part.get_param(
                "name", header="content-disposition"
            ): part.get_payload(decode=True).decode()
            for part in parts
            if part not in sent_images
        }
        assert fields == {
            "model": model,
            "prompt": "商品绿色茶叶",
            "size": "1024x1536"
            if model.startswith("gpt-image-1")
            else "1024x1280",
            "quality": "high",
            "output_format": "png",
            **({"input_fidelity": fidelity} if fidelity else {}),
        }
        return httpx.Response(
            200,
            json={
                "model": "actual-model",
                "data": [{"b64_json": base64.b64encode(pixels()).decode()}],
            },
        )

    result = run_generate(
        handler, references, "4:5", {**PROFILE, "model": model}
    )
    assert result.mime_type == "image/png"
    assert result.response_model == "actual-model"


@pytest.mark.parametrize(
    "encoded",
    [
        base64.b64encode(pixels()[:65]).decode(),
        "%%%",
        base64.b64encode(b"invalid").decode(),
        base64.b64encode(pixels("GIF")).decode(),
        None,
    ],
)
def test_rejects_truncated_success_response(encoded):
    with pytest.raises(errors.AppError) as caught:
        run_generate(
            lambda _: httpx.Response(
                200, json={"data": [{"b64_json": encoded}]}
            )
        )
    assert caught.value.code == "invalid_response"


@pytest.mark.parametrize(
    "ratio,size", [("4:5", (1024, 1280)), ("16:9", (1536, 864))]
)
def test_legacy_ratio_is_cropped(ratio, size):
    result = run_generate(
        lambda _: httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(pixels()).decode()}]},
        ),
        ratio=ratio,
    )
    with Image.open(io.BytesIO(result.data)) as output:
        output.load()
        assert output.size == size
        assert output.format == "PNG"


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_results_are_valid_png(fmt):
    result = run_generate(
        lambda _: httpx.Response(
            200,
            json={
                "data": [{"b64_json": base64.b64encode(pixels(fmt)).decode()}]
            },
        )
    )
    assert result.response_model == PROFILE["model"]
    with Image.open(io.BytesIO(result.data)) as output:
        output.load()
        assert output.format == "PNG"
        assert output.size == (32, 48)


def test_missing_references_are_rejected_before_request():
    def handler(_):
        pytest.fail("empty reference request must not be sent")

    with pytest.raises(errors.AppError) as caught:
        run_generate(handler, [])
    assert caught.value.status_code == 400


@pytest.mark.parametrize(
    "status,code",
    [(401, "auth"), (404, "model"), (429, "rate_limit"), (504, "timeout")],
)
def test_provider_errors_are_classified_and_redacted(status, code):
    with pytest.raises(errors.AppError) as caught:
        run_generate(
            lambda _: httpx.Response(
                status, json={"error": {"message": PROFILE["apiKey"]}}
            )
        )
    assert caught.value.code == code
    assert PROFILE["apiKey"] not in str(caught.value)


def test_size_and_quality_match_legacy_rules():
    assert image.resolve_size("16:9", "gpt-image-1") == "1536x1024"
    assert image.resolve_size("16:9", "gpt-image-2") == "1536x864"
    assert image.resolve_size("自动适配", "gpt-image-2") == "1024x1024"
    assert image.resolve_quality("HIGH") == "high"
    assert image.resolve_quality("普通A+") == "medium"


@pytest.mark.parametrize("payload", [[], {}, {"data": []}, {"data": [42]}])
def test_invalid_response_shape(payload):
    with pytest.raises(errors.AppError) as caught:
        run_generate(lambda _: httpx.Response(200, json=payload))
    assert caught.value.code == "invalid_response"


@pytest.mark.parametrize(
    "exception,code",
    [
        (httpx.ReadTimeout("secret"), "timeout"),
        (httpx.ConnectError("secret"), "network"),
    ],
)
def test_transport_failure_is_sanitized(exception, code):
    def handler(_):
        raise exception

    with pytest.raises(errors.AppError) as caught:
        run_generate(handler)
    assert caught.value.code == code
    assert "secret" not in str(caught.value)


def test_deadline_cancels_slow_request(monkeypatch):
    monkeypatch.setattr(image, "REQUEST_TIMEOUT_SECONDS", 0.01)

    async def handler(_):
        await asyncio.Event().wait()

    with pytest.raises(errors.AppError) as caught:
        run_generate(handler)
    assert caught.value.code == "timeout"


def test_caller_cancellation_propagates():
    async def handler(_):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        run_generate(handler)


def test_pixel_limit_rejects_decodable_image(monkeypatch):
    monkeypatch.setattr(image, "_MAX_PIXELS", 100)
    with pytest.raises(errors.AppError) as caught:
        run_generate(
            lambda _: httpx.Response(
                200,
                json={
                    "data": [{"b64_json": base64.b64encode(pixels()).decode()}]
                },
            )
        )
    assert caught.value.code == "invalid_response"
