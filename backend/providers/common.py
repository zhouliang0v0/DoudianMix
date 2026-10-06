"""Shared request and response handling without credential logging."""

import asyncio

import httpx

from backend import errors

REQUEST_TIMEOUT_SECONDS = 60.0


def is_anthropic(profile: dict) -> bool:
    """Whether a profile uses the native Claude Messages protocol."""
    return profile.get("protocol") == "anthropic"


async def request_json(
    profile: dict, payload: dict, client: httpx.AsyncClient
) -> dict:
    """Post a protocol payload with a 60 second timeout.

    Cancellation propagates to the caller. Provider and transport failures are
    exposed only through sanitized application errors.
    """
    anthropic = is_anthropic(profile)
    headers = {"Content-Type": "application/json"}
    secret = profile["apiKey"]
    if anthropic:
        headers.update({"x-api-key": secret, "anthropic-version": "2023-06-01"})
    else:
        headers["Authorization"] = f"Bearer {secret}"
    endpoint = "messages" if anthropic else "chat/completions"
    url = f'{profile["baseUrl"].rstrip("/")}/{endpoint}'
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
            response = await client.post(
                url, headers=headers, json=payload, timeout=60.0
            )
    except (TimeoutError, httpx.TimeoutException):
        raise errors.AppError("模型请求超时", 502, "timeout") from None
    except httpx.RequestError:
        raise errors.AppError("模型网络请求失败", 502, "network") from None
    return checked_json(response, secret)


def checked_json(response: httpx.Response, secret: str) -> dict:
    """Decode provider JSON and classify failures with credentials removed."""
    try:
        data = response.json()
    except ValueError:
        data = None
    if not response.is_success:
        message = "请求失败"
        if isinstance(data, dict):
            provider_error = data.get("error")
            message = (
                (
                    provider_error.get("message")
                    if isinstance(provider_error, dict)
                    else None
                )
                or data.get("message")
                or message
            )
        raise errors.classify_provider_error(
            response.status_code, str(message), secret
        )
    if not isinstance(data, dict):
        raise errors.AppError("模型返回无效 JSON", 502, "invalid_response")
    return data


def response_text(data: dict, anthropic: bool) -> str:
    """Extract native text blocks or compatible chat completion content."""
    content = data.get("content") if anthropic else None
    if not anthropic:
        choices = data.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        content = "\n".join(
            item["text"]
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        )
    if not isinstance(content, str) or not content.strip():
        raise errors.AppError(
            "模型没有返回文本，请检查所填模型是否支持对话生成",
            502,
            "invalid_response",
        )
    return content.strip()
