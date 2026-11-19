"""Base entity for Afvalcontainer Antwerpen."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONFIGURATION_URL, DOMAIN, MANUFACTURER
from .coordinator import AfvalcontainerCoordinator


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """Device info for the (single) service device of an account."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
        entry_type=DeviceEntryType.SERVICE,
        manufacturer=MANUFACTURER,
        model="Diftar",
        name=entry.title,
        configuration_url=CONFIGURATION_URL,
    )


class AfvalcontainerEntity(CoordinatorEntity[AfvalcontainerCoordinator]):
    """Common base for all entities of an account."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AfvalcontainerCoordinator, key: str) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry)
