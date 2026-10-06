"""Vision requests preserve the uploaded image bytes and protocol forms."""

import base64
import json
import re

import httpx

from backend import errors
from backend.providers import common


def parse_analysis(content: str, response_model: str) -> dict:
    """Parse facts JSON or retain plain text with legacy unknowns."""
    clean = re.sub(
        r"^```(?:json)?\s*", "", content.strip(), flags=re.IGNORECASE
    )
    clean = re.sub(r"\s*```$", "", clean)
    try:
        value = json.loads(clean)
    except ValueError:
        value = {
            "facts": [],
            "unknowns": ["商品规格、材质等尚待核实"],
            "text": content.strip(),
        }
    if not isinstance(value, dict):
        value = {}
    result = {}
    for key in ("facts", "unknowns"):
        items = value.get(key)
        result[key] = (
            [item for item in items if isinstance(item, str)][:30]
            if isinstance(items, list)
            else []
        )
    result["text"] = str(value.get("text") or content)[:10000]
    result["response_model"] = response_model
    return result


async def analyze_vision(
    profile: dict,
    images: list[tuple[bytes, str]],
    brief: str,
    settings: dict,
    client: httpx.AsyncClient,
) -> dict:
    """Send original images and user context, returning normalized analysis."""
    if not images:
        raise errors.AppError("请先上传商品原图", 400, "input")
    instruction = (
        "请结合商品原图和用户资料提取商品事实。不要推测无法看清的材质、尺寸、认证、性能。"
        '输出 JSON：{"facts":["已知事实"],"unknowns":["待补充项"],"text":"五行商品资料"}。'
        f'用户资料：{str(brief or "")[:4000]}。平台：{settings.get("platform") or ""}，'
        f'语言：{settings.get("language") or "中文"}。'
    )
    anthropic = common.is_anthropic(profile)
    content = [{"type": "text", "text": instruction}]
    for image_bytes, mime_type in images:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime_type,
                    "data": encoded,
                },
            }
            if anthropic
            else {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
            }
        )
    payload = {
        "model": profile["model"],
        "messages": [{"role": "user", "content": content}],
    }
    payload.update({"max_tokens": 1200} if anthropic else {"stream": False})
    data = await common.request_json(profile, payload, client)
    return parse_analysis(
        common.response_text(data, anthropic),
        data.get("model") or profile["model"],
    )
