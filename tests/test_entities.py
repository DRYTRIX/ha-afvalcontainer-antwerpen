"""Entity, device trigger and diagnostics tests."""

from __future__ import annotations

import json

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components import automation
from homeassistant.components.device_automation import DeviceAutomationType
from homeassistant.components.sensor import ATTR_LAST_RESET, ATTR_STATE_CLASS
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_get_device_automations,
    async_mock_service,
)

from custom_components.afvalcontainer_antwerpen.const import (
    CONF_LOW_BALANCE_THRESHOLD,
    CONF_SCAN_INTERVAL,
    DOMAIN,
    EVENT_NEW_EMPTYING,
    EVENT_NEW_TOPUP,
)
from custom_components.afvalcontainer_antwerpen.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.conftest import FROZEN_NOW
from tests.fake_site import FakeSite

P = "sensor.afvalcontainer_antwerpen_jan_"


def _device(hass: HomeAssistant, entry: MockConfigEntry) -> dr.DeviceEntry:
    [device] = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    return device


@pytest.mark.usefixtures("setup_integration")
async def test_sensor_states(hass: HomeAssistant) -> None:
    balance = hass.states.get(f"{P}balance")
    assert balance.state == "26.94"
    assert balance.attributes[ATTR_DEVICE_CLASS] == "monetary"
    assert balance.attributes[ATTR_STATE_CLASS] == "total"
    assert balance.attributes[ATTR_UNIT_OF_MEASUREMENT] == "€"
    assert balance.attributes["invoices_url"] == "https://winkelmand.antwerpen.be/betalingen"

    assert hass.states.get(f"{P}minimum_balance").state == "5.0"

    # Test HA runs in US/Pacific: local midnight of 2026-11-18 is 08:00 UTC.
    last = hass.states.get(f"{P}last_emptying")
    assert last.state == "2026-11-18T08:00:00+00:00"
    assert last.attributes["weight_kg"] == 8.5
    assert last.attributes["cost_eur"] == 1.65
    assert last.attributes["container"] == "Restafval 120 l …0001"

    assert hass.states.get(f"{P}last_emptying_weight").state == "8.5"
    assert hass.states.get(f"{P}last_emptying_cost").state == "1.65"

    year = hass.states.get(f"{P}weight_this_year")
    assert year.state == "26.9"  # 8.5 + 12.0 + 6.4; the 2025 row is excluded
    assert year.attributes[ATTR_STATE_CLASS] == "total"
    assert year.attributes[ATTR_LAST_RESET] == "2026-01-01T00:00:00-08:00"
    assert hass.states.get(f"{P}weight_this_month").state == "20.5"
    assert hass.states.get(f"{P}cost_this_year").state == "5.22"
    month = hass.states.get(f"{P}cost_this_month")
    assert month.state == "3.98"
    assert month.attributes[ATTR_LAST_RESET] == "2026-11-01T00:00:00-07:00"
    assert hass.states.get(f"{P}emptyings_this_year").state == "3"

    topup = hass.states.get(f"{P}last_top_up")
    assert topup.state == "2026-06-26T07:00:00+00:00"
    assert topup.attributes["amount_eur"] == 50.0

    history = hass.states.get(f"{P}history")
    assert history.state == "6"
    rows = history.attributes["rows"]
    assert rows[0] == {
        "date": "2026-11-18",
        "service": "emptying",
        "weight_kg": 8.5,
        "amount_eur": 1.65,
        "container": "0040000000000001",
        "location": "Voorbeeldstraat 1, 2000 Antwerpen",
    }

    assert hass.states.get("binary_sensor.afvalcontainer_antwerpen_jan_low_balance").state == "off"


@pytest.mark.usefixtures("setup_integration")
async def test_history_rows_attribute_not_recorded(hass: HomeAssistant) -> None:
    entity = hass.data["entity_components"]["sensor"].get_entity(f"{P}history")
    assert "rows" in entity._unrecorded_attributes


@pytest.mark.usefixtures("setup_integration")
async def test_per_container_sensors(hass: HomeAssistant) -> None:
    first = hass.states.get(f"{P}last_emptying_restafval_120_l_0001")
    assert first.state == "2026-11-18T08:00:00+00:00"
    assert first.attributes["friendly_name"].endswith("Last emptying Restafval 120 l …0001")
    second = hass.states.get(f"{P}weight_this_year_restafval_240_l_0002")
    assert second.state == "12.0"
    assert hass.states.get(f"{P}weight_this_year_restafval_120_l_0001").state == "14.9"


