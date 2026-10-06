"""Public model role configuration endpoints."""

import io

import fastapi
from PIL import Image
from starlette import concurrency
from starlette import responses

from backend import asset_store
from backend import errors
from backend import prompt_builder
from backend.providers import image
from backend.providers import vision


class PublicJSONResponse(responses.JSONResponse):
    """Preserve the existing UTF-8 JSON media type."""

    media_type = "application/json; charset=utf-8"


def json_headers(response: fastapi.Response) -> None:
    """Prevent caching model configuration responses."""
    response.headers["Cache-Control"] = "no-store"


router = fastapi.APIRouter(
    prefix="/api/roles",
    dependencies=[fastapi.Depends(json_headers)],
    default_response_class=PublicJSONResponse,
)

assist_router = fastapi.APIRouter(
    dependencies=[fastapi.Depends(json_headers)],
    default_response_class=PublicJSONResponse,
)


@router.get("")
def public_roles(request: fastapi.Request):
    """Return configured roles without credentials."""
    return request.app.state.profiles.public_roles()


@router.post("")
async def save_role(body: dict, request: fastapi.Request):
    """Validate and save one process-local role configuration."""
    result = request.app.state.profiles.save_role(body)
    await request.app.state.runner.resume_waiting()
    return result


@router.post("/test")
async def test_role(body: dict, request: fastapi.Request):
    """Test actual image capability using only a built-in red color block."""
    role = body.get("role")
    if role not in ("vision", "image"):
        raise errors.AppError("模型角色无效")
    state = request.app.state
    config = state.profiles.get_role(role)
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, "PNG")
    references = [(buffer.getvalue(), "image/png")]
    async with state.runner.model_lock:
        if role == "vision":
            result = await vision.analyze_vision(
                config,
                references,
                "红色测试色块，请说明已知和未知信息",
                {},
                state.http_client,
            )
            response_model = result["response_model"]
        else:
            result = await image.generate_image(
                config,
                references,
                "保持这个红色色块外观，生成干净的商品测试图",
                "1:1",
                "普通A+",
                state.http_client,
            )
            response_model = result.response_model
    state.profiles.mark_tested(role, config)
    return {"ok": True, "role": role, "responseModel": response_model}


@assist_router.post("/api/assist-copy")
async def assist_copy(body: dict, request: fastapi.Request):
    """Analyze draft originals on an explicit action without persisting copy."""
    state = request.app.state
    draft_id = body.get("draftId")
    originals = await concurrency.run_in_threadpool(
        _load_originals, state.assets, draft_id
    )
    config = state.profiles.get_role("vision")
    async with state.runner.model_lock:
        analysis = await vision.analyze_vision(
            config,
            originals,
            str(body.get("source") or ""),
            {
                "platform": body.get("platform"),
                "language": body.get("language"),
            },
            state.http_client,
        )
    return {
        "text": prompt_builder.ensure_unknown_markers(analysis),
        "facts": analysis["facts"],
        "unknowns": analysis["unknowns"],
        "provider": config["provider"],
        "responseModel": analysis["response_model"],
        "imageCount": len(originals),
    }


def _load_originals(
    assets: asset_store.AssetStore, draft_id: str
) -> list[tuple[bytes, str]]:
    """Load all draft originals in a worker, allowing unrelated requests."""
    draft = assets.read_draft(draft_id)
    if not draft["images"]:
        raise errors.AppError("请先上传至少一张商品原图")
    return [
        assets.read_image(draft_id, record["assetId"])
        for record in draft["images"]
    ]
