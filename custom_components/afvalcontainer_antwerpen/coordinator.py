"""Data update coordinator for Afvalcontainer Antwerpen."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    SERVICE_EMPTYING,
    SERVICE_TOPUP,
    AfvalcontainerClient,
    AfvalcontainerError,
    BalanceData,
    CannotConnectError,
    ContainerInfo,
    NotLoggedInError,
    ParseError,
    ServerError,
    enrich_containers,
)
from .const import (
    CONF_COOKIES,
    CONF_INERTIA_VERSION,
    CONF_LOGGED_IN_AT,
    CONF_SCAN_INTERVAL,
    CONTAINERS_REFRESH_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_NEW_EMPTYING,
    EVENT_NEW_TOPUP,
    LOGGER,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    STORAGE_VERSION,
)

type AfvalcontainerConfigEntry = ConfigEntry[AfvalcontainerCoordinator]

_EVENT_FOR_SERVICE = {SERVICE_EMPTYING: EVENT_NEW_EMPTYING, SERVICE_TOPUP: EVENT_NEW_TOPUP}


def seen_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Return the store that remembers which history rows were already seen."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.seen")


def scan_interval_from_options(options: dict[str, Any]) -> timedelta:
    """Return the configured poll interval, clamped to the allowed range."""
    minutes = int(options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
    return timedelta(minutes=max(MIN_SCAN_INTERVAL, min(MAX_SCAN_INTERVAL, minutes)))


def _ago(now: datetime, then: datetime | None) -> str:
    """Human readable age such as ``"9h 12m ago"``."""
    if then is None:
        return "unknown"
    minutes = int((now - then).total_seconds() // 60)
    return f"{minutes // 60}h {minutes % 60:02d}m ago"


class AfvalcontainerCoordinator(DataUpdateCoordinator[BalanceData]):
    """Polls the balance page, keeps the session alive and fires history events."""

    config_entry: AfvalcontainerConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: AfvalcontainerConfigEntry,
        client: AfvalcontainerClient,
    ) -> None:
        """Initialise the coordinator."""
        self._base_interval = scan_interval_from_options(dict(entry.options))
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=self._base_interval,
        )
        self.client = client
        self.device_id: str | None = None
        self._failures = 0
        self._store = seen_store(hass, entry.entry_id)
        self._seen: set[str] | None = None
        self._container_cache: dict[str, ContainerInfo] = {}
        self.last_success: datetime | None = None
        self.last_auth_failure: str | None = None
        self._containers_fetched: datetime | None = None

    async def _async_setup(self) -> None:
        """Load the IDs of history rows that were already seen."""
        stored = await self._store.async_load()
        if stored and isinstance(stored.get("seen_ids"), list):
            self._seen = set(stored["seen_ids"])

    async def _async_update_data(self) -> BalanceData:
        """Fetch the balance page."""
        try:
            data = await self.client.async_get_balance()
        except NotLoggedInError as err:
            self._log_session_lost(err)
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="session_expired"
            ) from err
        except ServerError as err:
            self._back_off()
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="server_error",
                translation_placeholders={"error": str(err)},
            ) from err
        except CannotConnectError as err:
            self._back_off()
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        except ParseError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="parse_error",
                translation_placeholders={"error": str(err)},
            ) from err

        self._reset_backoff()
        self.last_success = dt_util.utcnow()
        await self._async_enrich_containers(data)
        self._persist_session()
        await self._async_fire_new_row_events(data)
        return data

    # -- session diagnostics --------------------------------------------- #
    def logged_in_at(self) -> datetime | None:
        """When the login data was pasted (None for entries created before 0.1.2)."""
        value = self.config_entry.data.get(CONF_LOGGED_IN_AT)
        return dt_util.parse_datetime(value) if isinstance(value, str) else None

    def _log_session_lost(self, err: NotLoggedInError) -> None:
        """Log why and when the session ended, so the site's limits can be learned."""
        now = dt_util.utcnow()
        logged_in_at = self.logged_in_at()
        self.last_auth_failure = str(err)
        LOGGER.warning(
            "Session is no longer valid (%s; silent SSO %s). Last successful update: %s. "
            "Login data pasted: %s",
            err,
            self.client.last_sso_result or "not tried",
            _ago(now, self.last_success),
            _ago(now, logged_in_at),
        )

    # -- backoff ---------------------------------------------------------- #
    def _back_off(self) -> None:
        """Exponential backoff, capped so a recovered site still finds a live session."""
        self._failures += 1
        cap = max(self._base_interval, timedelta(minutes=MAX_SCAN_INTERVAL))
        self.update_interval = min(self._base_interval * (2 ** min(self._failures, 4)), cap)

    def _reset_backoff(self) -> None:
        self._failures = 0
        self.update_interval = self._base_interval

    # -- session persistence ---------------------------------------------- #
    def _persist_session(self) -> None:
        """Store renewed cookies in the config entry, only when they changed."""
        entry = self.config_entry
        cookies = self.client.export_cookies()
        version = self.client.inertia_version
        if not cookies:
            return
        if cookies == entry.data.get(CONF_COOKIES) and version == entry.data.get(
            CONF_INERTIA_VERSION
        ):
            return
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_COOKIES: cookies, CONF_INERTIA_VERSION: version}
        )

    # -- containers metadata ---------------------------------------------- #
    async def _async_enrich_containers(self, data: BalanceData) -> None:
        """Add fraction/volume metadata from the containers page (at most daily)."""
        now = dt_util.utcnow()
        if data.containers and (
            self._containers_fetched is None
            or now - self._containers_fetched >= CONTAINERS_REFRESH_INTERVAL
        ):
            self._containers_fetched = now
            try:
                pages = await self.client.async_get_container_pages()
            except AfvalcontainerError as err:
                LOGGER.debug("Containers page not available: %s", err)
            else:
                for page in pages:
                    enrich_containers(data.containers, page)
                self._container_cache = {
                    number: ContainerInfo(number, info.fraction, info.volume)
                    for number, info in data.containers.items()
                }
        for number, info in data.containers.items():
            if cached := self._container_cache.get(number):
                info.fraction = info.fraction or cached.fraction
                info.volume = info.volume or cached.volume

    # -- events ------------------------------------------------------------ #
    async def _async_fire_new_row_events(self, data: BalanceData) -> None:
        """Fire bus events for history rows that were not seen before."""
        ids = {row.id for row in data.history}
        if self._seen is None:
            # First setup: remember the backlog silently.
            self._seen = ids
            await self._async_save_seen()
            return
        new_rows = [row for row in reversed(data.history) if row.id not in self._seen]
        if not new_rows:
            return
        for row in new_rows:  # oldest first
            if event_type := _EVENT_FOR_SERVICE.get(row.service):
                self.hass.bus.async_fire(
                    event_type,
                    {
                        **row.as_dict(),
                        "cost_eur": row.cost_eur,
                        "balance_eur": data.current_amount,
                        "device_id": self.device_id,
                        "config_entry_id": self.config_entry.entry_id,
                    },
                )
        self._seen |= ids
        await self._async_save_seen()

    async def _async_save_seen(self) -> None:
        await self._store.async_save({"seen_ids": sorted(self._seen or ())})