async def test_single_container_has_no_container_sensors(
    hass: HomeAssistant,
    site: FakeSite,
    mock_site: None,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    props = site.balance_page["props"]
    site.balance_page = {
        **site.balance_page,
        "props": {
            **props,
            "historyRows": [
                r for r in props["historyRows"] if r["containerNumber"] != "0040000000000002"
            ],
        },
    }
    freezer.move_to(FROZEN_NOW)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    entries = er.async_entries_for_config_entry(er.async_get(hass), config_entry.entry_id)
    keys = {e.translation_key for e in entries}
    assert "container_last_emptying" not in keys
    assert "last_emptying" in keys


@pytest.mark.parametrize(("threshold", "expected"), [(30.0, "on"), (10.0, "off"), (None, "off")])
async def test_low_balance_binary_sensor(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    threshold: float | None,
    expected: str,
) -> None:
    options = {CONF_SCAN_INTERVAL: 30}
    if threshold is not None:
        options[CONF_LOW_BALANCE_THRESHOLD] = threshold
    hass.config_entries.async_update_entry(setup_integration, options=options)
    assert await hass.config_entries.async_reload(setup_integration.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.afvalcontainer_antwerpen_jan_low_balance")
    assert state.state == expected
    assert state.attributes["threshold"] == (threshold if threshold is not None else 5.0)


async def test_device_info(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    device = _device(hass, setup_integration)
    assert device.manufacturer == "Stad Antwerpen"
    assert device.entry_type is dr.DeviceEntryType.SERVICE
    assert device.configuration_url == "https://afvalcontainer.antwerpen.be/mijn-containers/saldo"
    assert device.name == "Afvalcontainer Antwerpen (Jan)"


async def test_device_triggers(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    device = _device(hass, setup_integration)
    triggers = await async_get_device_automations(hass, DeviceAutomationType.TRIGGER, device.id)
    ours = {t["type"] for t in triggers if t["domain"] == DOMAIN}
    assert ours == {"new_emptying", "new_topup"}

    calls: list[ServiceCall] = async_mock_service(hass, "test", "automation")
    assert await async_setup_component(
        hass,
        automation.DOMAIN,
        {
            automation.DOMAIN: [
                {
                    "trigger": {
                        "platform": "device",
                        "domain": DOMAIN,
                        "device_id": device.id,
                        "type": trigger_type,
                    },
                    "action": {
                        "service": "test.automation",
                        "data_template": {
                            "type": trigger_type,
                            "kg": "{{ trigger.event.data.weight_kg }}",
                        },
                    },
                }
                for trigger_type in ("new_emptying", "new_topup")
            ]
        },
    )
    hass.bus.async_fire(EVENT_NEW_EMPTYING, {"device_id": device.id, "weight_kg": 9.1})
    hass.bus.async_fire(EVENT_NEW_EMPTYING, {"device_id": "other-device", "weight_kg": 1})
    hass.bus.async_fire(EVENT_NEW_TOPUP, {"device_id": device.id})
    await hass.async_block_till_done()
    assert [(c.data["type"], c.data["kg"]) for c in calls] == [
        ("new_emptying", 9.1),
        ("new_topup", ""),
    ]


async def test_diagnostics_redacted(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
) -> None:
    # Called directly: the HTTP test client's token predates the frozen clock.
    diagnostics = await async_get_config_entry_diagnostics(hass, setup_integration)
    diagnostics = json.loads(json.dumps(diagnostics, default=str))
    dumped = json.dumps(diagnostics)
    for secret in (
        "sess0",
        "jan.testpersoon",
        "Jan",
        "Voorbeeldstraat",
        "0040000000000001",
        "ASTD0000000",
        "+3240",
    ):
        assert secret not in dumped, secret
    assert diagnostics["cookie_names"] == ["XSRF-TOKEN", "afvalcontainers-antwerpen-session"]
    assert diagnostics["session"]["last_success"] is not None
    assert diagnostics["session"]["has_remember_cookie"] is False
    assert diagnostics["data"]["current_amount"] == 26.94
    assert diagnostics["data"]["containers"][0]["short_id"] == "0001"
    assert len(diagnostics["data"]["history"]) == 6
