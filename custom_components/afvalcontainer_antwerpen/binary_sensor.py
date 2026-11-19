"""Binary sensors for Afvalcontainer Antwerpen."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_LOW_BALANCE_THRESHOLD
from .coordinator import AfvalcontainerConfigEntry, AfvalcontainerCoordinator
from .entity import AfvalcontainerEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AfvalcontainerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    async_add_entities([LowBalanceBinarySensor(entry.runtime_data)])


class LowBalanceBinarySensor(AfvalcontainerEntity, BinarySensorEntity):
    """On when the balance is below the threshold."""

    _attr_translation_key = "low_balance"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: AfvalcontainerCoordinator) -> None:
        """Initialise the binary sensor."""
        super().__init__(coordinator, "low_balance")

    @property
    def threshold(self) -> float | None:
        """Configured threshold, or the site's minimum balance."""
        configured = self.coordinator.config_entry.options.get(CONF_LOW_BALANCE_THRESHOLD)
        if configured is not None:
            return float(configured)
        return self.coordinator.data.minimum_amount

    @property
    def is_on(self) -> bool | None:
        """Return True when the balance is low."""
        balance = self.coordinator.data.current_amount
        threshold = self.threshold
        if balance is None or threshold is None:
            return None
        return balance < threshold

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the threshold in use."""
        return {"threshold": self.threshold, "balance": self.coordinator.data.current_amount}
