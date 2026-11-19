"""Config flow for Afvalcontainer Antwerpen."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
)
import voluptuous as vol

from .api import (
    AfvalcontainerClient,
    BalanceData,
    CannotConnectError,
    InvalidLoginDataError,
    NotLoggedInError,
    parse_login_data,
)
from .const import (
    CONF_COOKIES,
    CONF_INERTIA_VERSION,
    CONF_LOGIN_DATA,
    CONF_LOW_BALANCE_THRESHOLD,
    CONF_SCAN_INTERVAL,
    CONFIGURATION_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    get_endpoints,
)
from .coordinator import AfvalcontainerConfigEntry

LOGIN_SCHEMA = vol.Schema(
    {vol.Required(CONF_LOGIN_DATA): TextSelector(TextSelectorConfig(multiline=True))}
)
PLACEHOLDERS = {"url": CONFIGURATION_URL}


@dataclass(slots=True)
class ValidatedLogin:
    """Result of a successful login-data validation."""

    cookies: dict[str, str]
    inertia_version: str | None
    data: BalanceData


async def async_validate_login_data(hass: HomeAssistant, text: str) -> ValidatedLogin:
    """Parse the pasted login data and fetch the balance page once."""
    # Imported here so tests can patch the session factory in one place.
    from . import create_session  # noqa: PLC0415

    cookies = parse_login_data(text)
    session = create_session(hass)
    try:
        client = AfvalcontainerClient(session, cookies, endpoints=get_endpoints())
        data = await client.async_get_balance()
        return ValidatedLogin(client.export_cookies() or cookies, client.inertia_version, data)
    finally:
        await session.close()


def entry_title(data: BalanceData) -> str:
    """Title for the config entry."""
    name = data.account.first_name
    return f"Afvalcontainer Antwerpen ({name})" if name else "Afvalcontainer Antwerpen"


class AfvalcontainerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: AfvalcontainerConfigEntry) -> OptionsFlowHandler:
        """Return the options flow."""
        return OptionsFlowHandler()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Start: there is only one way in, the paste step."""
        return await self.async_step_auth(user_input)

    async def async_step_auth(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the pasted login data."""
        return await self._async_login_step("auth", user_input)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauthentication after the session died."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for fresh login data."""
        return await self._async_login_step("reauth_confirm", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Replace the stored login data manually."""
        return await self._async_login_step("reconfigure", user_input)

    async def _async_login_step(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                login = await async_validate_login_data(self.hass, user_input[CONF_LOGIN_DATA])
            except InvalidLoginDataError:
                errors["base"] = "invalid_format"
            except NotLoggedInError:
                errors["base"] = "invalid_auth"
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except Exception:
                LOGGER.exception("Unexpected error while validating login data")
                errors["base"] = "unknown"
            else:
                if login.data.account.account_id is None:
                    LOGGER.error("Logged in, but the page contains no account identifier")
                    errors["base"] = "unknown"
                else:
                    return await self._async_finish(login)

        return self.async_show_form(
            step_id=step_id,
            data_schema=LOGIN_SCHEMA,
            errors=errors,
            description_placeholders=PLACEHOLDERS,
        )

    async def _async_finish(self, login: ValidatedLogin) -> ConfigFlowResult:
        await self.async_set_unique_id(login.data.account.account_id)
        data = {CONF_COOKIES: login.cookies, CONF_INERTIA_VERSION: login.inertia_version}
        if self.source in (SOURCE_REAUTH, SOURCE_RECONFIGURE):
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            entry = (
                self._get_reauth_entry()
                if self.source == SOURCE_REAUTH
                else self._get_reconfigure_entry()
            )
            return self.async_update_reload_and_abort(entry, data_updates=data)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=entry_title(login.data),
            data=data,
            options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
        )


class OptionsFlowHandler(OptionsFlowWithReload):
    """Options: poll interval and low-balance threshold."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        mode=NumberSelectorMode.SLIDER,
                        unit_of_measurement="min",
                    )
                ),
                vol.Optional(CONF_LOW_BALANCE_THRESHOLD): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=1000,
                        step=0.5,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="€",
                    )
                ),
            }
        )
        minimum = None
        coordinator = getattr(self.config_entry, "runtime_data", None)
        if coordinator is not None and coordinator.data is not None:
            minimum = coordinator.data.minimum_amount
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, self.config_entry.options),
            description_placeholders={
                "minimum": "-" if minimum is None else f"{minimum:.2f}".replace(".", ",")
            },
        )
