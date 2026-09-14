from __future__ import annotations

from app.core.errors import AppError
from app.modules.mailboxes.providers.base import EmailProvider
from app.modules.mailboxes.providers.gmail import GmailProvider

_providers: dict[str, EmailProvider] = {}


class ProviderRegistry:
    """Registry for resolving and configuring email providers."""

    @classmethod
    def get(cls, provider_name: str) -> EmailProvider:
        normalized = provider_name.strip().upper()
        if normalized in _providers:
            return _providers[normalized]

        if normalized == "GMAIL":
            provider = GmailProvider()
            _providers["GMAIL"] = provider
            return provider

        raise AppError(
            "unsupported_provider",
            f"Provider '{provider_name}' is not supported in this release",
            status_code=400,
        )

    @classmethod
    def register(cls, provider_name: str, provider: EmailProvider) -> None:
        """Register or override a provider implementation (e.g. mock/fake in tests)."""
        _providers[provider_name.strip().upper()] = provider

    @classmethod
    def reset(cls) -> None:
        """Clear registered overrides."""
        _providers.clear()
