"""Errors exposed through the local HTTP contract."""


class AppError(Exception):
    """An application failure safe to show to the user.

    Attributes:
        status_code: HTTP response status.
        code: Optional machine readable error code.
    """

    def __init__(
        self, message: str, status_code: int = 400, code: str | None = None
    ) -> None:
        """Initialize the public message, status, and optional code."""
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.provider_status: int | None = None


def classify_provider_error(status: int, message: str, secret: str) -> AppError:
    """Return a public provider error with the active credential removed."""
    safe_message = str(message or "请求失败")
    if secret:
        safe_message = safe_message.replace(secret, "[redacted]")
    if status in (401, 403):
        code = "auth"
    elif status == 404:
        code = "model"
    elif status == 429:
        code = "rate_limit"
    elif status in (408, 504):
        code = "timeout"
    elif status in (400, 422):
        markers = ("image", "vision", "multimodal", "unsupported", "不支持")
        code = (
            "capability"
            if any(marker in safe_message.lower() for marker in markers)
            else "request"
        )
    else:
        code = "provider"
    error = AppError(f"服务商返回 {status}：{safe_message[:240]}", 502, code)
    error.provider_status = status
    return error
