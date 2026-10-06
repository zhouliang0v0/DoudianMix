"""Process-local model roles; credentials never enter public responses."""

import threading
from urllib import parse

from backend import errors


class ProfileStore:
    """Keep independent vision and image credentials in process memory."""

    def __init__(self, catalog: list[dict]) -> None:
        """Initialize an empty store with the public provider presets."""
        self._catalog = {preset["id"]: preset for preset in catalog}
        self._roles: dict[str, dict] = {}
        self._profiles: dict[str, dict] = {}
        self._active_provider = "openai"
        self._lock = threading.RLock()

    def public_config(self) -> dict:
        """Return legacy text metadata without credentials."""
        with self._lock:
            return {
                "activeProvider": self._active_provider,
                "profiles": {
                    key: {
                        "baseUrl": value["baseUrl"],
                        "model": value["model"],
                        "hasKey": bool(value["apiKey"]),
                    }
                    for key, value in self._profiles.items()
                },
            }

    def save_config(self, body: dict) -> dict:
        """Validate and save an independent process-local text profile."""
        provider = body.get("provider")
        if not isinstance(provider, str) or provider not in self._catalog:
            raise errors.AppError("请选择有效的服务商")
        base_url = str(body.get("baseUrl") or "").strip()
        model = str(body.get("model") or "").strip()
        if not base_url or not model:
            raise errors.AppError("请填写接口地址和模型名称")
        try:
            parsed = parse.urlsplit(base_url)
            if not parsed.hostname or parsed.port == -1:
                raise ValueError()
        except ValueError as error:
            raise errors.AppError("接口地址格式不正确") from error
        if parsed.scheme != "https" and not (
            parsed.scheme == "http"
            and parsed.hostname in ("localhost", "127.0.0.1")
        ):
            raise errors.AppError("接口地址必须为 HTTPS，或本机 HTTP 地址")
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise errors.AppError("接口地址不能包含账户、查询参数或片段")
        with self._lock:
            old = self._profiles.get(provider, {})
            api_key = str(body.get("apiKey") or "").strip()
            if not api_key and old.get("baseUrl") == base_url:
                api_key = old.get("apiKey", "")
            self._profiles[provider] = {
                "baseUrl": base_url,
                "model": model,
                "apiKey": api_key,
                "protocol": self._catalog[provider].get("protocol", "openai"),
            }
            self._active_provider = provider
            return self.public_config()

    def active_config(self) -> dict:
        """Return a text request snapshot with server-only credentials."""
        with self._lock:
            profile = self._profiles.get(self._active_provider)
            if not profile or not profile["apiKey"]:
                raise errors.AppError(
                    "请先在模型设置中填写接口地址、模型名称和 SK"
                )
            return dict(profile)

    def save_role(self, body: dict) -> dict:
        """Save a validated role, resetting its capability test status.

        A blank key reuses credentials only for the same provider and URL.
        Changing either leaves the role without credentials until refilled.
        """
        with self._lock:
            return self._save_role(body)

    def _save_role(self, body: dict) -> dict:
        """Validate and replace a configuration while holding the lock."""
        role = body.get("role")
        if role not in ("vision", "image"):
            raise errors.AppError("模型角色无效")
        provider = body.get("provider")
        preset = next(
            (value for key, value in self._catalog.items() if key == provider),
            None,
        )
        if preset is None or (role == "image" and provider != "openai"):
            raise errors.AppError("该服务商暂不支持此角色")
        base_url = str(body.get("baseUrl") or "").strip()
        model = str(body.get("model") or "").strip()
        try:
            parsed = parse.urlsplit(base_url)
            valid_host = parsed.hostname and parsed.port != -1
        except ValueError as error:
            raise errors.AppError("接口地址格式不正确") from error
        if not valid_host:
            raise errors.AppError("接口地址格式不正确")
        if (
            not model
            or not (
                parsed.scheme == "https"
                or (
                    parsed.scheme == "http"
                    and parsed.hostname in ("localhost", "127.0.0.1")
                )
            )
            or parsed.username is not None
            or parsed.password is not None
            or "?" in base_url
            or "#" in base_url
            or any(char.isspace() for char in base_url)
        ):
            raise errors.AppError("模型 ID 或接口地址无效")
        old = self._roles.get(role, {})
        api_key = str(body.get("apiKey") or "").strip()
        if (
            not api_key
            and old.get("provider") == provider
            and old.get("baseUrl") == base_url
        ):
            api_key = old.get("apiKey", "")
        self._roles[role] = {
            "role": role,
            "provider": provider,
            "baseUrl": base_url,
            "model": model,
            "apiKey": api_key,
            "protocol": preset.get("protocol", "openai"),
            "tested": False,
        }
        return self.public_roles()

    def get_role(self, role: str) -> dict:
        """Return a server-only configuration or a credentials error."""
        profile = self._roles.get(role)
        if not profile or not profile["apiKey"] or not profile["model"]:
            label = "视觉理解" if role == "vision" else "图片生成"
            raise errors.AppError(
                f"请先配置{label}模型和 SK", code="credentials"
            )
        return profile

    def public_roles(self) -> dict:
        """Expose role metadata and key presence without secret values."""
        return {
            "roles": {
                role: {
                    **{
                        key: profile[key]
                        for key in (
                            "role",
                            "provider",
                            "baseUrl",
                            "model",
                            "tested",
                        )
                    },
                    "hasKey": bool(profile["apiKey"]),
                }
                for role, profile in self._roles.items()
            }
        }

    def mark_tested(
        self, role: str, tested_profile: dict | None = None
    ) -> None:
        """Mark success only if the exact tested configuration is still active.

        Args:
            role: Configured model role.
            tested_profile: Exact profile used by the provider request. Omit
                only for synchronous callers marking the current profile.
        """
        with self._lock:
            if (
                tested_profile is not None
                and self._roles.get(role) is not tested_profile
            ):
                return
            self.get_role(role)["tested"] = True
