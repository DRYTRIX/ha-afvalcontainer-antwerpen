"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import web
import pytest
import pytest_socket

from custom_components.afvalcontainer_antwerpen.api import Endpoints
from tests.fake_site import APP_URL, SSO_URL, FakeSite, make_app

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
    runner = web.AppRunner(make_app(site))
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
