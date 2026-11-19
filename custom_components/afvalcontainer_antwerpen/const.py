"""Constants for the Afvalcontainer Antwerpen integration."""

from __future__ import annotations

from datetime import timedelta
import logging
import os
from typing import Final

from .api import BALANCE_URL, BASE_URL, SSO_REALM_URL, Endpoints

DOMAIN: Final = "afvalcontainer_antwerpen"
LOGGER = logging.getLogger(__package__)

MANUFACTURER: Final = "Stad Antwerpen"
CONFIGURATION_URL: Final = BALANCE_URL

# Config entry data
CONF_COOKIES: Final = "cookies"
CONF_INERTIA_VERSION: Final = "inertia_version"
# Config flow field
CONF_LOGIN_DATA: Final = "login_data"
# Options
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_LOW_BALANCE_THRESHOLD: Final = "low_balance_threshold"

# Polling: must stay below the 2 h sliding session lifetime to keep it alive.
DEFAULT_SCAN_INTERVAL: Final = 30
MIN_SCAN_INTERVAL: Final = 10
MAX_SCAN_INTERVAL: Final = 90
CONTAINERS_REFRESH_INTERVAL: Final = timedelta(hours=24)

EVENT_NEW_EMPTYING: Final = f"{DOMAIN}_new_emptying"
EVENT_NEW_TOPUP: Final = f"{DOMAIN}_new_topup"

TRIGGER_NEW_EMPTYING: Final = "new_emptying"
TRIGGER_NEW_TOPUP: Final = "new_topup"
TRIGGER_EVENTS: Final = {
    TRIGGER_NEW_EMPTYING: EVENT_NEW_EMPTYING,
    TRIGGER_NEW_TOPUP: EVENT_NEW_TOPUP,
}

STORAGE_VERSION: Final = 1
HISTORY_ATTRIBUTE_ROWS: Final = 20

# Development only: point the integration at a fake site (see CONTRIBUTING.md).
ENV_BASE_URL: Final = "AFVALCONTAINER_ANTWERPEN_BASE_URL"
ENV_SSO_URL: Final = "AFVALCONTAINER_ANTWERPEN_SSO_URL"


def get_endpoints() -> Endpoints:
    """Return the site endpoints (overridable through env vars for development)."""
    return Endpoints(
        base_url=os.environ.get(ENV_BASE_URL, BASE_URL),
        sso_url=os.environ.get(ENV_SSO_URL, SSO_REALM_URL),
    )
