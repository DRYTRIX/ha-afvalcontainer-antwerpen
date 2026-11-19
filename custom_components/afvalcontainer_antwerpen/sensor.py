"""Sensors for Afvalcontainer Antwerpen."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import CURRENCY_EURO, EntityCategory, UnitOfMass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import SERVICE_EMPTYING, SERVICE_TOPUP, BalanceData, HistoryRow
from .const import HISTORY_ATTRIBUTE_ROWS
from .coordinator import AfvalcontainerConfigEntry, AfvalcontainerCoordinator
from .entity import AfvalcontainerEntity

PARALLEL_UPDATES = 0

type StateValue = float | int | datetime | None


def _today() -> date:
    return dt_util.now().date()


def _midnight(day: date | None) -> datetime | None:
    return dt_util.start_of_local_day(day) if day else None


def _start_of_year() -> datetime:
    return dt_util.start_of_local_day(_today().replace(month=1, day=1))


def _start_of_month() -> datetime:
    return dt_util.start_of_local_day(_today().replace(day=1))


def emptyings(
    data: BalanceData,
    *,
    year: int | None = None,
    month: int | None = None,
    container: str | None = None,
) -> list[HistoryRow]:
    """Return emptying rows, newest first, optionally filtered."""
    rows = []
    for row in data.rows_for(SERVICE_EMPTYING):
        if container is not None and row.container != container:
            continue
        if year is not None and (row.date is None or row.date.year != year):
            continue
        if month is not None and (row.date is None or row.date.month != month):
            continue
        rows.append(row)
    return rows


def _sum(values: list[float | None]) -> float:
    return round(sum(v for v in values if v is not None), 2)


def _this_year(data: BalanceData, container: str | None = None) -> list[HistoryRow]:
    return emptyings(data, year=_today().year, container=container)


def _this_month(data: BalanceData) -> list[HistoryRow]:
    today = _today()
    return emptyings(data, year=today.year, month=today.month)


def _last(rows: list[HistoryRow]) -> HistoryRow | None:
    return rows[0] if rows else None


def _emptying_attrs(row: HistoryRow | None, data: BalanceData) -> dict[str, Any]:
    if row is None:
        return {}
    container = data.containers.get(row.container) if row.container else None
    return {
        "weight_kg": row.weight_kg,
        "cost_eur": row.cost_eur,
        "container": container.label if container else row.container,
    }


@dataclass(frozen=True, kw_only=True)
class AfvalcontainerSensorDescription(SensorEntityDescription):
    """Sensor description with value and attribute callbacks."""

    value_fn: Callable[[BalanceData], StateValue]
    attrs_fn: Callable[[BalanceData], dict[str, Any]] | None = None
    last_reset_fn: Callable[[], datetime] | None = None


SENSORS: tuple[AfvalcontainerSensorDescription, ...] = (
    AfvalcontainerSensorDescription(
        key="balance",
        translation_key="balance",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        value_fn=lambda d: d.current_amount,
        attrs_fn=lambda d: {"invoices_url": d.invoices_url},
    ),
    AfvalcontainerSensorDescription(
        key="minimum_balance",
        translation_key="minimum_balance",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.minimum_amount,
    ),
    AfvalcontainerSensorDescription(
        key="last_emptying",
        translation_key="last_emptying",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: _midnight(row.date) if (row := _last(emptyings(d))) else None,
        attrs_fn=lambda d: _emptying_attrs(_last(emptyings(d)), d),
    ),
    AfvalcontainerSensorDescription(
        key="last_emptying_weight",
        translation_key="last_emptying_weight",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        suggested_display_precision=1,
        value_fn=lambda d: row.weight_kg if (row := _last(emptyings(d))) else None,
    ),
    AfvalcontainerSensorDescription(
        key="last_emptying_cost",
        translation_key="last_emptying_cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        value_fn=lambda d: row.cost_eur if (row := _last(emptyings(d))) else None,
    ),
    AfvalcontainerSensorDescription(
        key="weight_this_year",
        translation_key="weight_this_year",
        device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        suggested_display_precision=1,
        value_fn=lambda d: _sum([r.weight_kg for r in _this_year(d)]),
        last_reset_fn=_start_of_year,
    ),
    AfvalcontainerSensorDescription(
        key="weight_this_month",
        translation_key="weight_this_month",
        device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        suggested_display_precision=1,
        value_fn=lambda d: _sum([r.weight_kg for r in _this_month(d)]),
        last_reset_fn=_start_of_month,
    ),
    AfvalcontainerSensorDescription(
        key="cost_this_year",
        translation_key="cost_this_year",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        value_fn=lambda d: _sum([r.cost_eur for r in _this_year(d)]),
        last_reset_fn=_start_of_year,
    ),
    AfvalcontainerSensorDescription(
        key="cost_this_month",
        translation_key="cost_this_month",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        value_fn=lambda d: _sum([r.cost_eur for r in _this_month(d)]),
        last_reset_fn=_start_of_month,
    ),
    AfvalcontainerSensorDescription(
        key="emptyings_this_year",
        translation_key="emptyings_this_year",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: len(_this_year(d)),
        last_reset_fn=_start_of_year,
    ),
    AfvalcontainerSensorDescription(
        key="last_topup",
        translation_key="last_topup",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: (
            _midnight(row.date) if (row := _last(d.rows_for(SERVICE_TOPUP))) else None
        ),
        attrs_fn=lambda d: (
            {"amount_eur": row.amount_eur} if (row := _last(d.rows_for(SERVICE_TOPUP))) else {}
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AfvalcontainerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            *(AfvalcontainerSensor(coordinator, description) for description in SENSORS),
            AfvalcontainerHistorySensor(coordinator),
        ]
    )

    known: set[str] = set()

    @callback
    def _add_container_sensors() -> None:
        containers = coordinator.data.containers if coordinator.data else {}
        if len(containers) < 2:
            return
        new = [number for number in containers if number not in known]
        if not new:
            return
        known.update(new)
        entities: list[SensorEntity] = []
        for number in new:
            entities.append(ContainerLastEmptyingSensor(coordinator, number))
            entities.append(ContainerWeightThisYearSensor(coordinator, number))
        async_add_entities(entities)

    _add_container_sensors()
    entry.async_on_unload(coordinator.async_add_listener(_add_container_sensors))


class AfvalcontainerSensor(AfvalcontainerEntity, SensorEntity):
    """A sensor computed from the balance page."""

    entity_description: AfvalcontainerSensorDescription

    def __init__(
        self,
        coordinator: AfvalcontainerCoordinator,
        description: AfvalcontainerSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateValue:
        """Return the state."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)

    @property
    def last_reset(self) -> datetime | None:
        """Start of the current period for period totals."""
        if self.entity_description.last_reset_fn is None:
            return None
        return self.entity_description.last_reset_fn()


