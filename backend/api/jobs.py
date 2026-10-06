"""Create and manage jobs using the existing camelCase HTTP contract."""

import re
import time
import uuid

import fastapi
from starlette import responses
from starlette import concurrency

from backend import asset_store
from backend import errors
from backend import exporter
from backend import job_store
from backend import profile_store

MODULE_NAMES = (
    "首屏主视觉",
    "核心卖点图",
    "使用场景图",
    "多角度图",
    "场景氛围图",
    "商品细节图",
    "品牌故事图",
    "尺寸/容量/尺码图",
    "效果对比图",
    "详细规格/参数表",
    "工艺制作图",
    "配件/赠品图",
    "系列展示图",
    "商品成分图",
    "售后保障图",
    "使用建议图",
)
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)
router = fastapi.APIRouter()


def _json(payload):
    return responses.JSONResponse(
        payload,
        media_type="application/json; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/jobs")
async def list_jobs(request: fastapi.Request):
    """List the legacy job summaries in descending creation order."""
    records = await concurrency.run_in_threadpool(
        request.app.state.jobs.list_jobs
    )
    return _json(
        {
            "jobs": [
                {
                    "id": job["id"],
                    "status": job["status"],
                    "createdAt": job["createdAt"],
                    "modules": len(job["modules"]),
                    "completed": sum(
                        module["status"] == "completed"
                        for module in job["modules"]
                    ),
                }
                for job in records
            ]
        }
    )


@router.get("/api/jobs/{job_id}")
async def get_job(request: fastapi.Request, job_id: str):
    """Read complete persisted metadata without transforming legacy fields."""
    return _json(
        await concurrency.run_in_threadpool(request.app.state.jobs.read, job_id)
    )


@router.post("/api/jobs/{job_id}/cancel")
async def cancel_job(request: fastapi.Request, job_id: str):
    """Stop future requests while retaining completed results."""
    job = await request.app.state.runner.cancel(job_id)
    return _json({"status": job["status"]})


@router.post("/api/jobs/{job_id}/retry")
async def retry_job(request: fastapi.Request, job_id: str, payload: dict):
    """Reset and enqueue exactly one failed module."""
    job = await request.app.state.runner.retry(job_id, payload.get("moduleId"))
    return _json({"status": job["status"]})


@router.delete("/api/jobs/{job_id}")
async def delete_job(request: fastapi.Request, job_id: str):
    """Drain outstanding work and remove the job and its source draft."""
    await request.app.state.runner.delete(job_id)
    return _json({"ok": True})


def create_job(
    payload: dict,
    assets: asset_store.AssetStore,
    jobs: job_store.JobStore,
    profiles: profile_store.ProfileStore,
) -> dict:
    """Validate and persist the Node-compatible queued job.

    Args:
        payload: Existing camelCase job request.
        assets: Source draft and image storage.
        jobs: Atomic job metadata storage.
        profiles: Process-local role credentials.

    Returns:
        Complete persisted job without credentials.

    Raises:
        AppError: Required inputs or credentials are missing or invalid.
        OSError: Snapshot assets or metadata cannot be written.
    """
    draft_id = payload.get("draftId")
    if not isinstance(draft_id, str) or not _UUID.fullmatch(draft_id):
        raise errors.AppError("请先上传商品原图")
    if not assets.read_draft(draft_id)["images"]:
        raise errors.AppError("请先上传商品原图")
    brief = str(payload.get("brief") or "")
    if not brief.strip():
        raise errors.AppError("请先填写商品资料")
    selected = payload.get("modules")
    if (
        not isinstance(selected, list)
        or not 1 <= len(selected) <= 16
        or any(
            not isinstance(name, str) or name not in MODULE_NAMES
            for name in selected
        )
        or len(set(selected)) != len(selected)
    ):
        raise errors.AppError("请至少选择一个有效模块")
    vision = profiles.get_role("vision")
    image = profiles.get_role("image")
    settings = payload.get("settings")
    if not isinstance(settings, dict):
        settings = {}
    job_id = str(uuid.uuid4())
    try:
        images = assets.copy_to_job(draft_id, job_id)
        now = time.time_ns() // 1_000_000
        return jobs.create(
            {
                "id": job_id,
                "draftId": draft_id,
                "brief": brief[:4000],
                "settings": {
                    key: str(settings.get(key) or "")[:80]
                    for key in (
                        "platform",
                        "market",
                        "language",
                        "quality",
                        "ratio",
                        "style",
                    )
                },
                "visionProfile": {
                    key: vision[key]
                    for key in ("provider", "baseUrl", "model", "protocol")
                },
                "imageProfile": {
                    key: image[key] for key in ("provider", "baseUrl", "model")
                },
                "images": images,
                "modules": [
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
                    for index, name in enumerate(selected)
                ],
                "status": "queued",
                "stage": "queued",
                "analysis": None,
                "createdAt": now,
                "updatedAt": now,
            }
        )
    except Exception:
        jobs.remove(job_id)
        raise


@router.post("/api/jobs")
async def post_job(request: fastapi.Request, payload: dict):
    """Persist a job before handing its ID to the injected queue callback."""
    state = request.app.state
    job = await concurrency.run_in_threadpool(
        create_job, payload, state.assets, state.jobs, state.profiles
    )
    if state.enqueue is not None:
        state.enqueue(job["id"])
    else:
        await state.runner.enqueue(job["id"])
    return responses.JSONResponse(
        {"jobId": job["id"], "status": job["status"]},
        status_code=201,
        media_type="application/json; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/jobs/{job_id}/originals/{asset_id}")
async def get_original(request: fastapi.Request, job_id: str, asset_id: str):
    """Read the job snapshot original selected by its stored asset ID."""
    return await concurrency.run_in_threadpool(
        _read_result, request.app.state.jobs, job_id, asset_id, True
    )


@router.get("/api/jobs/{job_id}/images/{module_id}")
async def get_image(request: fastapi.Request, job_id: str, module_id: str):
    """Read a completed result selected by its stored module ID."""
    return await concurrency.run_in_threadpool(
        _read_result, request.app.state.jobs, job_id, module_id, False
    )


def _read_result(jobs, job_id, identifier, original):
    job = jobs.read(job_id)
    message = "原图不存在" if original else "结果图不存在"
    records = job.get("images", []) if original else job["modules"]
    key = "assetId" if original else "id"
    record = next((item for item in records if item[key] == identifier), None)
    if record is None or (not original and record["status"] != "completed"):
        raise errors.AppError(message, 404)
    name = record.get("originalName" if original else "imageFile")
    try:
        content = exporter.read_job_image(
            jobs.job_dir(job_id), "originals" if original else "results", name
        )
    except errors.AppError as error:
        raise errors.AppError(message, 404) from error
    mime = (
        record.get("mimeType")
        if original
        else (
            "image/webp"
            if name.endswith(".webp")
            else "image/jpeg"
            if name.endswith(".jpg")
            else "image/png"
        )
    )
    return responses.Response(
        content, media_type=mime, headers={"Cache-Control": "no-store"}
    )


@router.get("/api/jobs/{job_id}/export")
async def export_job(request: fastapi.Request, job_id: str):
    """Download the legacy archive without exposing private storage paths."""

    def build():
        jobs = request.app.state.jobs
        return exporter.build_job_archive(
            jobs.read(job_id), jobs.job_dir(job_id)
        )

    content = await concurrency.run_in_threadpool(build)
    return responses.Response(
        content,
        media_type="application/zip",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": (
                f'attachment; filename="product-detail-{job_id}.zip"'
            ),
        },
    )
