"""The Afvalcontainer Antwerpen integration."""

from __future__ import annotations

import aiohttp
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import AfvalcontainerClient
from .const import CONF_COOKIES, CONF_INERTIA_VERSION, get_endpoints
from .coordinator import AfvalcontainerConfigEntry, AfvalcontainerCoordinator, seen_store
from .entity import device_info

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]


def create_session(hass: HomeAssistant, *, auto_cleanup: bool = True) -> aiohttp.ClientSession:
    """Create a dedicated client session with its own cookie jar.

    With ``auto_cleanup`` (the default) Home Assistant detaches the session when
    the config entry that created it unloads, or when Home Assistant stops.
    """
    return async_create_clientsession(
        hass, auto_cleanup=auto_cleanup, cookie_jar=aiohttp.CookieJar()
    )


async def async_setup_entry(hass: HomeAssistant, entry: AfvalcontainerConfigEntry) -> bool:
    """Set up Afvalcontainer Antwerpen from a config entry."""
    session = create_session(hass)  # detached automatically when the entry unloads
    client = AfvalcontainerClient(
        session,
        entry.data.get(CONF_COOKIES, {}),
        inertia_version=entry.data.get(CONF_INERTIA_VERSION),
        endpoints=get_endpoints(),
    )
    coordinator = AfvalcontainerCoordinator(hass, entry, client)
    # Create the device up front so events fired on the first refresh carry its id.
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **device_info(entry)
    )
    coordinator.device_id = device.id

    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AfvalcontainerConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: AfvalcontainerConfigEntry) -> None:
    """Remove persisted state when the entry is deleted."""
    await seen_store(hass, entry.entry_id).async_remove()
