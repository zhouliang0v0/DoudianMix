"""OpenAI Images Edits requests and fully decoded PNG results."""

import asyncio
import base64
import binascii
import dataclasses
import io
import re
import warnings

import httpx
from PIL import Image
from PIL import ImageOps

from backend import errors
from backend.providers import common

REQUEST_TIMEOUT_SECONDS = 180.0
_MAX_PIXELS = 80_000_000
_LEGACY = re.compile(r"^gpt-image-1(?:$|\.|-mini)")


@dataclasses.dataclass(frozen=True)
class GeneratedImage:
    """A validated generated PNG and the provider's model identifier.

    Attributes:
        data: Complete PNG bytes.
        mime_type: Always image/png.
        response_model: Reported model, falling back to the requested model.
    """

    data: bytes
    mime_type: str
    response_model: str


def resolve_size(ratio: str, model: str) -> str:
    """Map requested ratios to current or legacy model dimensions."""
    legacy = bool(_LEGACY.match(model))
    if "16:9" in ratio:
        return "1536x1024" if legacy else "1536x864"
    if "4:5" in ratio:
        return "1024x1536" if legacy else "1024x1280"
    return "1024x1024"


def resolve_quality(quality: str) -> str:
    """Map high quality labels; other labels retain medium quality."""
    return (
        "high" if "高级" in quality or "high" in quality.lower() else "medium"
    )


def _decode_png(encoded: str, ratio: str, model: str) -> bytes:
    """Verify structure and decode every frame before optional center crop."""
    try:
        raw = base64.b64decode(encoded, validate=True)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if source.format not in ("PNG", "JPEG", "WEBP"):
                    raise ValueError("Unsupported format")
                if source.width * source.height > _MAX_PIXELS:
                    raise ValueError("Too many pixels")
                source.verify()
            with Image.open(io.BytesIO(raw)) as source:
                for frame in range(getattr(source, "n_frames", 1)):
                    source.seek(frame)
                    if source.width * source.height > _MAX_PIXELS:
                        raise ValueError("Too many pixels")
                    source.load()
                source.seek(0)
                output = ImageOps.exif_transpose(source).convert("RGBA")
                if _LEGACY.match(model):
                    target = (
                        (1024, 1280)
                        if "4:5" in ratio
                        else (1536, 864)
                        if "16:9" in ratio
                        else None
                    )
                    if target:
                        output = ImageOps.fit(
                            output, target, Image.Resampling.LANCZOS
                        )
                buffer = io.BytesIO()
                output.save(buffer, format="PNG")
                return buffer.getvalue()
    except (
        ValueError,
        binascii.Error,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise errors.AppError(
            "服务商未返回有效图片", 502, "invalid_response"
        ) from None


async def generate_image(
    profile: dict,
    references: list[tuple[bytes, str]],
    module_brief: str,
    ratio: str,
    quality: str,
    client: httpx.AsyncClient,
) -> GeneratedImage:
    """Edit original references using an injected asynchronous client.

    Args:
        profile: Image role baseUrl, model, and in-memory apiKey.
        references: Original bytes with their declared MIME types.
        module_brief: Module prompt, limited to the legacy 10000 characters.
        ratio: Requested aspect ratio label.
        quality: Requested quality label.
        client: HTTP client supplied by the caller.

    Returns:
        Fully decoded and normalized PNG result.

    Raises:
        errors.AppError: Missing references or sanitized provider failure.
        asyncio.CancelledError: Caller canceled the request.
    """
    if not references:
        raise errors.AppError("缺少原图参考", 400)
    model = profile["model"]
    fields = {
        "model": model,
        "prompt": str(module_brief or "")[:10000],
        "size": resolve_size(ratio, model),
        "quality": resolve_quality(quality),
        "output_format": "png",
    }
    if _LEGACY.match(model) and "mini" not in model:
        fields["input_fidelity"] = "high"
    extensions = {"image/jpeg": "jpg", "image/webp": "webp"}
    files = [
        (
            "image[]",
            (f"reference-{index}.{extensions.get(mime, 'png')}", data, mime),
        )
        for index, (data, mime) in enumerate(references, 1)
    ]
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
            response = await client.post(
                f'{profile["baseUrl"].rstrip("/")}/images/edits',
                headers={"Authorization": f'Bearer {profile["apiKey"]}'},
                data=fields,
                files=files,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
    except (TimeoutError, httpx.TimeoutException):
        raise errors.AppError("生图请求超时", 502, "timeout") from None
    except httpx.RequestError:
        raise errors.AppError("生图网络请求失败", 502, "network") from None
    data = common.checked_json(response, profile["apiKey"])
    results = data.get("data")
    first = results[0] if isinstance(results, list) and results else None
    encoded = first.get("b64_json") if isinstance(first, dict) else None
    if not isinstance(encoded, str) or not encoded:
        raise errors.AppError("服务商未返回有效图片", 502, "invalid_response")
    response_model = data.get("model")
    return GeneratedImage(
        _decode_png(encoded, ratio, model),
        "image/png",
        response_model
        if isinstance(response_model, str) and response_model
        else model,
    )
