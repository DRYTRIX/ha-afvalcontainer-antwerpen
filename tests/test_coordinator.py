"""Coordinator: session keep-alive, SSO retry, reauth, backoff and events."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.afvalcontainer_antwerpen.const import (
    CONF_COOKIES,
    CONF_INERTIA_VERSION,
    DOMAIN,
    EVENT_NEW_EMPTYING,
    EVENT_NEW_TOPUP,
)
from tests.conftest import FROZEN_NOW
from tests.fake_site import SESSION, VERSION, FakeSite


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, minutes: int = 31) -> None:
    """Fire the next scheduled poll (timers are relative to the loop clock)."""
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


def _reauth_flows(hass: HomeAssistant) -> list[dict[str, Any]]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"]["source"] == SOURCE_REAUTH
    ]


async def test_setup_persists_renewed_session(
    hass: HomeAssistant,
    site: FakeSite,
    setup_integration: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    entry = setup_integration
    assert entry.state is ConfigEntryState.LOADED
    first = entry.data[CONF_COOKIES][SESSION]
    assert first in site.valid_sessions
    assert entry.data[CONF_INERTIA_VERSION] == VERSION

    await _tick(hass, freezer)
    second = entry.data[CONF_COOKIES][SESSION]
    assert second != first
    # second poll used the Inertia JSON endpoint with the stored version
    saldo = [r for r in site.requests if r[1] == "/mijn-containers/saldo"]
    assert saldo[-1][2] is True


async def test_no_entry_write_when_cookies_unchanged(
    hass: HomeAssistant,
    site: FakeSite,
    setup_integration: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    site.renew = False
    with patch.object(
        hass.config_entries,
        "async_update_entry",
        wraps=hass.config_entries.async_update_entry,
    ) as update:
        await _tick(hass, freezer)  # jar unchanged since the last write
        await _tick(hass, freezer)
    assert update.call_count == 0


async def test_restart_resumes_with_persisted_cookies(
    hass: HomeAssistant,
    site: FakeSite,
    setup_integration: MockConfigEntry,
) -> None:
    entry = setup_integration
    stored = entry.data[CONF_COOKIES][SESSION]
    site.valid_sessions = {stored}  # only the latest renewed cookie is valid
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_silent_sso_on_setup(
    hass: HomeAssistant,
    site: FakeSite,
    mock_site: None,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(FROZEN_NOW)
    site.valid_sso.add("kc")
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=config_entry.unique_id,
        title=config_entry.title,
        data={CONF_COOKIES: {SESSION: "expired", "KEYCLOAK_IDENTITY": "kc"}},
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.data[CONF_COOKIES][SESSION] in site.valid_sessions
    assert config_entry.data[CONF_COOKIES]["KEYCLOAK_IDENTITY"] == "kc"
    assert any("digipolis/individual/callback" in path for _, path, _ in site.requests)


async def test_auth_failure_on_setup_starts_reauth(
    hass: HomeAssistant,
    mock_site: None,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(FROZEN_NOW)
    entry = MockConfigEntry(domain=DOMAIN, unique_id="x", data={CONF_COOKIES: {SESSION: "gone"}})
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert len(_reauth_flows(hass)) == 1


async def test_session_dies_later_starts_reauth(
    hass: HomeAssistant,
    site: FakeSite,
    setup_integration: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    site.valid_sessions.clear()
    await _tick(hass, freezer)
    flows = _reauth_flows(hass)
    assert len(flows) == 1
    assert flows[0]["context"]["entry_id"] == setup_integration.entry_id
    # SSO was tried once before giving up
    assert any(path.endswith("/openid-connect/auth") for _, path, _ in site.requests)


async def test_session_loss_is_logged_with_timing(
    hass: HomeAssistant,
    site: FakeSite,
    setup_integration: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _tick(hass, freezer)  # one more successful poll, 31 min after setup
    site.valid_sessions.clear()
    await _tick(hass, freezer)
    message = next(r.getMessage() for r in caplog.records if "no longer valid" in r.getMessage())
    assert "silent SSO failed" in message
    assert "Last successful update: 0h 31m ago" in message
    assert "Login data pasted: 1h 02m ago" in message
    assert setup_integration.runtime_data.last_auth_failure


async def test_server_error_backoff_and_recovery(
    hass: HomeAssistant,
    site: FakeSite,
    setup_integration: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    coordinator = setup_integration.runtime_data
    site.fail_with = 503
    await _tick(hass, freezer)
    assert coordinator.last_update_success is False
    assert coordinator.update_interval == timedelta(minutes=60)
    state = hass.states.get("sensor.afvalcontainer_antwerpen_jan_balance")
    assert state.state == "unavailable"

    # The site recovers within the 2 h session lifetime: the session is still valid.
    site.fail_with = None
    await _tick(hass, freezer, 61)
    assert coordinator.last_update_success is True, coordinator.last_exception
    assert coordinator.update_interval == timedelta(minutes=30)
    assert not _reauth_flows(hass)


async def test_server_error_backoff_is_capped(
    hass: HomeAssistant,
    site: FakeSite,
    setup_integration: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    coordinator = setup_integration.runtime_data
    site.fail_with = 500
    intervals = []
    for minutes in (31, 61, 91, 91):
        await _tick(hass, freezer, minutes)
        intervals.append(coordinator.update_interval)
    assert intervals == [timedelta(minutes=m) for m in (60, 90, 90, 90)]
    assert not _reauth_flows(hass)  # server errors never trigger reauth


async def test_unload_and_remove(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    entry = setup_integration
    key = f"{DOMAIN}.{entry.entry_id}.seen"
    await hass.async_block_till_done()
    assert key in hass_storage
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #
NEW_EMPTYING = {
    "id": "emptying-228590000",
    "date": "19/11/2026",
    "date_sort_value": "2026-11-19",
    "service": "Lediging",
    "location": "Voorbeeldstraat 1, 2000 Antwerpen",
    "weight": "9,1 kg",
    "amount": "1,77 euro",
    "containerNumber": "0040000000000002",
    "orderId": "-",
}
NEW_TOPUP = {
    "id": "110599999",
    "date": "19/11/2026",
    "date_sort_value": "2026-11-19",
    "service": "Provisie",
    "location": "-",
    "weight": "-",
    "amount": "25,00 euro",
    "containerNumber": "-",
    "orderId": "ASTD0000001",
}


def _add_rows(site: FakeSite, *rows: dict[str, Any]) -> None:
    props = site.balance_page["props"]
    site.balance_page = {
        **site.balance_page,
        "props": {**props, "historyRows": [*rows, *props["historyRows"]]},
    }


async def test_events_fire_once_and_not_for_backlog(
    hass: HomeAssistant,
    site: FakeSite,
    mock_site: None,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    emptyings = async_capture_events(hass, EVENT_NEW_EMPTYING)
    topups = async_capture_events(hass, EVENT_NEW_TOPUP)
    freezer.move_to(FROZEN_NOW)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert emptyings == []  # backlog is not announced on first setup
    assert topups == []

    _add_rows(site, NEW_EMPTYING, NEW_TOPUP)
    await _tick(hass, freezer)
    assert len(emptyings) == 1
    assert len(topups) == 1
    data = emptyings[0].data
    [device] = dr.async_entries_for_config_entry(dr.async_get(hass), config_entry.entry_id)
    assert data["device_id"] == device.id
    assert data["id"] == "emptying-228590000"
    assert data["date"] == "2026-11-19"
    assert data["weight_kg"] == 9.1
    assert data["cost_eur"] == 1.77
    assert data["container"] == "0040000000000002"
    assert data["service"] == "emptying"
    assert topups[0].data["amount_eur"] == 25.0

    await _tick(hass, freezer)
    assert len(emptyings) == 1  # not re-fired on the next poll

    # A restart must not re-fire either (seen IDs are persisted) ...
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(emptyings) == 1

    # ... but rows that appeared while HA was down are announced after restart.
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    _add_rows(site, {**NEW_EMPTYING, "id": "emptying-228590001"})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(emptyings) == 2


@pytest.mark.usefixtures("setup_integration")
async def test_refresh_button(hass: HomeAssistant, site: FakeSite) -> None:
    before = len(site.requests)
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.afvalcontainer_antwerpen_jan_refresh"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert len(site.requests) > before
