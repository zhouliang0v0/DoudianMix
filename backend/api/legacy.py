"""Legacy text APIs retained for existing local pages."""

import fastapi

from backend import errors
from backend.api import models
from backend.providers import text

router = fastapi.APIRouter(
    dependencies=[fastapi.Depends(models.json_headers)],
    default_response_class=models.PublicJSONResponse,
)


@router.get("/api/config")
def public_config(request: fastapi.Request):
    """Expose text configuration metadata without SK."""
    return request.app.state.profiles.public_config()


@router.post("/api/config")
def save_config(body: dict, request: fastapi.Request):
    """Save text credentials only in the process-local profile store."""
    return request.app.state.profiles.save_config(body)


@router.post("/api/test")
async def test_connection(request: fastapi.Request):
    """Check the active text provider and return the legacy reply field."""
    state = request.app.state
    async with state.runner.model_lock:
        reply = await text.call_text(
            state.profiles.active_config(),
            "只回复：连接成功",
            state.http_client,
        )
    return {"reply": reply[:120]}


@router.post("/api/generate-copy")
async def generate_copy(body: dict, request: fastapi.Request):
    """Generate editable text from the supplied product facts."""
    source = str(body.get("source") or "").strip()
    if not source:
        raise errors.AppError("请先输入产品名称或已知卖点，再使用 AI 帮写")
    language = str(body.get("language") or "中文")[:20]
    platform = str(body.get("platform") or "电商平台")[:20]
    prompt = (
        "你是电商商品文案编辑。根据用户提供的真实资料，整理商品详情页信息。"
        "不要编造未提供的功能、材质、认证、参数或效果。"
        f"用{language}撰写，面向{platform}。严格按以下五行输出："
        "1.产品名称：...；2.核心卖点：...；3.适用人群：...；"
        "4.期望场景：...；5.具体参数：...。未知内容写“待补充”。"
        f"\n\n用户资料：\n{source[:4000]}"
    )
    state = request.app.state
    async with state.runner.model_lock:
        result = await text.call_text(
            state.profiles.active_config(), prompt, state.http_client
        )
    return {"text": result}
