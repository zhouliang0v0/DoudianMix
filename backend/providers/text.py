"""Text generation through OpenAI-compatible and Claude protocols."""

import httpx

from backend.providers import common


async def call_text(
    profile: dict, prompt: str, client: httpx.AsyncClient
) -> str:
    """Send a user prompt and return the provider's nonempty text."""
    anthropic = common.is_anthropic(profile)
    payload = {
        "model": profile["model"],
        "messages": [{"role": "user", "content": prompt}],
    }
    payload.update({"max_tokens": 600} if anthropic else {"stream": False})
    data = await common.request_json(profile, payload, client)
    return common.response_text(data, anthropic)
