"""Legacy draft routes with bounded multipart image uploads."""

import re

import fastapi
from starlette import concurrency
from starlette import datastructures
from starlette import formparsers
from starlette import responses

from backend import errors

_MAX_BYTES = 10 * 1024 * 1024
_ROUTE_ID = re.compile(r"[0-9a-f-]{36}", re.I)


class _LegacyJSONResponse(responses.JSONResponse):
    """Preserve the explicit UTF-8 charset in legacy JSON responses."""

    media_type = "application/json; charset=utf-8"


def _json_headers(response: fastapi.Response) -> None:
    """Keep successful JSON response caching compatible with Node."""
    response.headers["Cache-Control"] = "no-store"


def _check_route_ids(request: fastapi.Request) -> None:
    """Apply Node route eligibility before parsing or accessing storage."""
    for value in request.path_params.values():
        if not _ROUTE_ID.fullmatch(value):
            raise fastapi.HTTPException(status_code=404)


router = fastapi.APIRouter(
    prefix="/api/drafts",
    dependencies=[
        fastapi.Depends(_check_route_ids),
        fastapi.Depends(_json_headers),
    ],
    default_response_class=_LegacyJSONResponse,
)


class _ImageParser(formparsers.MultiPartParser):
    """Bound file bytes during parsing, before temporary files can grow."""

    received_bytes = 0
    complete = False

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        """Reject excess bytes before scheduling them for disk writes."""
        self.received_bytes += end - start
        if self.received_bytes > _MAX_BYTES:
            raise formparsers.MultiPartException("每张图片不能超过 10 MB")
        super().on_part_data(data, start, end)

    def on_end(self) -> None:
        """Record the closing boundary so truncated requests are rejected."""
        self.complete = True

    def close_files(self) -> None:
        """Close even unfinished files after malformed input or disconnects."""
        # Starlette's internal cleanup list includes unfinished uploads;
        # retest malformed-request cleanup when upgrading Starlette.
        for uploaded in self._files_to_close_on_error:
            uploaded.close()


async def _read_image(request: fastapi.Request) -> tuple[bytes, str]:
    """Read exactly one image field and release all parser temporary files."""
    if (
        request.headers.get("content-type", "").split(";")[0].strip().lower()
        != "multipart/form-data"
    ):
        raise errors.AppError("请上传 multipart 图片")
    parser = _ImageParser(
        request.headers, request.stream(), max_files=1, max_fields=0
    )
    try:
        try:
            form = await parser.parse()
        except (formparsers.MultiPartException, ValueError) as error:
            if parser.received_bytes > _MAX_BYTES:
                raise errors.AppError("每张图片不能超过 10 MB", 413) from error
            raise errors.AppError("只能上传一个 image 字段") from error
        items = form.multi_items()
        if not parser.complete or not items:
            raise errors.AppError("未收到图片")
        if (
            len(items) != 1
            or items[0][0] != "image"
            or not isinstance(items[0][1], datastructures.UploadFile)
        ):
            raise errors.AppError("只能上传一个 image 字段")
        image = items[0][1]
        content = await image.read(_MAX_BYTES + 1)
        if len(content) > _MAX_BYTES:
            raise errors.AppError("每张图片不能超过 10 MB", 413)
        return content, image.filename or ""
    finally:
        parser.close_files()


@router.post("", status_code=201)
def create_draft(request: fastapi.Request):
    """Create an empty legacy draft."""
    return request.app.state.assets.create_draft()


@router.get("/{draft_id}")
def read_draft(draft_id: str, request: fastapi.Request):
    """Expose only public image identifiers and preview URLs."""
    draft = request.app.state.assets.read_draft(draft_id)
    return {
        "draftId": draft_id,
        "images": [
            {
                "assetId": image["assetId"],
                "previewUrl": (
                    f"/api/drafts/{draft_id}/images/"
                    f"{image['assetId']}/preview"
                ),
            }
            for image in draft["images"]
        ],
    }


@router.post("/{draft_id}/images", status_code=201)
async def upload_image(draft_id: str, request: fastapi.Request):
    """Validate the full multipart request before persisting an image."""
    content, filename = await _read_image(request)
    return await concurrency.run_in_threadpool(
        request.app.state.assets.add_image, draft_id, content, filename
    )


@router.delete("/{draft_id}/images/{asset_id}")
def delete_image(draft_id: str, asset_id: str, request: fastapi.Request):
    """Remove one asset and return the existing success envelope."""
    request.app.state.assets.remove_image(draft_id, asset_id)
    return {"ok": True}


@router.get("/{draft_id}/images/{asset_id}")
@router.get("/{draft_id}/images/{asset_id}/preview")
def read_image(draft_id: str, asset_id: str, request: fastapi.Request):
    """Return unchanged original bytes or the generated WebP preview."""
    content, mime_type = request.app.state.assets.read_image(
        draft_id, asset_id, preview=request.url.path.endswith("/preview")
    )
    return responses.Response(
        content, media_type=mime_type, headers={"Cache-Control": "no-store"}
    )
