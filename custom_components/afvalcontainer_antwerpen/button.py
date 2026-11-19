"""Button to force an update."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AfvalcontainerConfigEntry, AfvalcontainerCoordinator
from .entity import AfvalcontainerEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AfvalcontainerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the refresh button."""
    async_add_entities([RefreshButton(entry.runtime_data)])


class RefreshButton(AfvalcontainerEntity, ButtonEntity):
    """Fetch the balance page now."""

    _attr_translation_key = "refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AfvalcontainerCoordinator) -> None:
        """Initialise the button."""
        super().__init__(coordinator, "refresh")

    async def async_press(self) -> None:
        """Request a refresh (debounced by the coordinator)."""
        await self.coordinator.async_request_refresh()
