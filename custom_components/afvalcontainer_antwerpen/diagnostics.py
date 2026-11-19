"""Diagnostics for Afvalcontainer Antwerpen (personal data redacted)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

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


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AfvalcontainerConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    diagnostics: dict[str, Any] = {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "cookie_names": sorted(entry.data.get(CONF_COOKIES, {})),
        "update_interval": str(coordinator.update_interval),
        "last_update_success": coordinator.last_update_success,
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
