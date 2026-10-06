"""FastAPI entry point for the local migration runtime."""

import contextlib
import pathlib
import re
from collections import abc

import fastapi
from fastapi import exceptions
import httpx
from starlette import exceptions as starlette_exceptions
from starlette import responses

from backend import errors
from backend import asset_store
from backend import job_runner
from backend import job_store
from backend import profile_store
from backend import provider_catalog
from backend.api import drafts
from backend.api import jobs
from backend.api import legacy
from backend.api import models

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_STATIC_FILES = {
    "/": "public/index.html",
    "/index.html": "public/index.html",
    "/settings.html": "public/settings.html",
    "/results.html": "public/results.html",
    "/styles.css": "public/styles.css",
    "/script.js": "public/script.js",
    "/settings.js": "public/settings.js",
    "/results.js": "public/results.js",
    "/providers.json": "public/providers.json",
}
_JSON_BODY_ROUTES = frozenset(
    {
        "/api/config",
        "/api/roles",
        "/api/roles/test",
        "/api/assist-copy",
        "/api/generate-copy",
        "/api/jobs",
    }
)
_RETRY_ROUTE = re.compile(r"/api/jobs/[0-9a-f-]{36}/retry", re.I)
_IMAGE_UPLOAD_ROUTE = re.compile(r"/api/drafts/[0-9a-f-]{36}/images", re.I)


def _consumes_json(request: fastapi.Request) -> bool:
    """Match Node's body readers, preserving ignored and unknown routes."""
    if request.method != "POST":
        return False
    path = request.url.path
    media_type = request.headers.get("content-type", "").split(";", 1)[0]
    if (
        media_type.strip().lower() == "multipart/form-data"
        and _IMAGE_UPLOAD_ROUTE.fullmatch(path)
    ):
        return False
    return path in _JSON_BODY_ROUTES or _RETRY_ROUTE.fullmatch(path) is not None


@contextlib.asynccontextmanager
async def _lifespan(application: fastapi.FastAPI) -> abc.AsyncIterator[None]:
    """Own provider connections, queue recovery and draft cleanup."""
    async with httpx.AsyncClient(
        transport=application.state.transport, timeout=60.0
    ) as client:
        application.state.http_client = client
        runner = job_runner.JobRunner(
            application.state.jobs,
            application.state.assets,
            application.state.profiles,
            client,
        )
        application.state.runner = runner
        await runner.start()
        try:
            yield
        finally:
            await runner.close()


def _error_response(
    message: str, status_code: int, code: str | None = None
) -> responses.JSONResponse:
    """Build the existing public error shape without framework details."""
    payload = {"error": message}
    if code is not None:
        payload["code"] = code
    return responses.JSONResponse(
        payload,
        status_code=status_code,
        media_type="application/json; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


def create_app(
    data_dir: pathlib.Path | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    enqueue: abc.Callable[[str], None] | None = None,
) -> fastapi.FastAPI:
    """Create the single process local application.

    Args:
        data_dir: Isolated data directory for migration or tests.
        transport: Optional provider transport for deterministic tests.
        enqueue: Optional callback receiving each persisted queued job ID.

    Returns:
        Application with local access checks and public static routes.
    """
    application = fastapi.FastAPI(
        lifespan=_lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    application.state.data_dir = (
        data_dir if data_dir is not None else _ROOT / "data"
    )
    application.state.transport = transport
    application.state.assets = asset_store.AssetStore(
        application.state.data_dir
    )
    application.state.jobs = job_store.JobStore(application.state.data_dir)
    application.state.enqueue = enqueue
    application.include_router(jobs.router)
    application.include_router(drafts.router)
    application.state.profiles = profile_store.ProfileStore(
        provider_catalog.load_catalog(_ROOT / "public/providers.json")
    )
    application.include_router(models.router)
    application.include_router(models.assist_router)
    application.include_router(legacy.router)

    @application.middleware("http")
    async def require_local_page(request: fastapi.Request, call_next):
        """Reject foreign hosts and origins before routing."""
        server = request.scope.get("server")
        port = server[1] if server else None
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        allowed_origins = {f"http://{host}" for host in allowed_hosts}
        origin = request.headers.get("origin")
        if request.headers.get("host") not in allowed_hosts or (
            origin is not None and origin not in allowed_origins
        ):
            return _error_response("仅允许本机页面访问", 403)
        if _consumes_json(request):
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 40000:
                    return _error_response("请求内容过长", 413)
            # Node counts UTF-16 characters after decoding the JSON stream.
            if (
                len(body.decode("utf-8", errors="replace").encode("utf-16-le"))
                // 2
                > 10000
            ):
                return _error_response("请求内容过长", 413)
            # Replay the bounded stream for FastAPI's body parser.
            request._body = bytes(body) or b"{}"  # pylint: disable=protected-access
            request.scope["headers"] = [
                (key, value)
                for key, value in request.scope["headers"]
                if key != b"content-type"
            ] + [(b"content-type", b"application/json")]
        return await call_next(request)

    @application.exception_handler(errors.AppError)
    async def handle_app_error(
        request: fastapi.Request, error: errors.AppError
    ):
        """Expose intentional application errors."""
        del request
        return _error_response(str(error), error.status_code, error.code)

    @application.exception_handler(exceptions.RequestValidationError)
    async def handle_validation_error(request: fastapi.Request, error):
        """Keep invalid requests in the established error contract."""
        del request
        message = (
            "JSON 格式不正确"
            if any(item["type"] == "json_invalid" for item in error.errors())
            else "请求参数无效"
        )
        return _error_response(message, 400)

    @application.exception_handler(starlette_exceptions.HTTPException)
    async def handle_http_error(request: fastapi.Request, error):
        """Map framework routing errors to public JSON."""
        del request
        status = 404 if error.status_code == 405 else error.status_code
        message = "未找到页面" if status == 404 else "请求失败"
        return _error_response(message, status)

    @application.exception_handler(FileNotFoundError)
    async def handle_missing_file(request: fastapi.Request, error):
        """Treat missing stored files as absent resources."""
        del request, error
        return _error_response("未找到文件", 404)

    @application.exception_handler(Exception)
    async def handle_unexpected_error(request: fastapi.Request, error):
        """Keep internal failures and provider details out of responses."""
        del request, error
        return _error_response("请求失败", 500)

    async def serve_static(request: fastapi.Request):
        """Serve only registered public files."""
        path = _ROOT / _STATIC_FILES[request.url.path]
        if not path.is_file():
            raise errors.AppError("未找到页面", 404)
        media_type = (
            "text/javascript; charset=utf-8" if path.suffix == ".js" else None
        )
        return responses.FileResponse(
            path,
            media_type=media_type,
            headers={"Cache-Control": "no-store"},
        )

    for route in _STATIC_FILES:
        application.add_api_route(route, serve_static, methods=["GET"])
    return application


app = create_app()
