"""Diagnostics for Afvalcontainer Antwerpen (personal data redacted)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .api import SSO_COOKIES, is_remember_cookie
from .const import CONF_COOKIES
from .coordinator import AfvalcontainerConfigEntry

TO_REDACT = {
    CONF_COOKIES,
    "address",
    "container",
    "email",
    "first_name",
    "last_name",
    "location",
    "name",
    "number",
    "order_id",
    "phone",
    "title",
    "unique_id",
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _cookie_label(name: str) -> str:
    # The remember cookie's hash suffix is not secret, but keep the output short.
    return "remember_* (Laravel remember-me)" if is_remember_cookie(name) else name


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AfvalcontainerConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    cookie_names = sorted(entry.data.get(CONF_COOKIES, {}))
    diagnostics: dict[str, Any] = {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "cookie_names": [_cookie_label(name) for name in cookie_names],
        "update_interval": str(coordinator.update_interval),
        "last_update_success": coordinator.last_update_success,
        "session": {
            "logged_in_at": _iso(coordinator.logged_in_at()),
            "last_success": _iso(coordinator.last_success),
            "last_auth_failure": coordinator.last_auth_failure,
            "last_sso_result": coordinator.client.last_sso_result,
            "has_sso_cookies": any(name in SSO_COOKIES for name in cookie_names),
            "has_remember_cookie": any(is_remember_cookie(name) for name in cookie_names),
        },
    }
    if data is not None:
        diagnostics["data"] = async_redact_data(
            {
                "current_amount": data.current_amount,
                "minimum_amount": data.minimum_amount,
                "invoices_url": data.invoices_url,
                "features": data.features,
                "account_has_id": data.account.account_id is not None,
                "containers": [
                    {
                        "number": info.number,
                        "short_id": info.short_id,
                        "fraction": info.fraction,
                        "volume": info.volume,
                    }
                    for info in data.containers.values()
                ],
                "history": [row.as_dict() for row in data.history],
            },
            TO_REDACT,
        )
    return diagnostics
