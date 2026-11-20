"""Config, reauth, reconfigure and options flow tests."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.afvalcontainer_antwerpen.api import hash_account_id
from custom_components.afvalcontainer_antwerpen.const import (
    CONF_COOKIES,
    CONF_INERTIA_VERSION,
    CONF_LOGIN_DATA,
    CONF_LOW_BALANCE_THRESHOLD,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)
from tests.conftest import EMAIL
from tests.fake_site import SESSION, VERSION, FakeSite

pytestmark = pytest.mark.usefixtures("mock_site")


def curl(session: str) -> str:
    """Build a 'Copy as cURL' paste with extra noise cookies."""
    return (
        "curl 'https://afvalcontainer.antwerpen.be/mijn-containers/saldo' \\\n"
        "  -H 'accept: text/html' \\\n"
        f"  -b '_ga=GA1.1.1; {SESSION}={session}; XSRF-TOKEN=abc' \\\n"
        "  -H 'user-agent: Mozilla/5.0'"
    )


@pytest.fixture
def mock_setup_entry() -> AsyncMock:
    """Do not set up the entry after the flow."""
    with patch(
        "custom_components.afvalcontainer_antwerpen.async_setup_entry", return_value=True
    ) as mock:
        yield mock


async def test_user_flow_success(
    hass: HomeAssistant, site: FakeSite, mock_setup_entry: AsyncMock
) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "auth"
    assert "afvalcontainer.antwerpen.be" in result["description_placeholders"]["url"]

    pasted_session = site.new_session()
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOGIN_DATA: curl(pasted_session)}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Afvalcontainer Antwerpen (Jan)"
    entry = result["result"]
    assert entry.unique_id == hash_account_id(EMAIL)
    assert EMAIL not in entry.unique_id
    cookies = result["data"][CONF_COOKIES]
    assert set(cookies) == {SESSION, "XSRF-TOKEN"}  # _ga stripped
    assert cookies[SESSION] != pasted_session  # renewed cookie stored
    assert cookies[SESSION] in site.valid_sessions
    assert result["data"][CONF_INERTIA_VERSION] == VERSION
    assert "logged_in_at" in result["data"]
    assert result["options"] == {CONF_SCAN_INTERVAL: 30}
    assert len(mock_setup_entry.mock_calls) == 1


@pytest.mark.parametrize(
    ("prepare", "pasted", "error"),
    [
        (None, "this is not a cookie", "invalid_format"),
        (None, f"{SESSION}=expired-session", "invalid_auth"),
        ("fail", None, "cannot_connect"),
        ("crash", None, "unknown"),
        ("no_account", None, "unknown"),
    ],
)
async def test_user_flow_errors_and_recovery(
    hass: HomeAssistant,
    site: FakeSite,
    mock_setup_entry: AsyncMock,
    prepare: str | None,
    pasted: str | None,
    error: str,
) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    original_page = site.balance_page
    if prepare == "fail":
        site.fail_with = 502
    elif prepare == "no_account":
        site.balance_page = {
            **original_page,
            "props": {**original_page["props"], "auth": {"user": {"role": "x"}}},
        }
    patcher = patch(
        "custom_components.afvalcontainer_antwerpen.config_flow.AfvalcontainerClient.async_get_balance",
        side_effect=RuntimeError("boom"),
    )
    if prepare == "crash":
        patcher.start()
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOGIN_DATA: pasted or curl(site.new_session())}
    )
    if prepare == "crash":
        patcher.stop()
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    # The user fixes the problem and tries again in the same form.
    site.fail_with = None
    site.balance_page = original_page
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOGIN_DATA: curl(site.new_session())}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_duplicate(
    hass: HomeAssistant, site: FakeSite, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOGIN_DATA: curl(site.new_session())}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_flow(
    hass: HomeAssistant, site: FakeSite, config_entry: MockConfigEntry, mock_setup_entry: AsyncMock
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOGIN_DATA: f"{SESSION}=expired"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    fresh = site.new_session()
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOGIN_DATA: f"Cookie: {SESSION}={fresh}"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_COOKIES][SESSION] in site.valid_sessions
    assert config_entry.data[CONF_COOKIES][SESSION] != fresh


async def test_reauth_wrong_account(
    hass: HomeAssistant, site: FakeSite, config_entry: MockConfigEntry, mock_setup_entry: AsyncMock
) -> None:
    config_entry.add_to_hass(hass)
    props = site.balance_page["props"]
    site.balance_page = {
        **site.balance_page,
        "props": {**props, "auth": {"user": {"email": "someone.else@example.com"}}},
    }
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOGIN_DATA: f"{SESSION}={site.new_session()}"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"


async def test_reconfigure_flow(
    hass: HomeAssistant, site: FakeSite, config_entry: MockConfigEntry, mock_setup_entry: AsyncMock
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOGIN_DATA: f"{SESSION}={site.new_session()}"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_INERTIA_VERSION] == VERSION


async def test_options_flow(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    entry = setup_integration
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["description_placeholders"] == {"minimum": "5,00"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 15, CONF_LOW_BALANCE_THRESHOLD: 10.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == {CONF_SCAN_INTERVAL: 15, CONF_LOW_BALANCE_THRESHOLD: 10.0}
    # Options are applied by reloading the entry.
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.update_interval == timedelta(minutes=15)


async def test_options_flow_rejects_out_of_range(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    result = await hass.config_entries.options.async_init(setup_integration.entry_id)
    with pytest.raises(InvalidData, match="scan_interval"):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SCAN_INTERVAL: 5}
        )
