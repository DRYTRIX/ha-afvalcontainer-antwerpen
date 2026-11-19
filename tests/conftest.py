"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import aiohttp
from aiohttp import web
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import pytest_socket

from custom_components.afvalcontainer_antwerpen.api import Endpoints, hash_account_id
from custom_components.afvalcontainer_antwerpen.const import (
    CONF_COOKIES,
    CONF_INERTIA_VERSION,
    CONF_SCAN_INTERVAL,
    DOMAIN,
    ENV_BASE_URL,
    ENV_SSO_URL,
)
from tests.fake_site import APP_URL, SESSION, SSO_URL, FakeSite, make_app

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading the custom integration in every test."""


@pytest.fixture
def site() -> FakeSite:
    """Fake site state."""
    return FakeSite()


@pytest.fixture
def endpoints() -> Endpoints:
    """Endpoints pointing at the fake site."""
    return Endpoints(base_url=APP_URL, sso_url=SSO_URL)


@pytest.fixture
def socket_path() -> Generator[str]:
    """Short unix socket path (sun_path is limited to ~100 chars)."""
    with tempfile.TemporaryDirectory(prefix="afv") as tmp:
        yield str(Path(tmp) / "site.sock")


@pytest.fixture
async def site_server(site: FakeSite, socket_path: str) -> AsyncGenerator[str]:
    """Serve the fake site on a unix socket (TCP sockets are blocked in HA tests)."""
    # The HA test plugin only allows 127.0.0.1; also allow connect() on unix sockets.
    pytest_socket.socket_allow_hosts(["127.0.0.1"], allow_unix_socket=True)
    runner = web.AppRunner(make_app(site), access_log=None)
    await runner.setup()
    await web.UnixSite(runner, socket_path).start()
    yield socket_path
    await runner.cleanup()


def make_session(socket_path: str) -> aiohttp.ClientSession:
    """Client session with its own cookie jar that talks to the fake site."""
    return aiohttp.ClientSession(
        connector=aiohttp.UnixConnector(path=socket_path),
        cookie_jar=aiohttp.CookieJar(),
    )


@pytest.fixture
async def http(site_server: str) -> AsyncGenerator[aiohttp.ClientSession]:
    """Client session for API tests."""
    session = make_session(site_server)
    yield session
    await session.close()


# --------------------------------------------------------------------------- #
# Home Assistant level fixtures
# --------------------------------------------------------------------------- #
FROZEN_NOW = "2026-11-19T12:00:00+00:00"
EMAIL = "jan.testpersoon@example.com"


@pytest.fixture
async def mock_site(site_server: str, monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[None]:
    """Route the integration (setup and config flow) to the fake site."""
    monkeypatch.setenv(ENV_BASE_URL, APP_URL)
    monkeypatch.setenv(ENV_SSO_URL, SSO_URL)
    # In production HA owns (and shares) the connector, so the config flow only
    # detach()es its session. Here each session gets its own unix connector, which
    # the fixture closes itself.
    sessions: list[aiohttp.ClientSession] = []
    connectors: list[aiohttp.BaseConnector] = []

    def _factory(hass: HomeAssistant, **kwargs: Any) -> aiohttp.ClientSession:
        session = make_session(site_server)
        sessions.append(session)
        connectors.append(session.connector)
        return session

    with patch("custom_components.afvalcontainer_antwerpen.create_session", side_effect=_factory):
        yield
    for session in sessions:
        if not session.closed:
            await session.close()
    for connector in connectors:
        if not connector.closed:
            await connector.close()


@pytest.fixture
def config_entry(site: FakeSite) -> MockConfigEntry:
    """Return a config entry with a valid session on the fake site."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Afvalcontainer Antwerpen (Jan)",
        unique_id=hash_account_id(EMAIL),
        data={CONF_COOKIES: {SESSION: site.new_session()}, CONF_INERTIA_VERSION: None},
        options={CONF_SCAN_INTERVAL: 30},
    )


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant,
    mock_site: None,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> MockConfigEntry:
    """Set up the integration with the fake site and a frozen clock."""
    freezer.move_to(FROZEN_NOW)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