class AfvalcontainerHistorySensor(AfvalcontainerEntity, SensorEntity):
    """Number of history rows, with the most recent rows as an attribute."""

    _attr_translation_key = "history"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"rows"})

    def __init__(self, coordinator: AfvalcontainerCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, "history")

    @property
    def native_value(self) -> int:
        """Return the number of history rows."""
        return len(self.coordinator.data.history)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the newest rows (kept small)."""
        rows = [
            {
                "date": row.date.isoformat() if row.date else None,
                "service": row.service,
                "weight_kg": row.weight_kg,
                "amount_eur": row.amount_eur,
                "container": row.container,
                "location": row.location,
            }
            for row in self.coordinator.data.history[:HISTORY_ATTRIBUTE_ROWS]
        ]
        return {"rows": rows}


class _ContainerSensor(AfvalcontainerEntity, SensorEntity):
    """Base for per-container sensors."""

    def __init__(self, coordinator: AfvalcontainerCoordinator, number: str, key: str) -> None:
        super().__init__(coordinator, f"{key}_{number}")
        self._number = number
        self._attr_translation_key = key
        info = coordinator.data.containers.get(number)
        self._attr_translation_placeholders = {
            "container": info.label if info else f"…{number[-4:]}"
        }

    @property
    def available(self) -> bool:
        """Unavailable when the container disappeared from the history."""
        return super().available and self._number in self.coordinator.data.containers


class ContainerLastEmptyingSensor(_ContainerSensor):
    """Last emptying of one container."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: AfvalcontainerCoordinator, number: str) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, number, "container_last_emptying")

    def _row(self) -> HistoryRow | None:
        return _last(emptyings(self.coordinator.data, container=self._number))

    @property
    def native_value(self) -> datetime | None:
        """Return the date of the last emptying."""
        row = self._row()
        return _midnight(row.date) if row else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return weight and cost."""
        return _emptying_attrs(self._row(), self.coordinator.data)


class ContainerWeightThisYearSensor(_ContainerSensor):
    """Weight collected from one container this year."""

    _attr_device_class = SensorDeviceClass.WEIGHT
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfMass.KILOGRAMS
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: AfvalcontainerCoordinator, number: str) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, number, "container_weight_this_year")

    @property
    def native_value(self) -> float:
        """Return the total weight this year."""
        return _sum([r.weight_kg for r in _this_year(self.coordinator.data, self._number)])

    @property
    def last_reset(self) -> datetime:
        """Start of the year."""
        return _start_of_year()
