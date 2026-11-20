"""Async client and parsers for afvalcontainer.antwerpen.be.

This module deliberately has no Home Assistant imports so it can be split off
into a standalone library later. It only depends on aiohttp, yarl (an aiohttp
dependency) and the standard library.

The site is a Laravel + Inertia.js application. Every page embeds its data as
JSON in the ``data-page`` attribute of ``<div id="app">``; the same data is
served as plain JSON when the Inertia headers are sent with a current asset
version. There is no official API, so everything here parses defensively.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import html
from http.cookies import SimpleCookie
import ipaddress
import json
import logging
import re
from typing import Any, Final

import aiohttp
from yarl import URL

_LOGGER = logging.getLogger(__name__)

BASE_URL: Final = "https://afvalcontainer.antwerpen.be"
SSO_REALM_URL: Final = "https://authentication.antwerpen.be/auth/realms/a-profiel/"

BALANCE_PATH: Final = "/mijn-containers/saldo"
CONTAINERS_PATH: Final = "/mijn-containers/containers"
LOGIN_PATH: Final = "/login"
SSO_REDIRECT_PATH: Final = "/digipolis/individual/redirect"
BALANCE_URL: Final = f"{BASE_URL}{BALANCE_PATH}"

BALANCE_COMPONENT: Final = "survey/balance/Index"

SESSION_COOKIE: Final = "afvalcontainers-antwerpen-session"
XSRF_COOKIE: Final = "XSRF-TOKEN"
DGP_SSO_COOKIE: Final = "dgp.auth.ssokey"
KEYCLOAK_COOKIES: Final = (
    "KEYCLOAK_IDENTITY",
    "KEYCLOAK_IDENTITY_LEGACY",
    "KEYCLOAK_SESSION",
    "KEYCLOAK_SESSION_LEGACY",
    "AUTH_SESSION_ID",
    "AUTH_SESSION_ID_LEGACY",
)
APP_COOKIES: Final = (SESSION_COOKIE, XSRF_COOKIE)
SSO_COOKIES: Final = (DGP_SSO_COOKIE, *KEYCLOAK_COOKIES)
ALLOWED_COOKIES: Final = (*APP_COOKIES, *SSO_COOKIES)
# Laravel "remember me" cookie: remember_<guard>_<sha1>. Lets the site log the
# user back in after the 2 h session cookie expired, if the site issues it.
_REMEMBER_COOKIE_PATTERN: Final = r"remember_[a-z]+_[0-9a-f]{40}"
_REMEMBER_COOKIE_RE = re.compile(_REMEMBER_COOKIE_PATTERN)


def is_allowed_cookie(name: str) -> bool:
    """Return True for the cookies this integration keeps."""
    return name in ALLOWED_COOKIES or _REMEMBER_COOKIE_RE.fullmatch(name) is not None


def is_remember_cookie(name: str) -> bool:
    """Return True for a Laravel remember-me cookie."""
    return _REMEMBER_COOKIE_RE.fullmatch(name) is not None


USER_AGENT: Final = "Mozilla/5.0 (X11; Linux x86_64; rv:145.0) Gecko/20100101 Firefox/145.0"
DEFAULT_TIMEOUT: Final = 30.0

SERVICE_EMPTYING: Final = "emptying"
SERVICE_DELIVERY: Final = "delivery"
SERVICE_TOPUP: Final = "topup"
SERVICE_REFUND: Final = "refund"
SERVICE_COLLECTION: Final = "collection"
SERVICE_REPLACEMENT: Final = "replacement"
SERVICE_OTHER: Final = "other"

_SERVICE_MAP: Final = {
    "lediging": SERVICE_EMPTYING,
    "ledigingen": SERVICE_EMPTYING,
    "leveren": SERVICE_DELIVERY,
    "levering": SERVICE_DELIVERY,
    "provisie": SERVICE_TOPUP,
    "aanvulling": SERVICE_TOPUP,
    "opwaardering": SERVICE_TOPUP,
    "terugbetaling": SERVICE_REFUND,
    "ophalen": SERVICE_COLLECTION,
    "ophaling": SERVICE_COLLECTION,
    "vervangen": SERVICE_REPLACEMENT,
    "vervanging": SERVICE_REPLACEMENT,
}

_EMPTY_VALUES: Final = frozenset({"", "-", "\u2013", "\u2014", "n.v.t.", "nvt", "null", "none"})


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class AfvalcontainerError(Exception):
    """Base error for this client."""


class InvalidLoginDataError(AfvalcontainerError):
    """The pasted login data does not contain a usable cookie."""


class NotLoggedInError(AfvalcontainerError):
    """The session is not (or no longer) logged in."""


class CannotConnectError(AfvalcontainerError):
    """The site could not be reached."""


class ServerError(CannotConnectError):
    """The site answered with a 5xx error."""


class ParseError(AfvalcontainerError):
    """The page could not be understood."""


# --------------------------------------------------------------------------- #
# Login-data paste parsing
# --------------------------------------------------------------------------- #
_CMD_ESCAPE_RE = re.compile(r"\^(.)", re.DOTALL)
_PS_COOKIE_RE = re.compile(
    r"System\.Net\.Cookie\(\s*[\"']([^\"']+)[\"']\s*,\s*[\"']([^\"']*)[\"']", re.IGNORECASE
)
_ALLOWED_PAIR_RE = re.compile(
    r"(?<![\w.%-])("
    + "|".join(
        [_REMEMBER_COOKIE_PATTERN]
        + [re.escape(name) for name in sorted(ALLOWED_COOKIES, key=len, reverse=True)]
    )
    + r")=([^;,\s'\"\\]+)"
)


def parse_login_data(text: str) -> dict[str, str]:
    """Extract the cookies this integration needs from pasted login data.

    Accepts a raw ``Cookie:`` header value (with or without the prefix), a
    "Copy as cURL" command (bash, Windows cmd or PowerShell flavours), a
    Firefox "Copy request headers" block, a cookie-editor style JSON export
    or a Netscape ``cookies.txt``. Everything except :data:`ALLOWED_COOKIES`
    is dropped.

    Raises :class:`InvalidLoginDataError` when neither the app session cookie
    nor an SSO cookie is present.
    """
    text = (text or "").strip()
    found: dict[str, str] = {}
    if text:
        for extractor in (_from_json, _from_powershell, _from_netscape, _from_text):
            for name, value in extractor(text):
                if is_allowed_cookie(name) and value and name not in found:
                    found[name] = value
            if SESSION_COOKIE in found:
                break
    if SESSION_COOKIE not in found and not any(
        name in SSO_COOKIES or is_remember_cookie(name) for name in found
    ):
        raise InvalidLoginDataError("No afvalcontainer session cookie found in the pasted data")
    return found


def _from_json(text: str) -> Iterable[tuple[str, str]]:
    if text[0] not in "[{":
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if isinstance(data, dict):
        if isinstance(data.get("cookies"), list):
            data = data["cookies"]
        elif all(isinstance(v, str) for v in data.values()):
            return list(data.items())
        else:
            data = [data]
    pairs: list[tuple[str, str]] = []
    if not isinstance(data, list):
        return pairs
    for item in data:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name", item.get("Name"))
        value = item.get("value", item.get("Value"))
        domain = str(item.get("domain", item.get("Domain", "")) or "")
        if domain and not domain.lstrip(".").endswith("antwerpen.be"):
            continue
        if isinstance(name, str) and isinstance(value, str):
            pairs.append((name.strip(), value.strip()))
    return pairs


def _from_powershell(text: str) -> Iterable[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in _PS_COOKIE_RE.finditer(text)]


def _from_netscape(text: str) -> Iterable[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.removeprefix("#HttpOnly_")
        parts = line.split("\t")
        if len(parts) == 7 and "antwerpen.be" in parts[0]:
            pairs.append((parts[5].strip(), parts[6].strip()))
    return pairs


def _from_text(text: str) -> Iterable[tuple[str, str]]:
    """Scan free text (cookie header, cURL, header block) for known cookies."""
    if "^" in text:  # Windows cmd escaping: ^" ^% ^& ...
        text = _CMD_ESCAPE_RE.sub(r"\1", text)
    return [(m.group(1), m.group(2)) for m in _ALLOWED_PAIR_RE.finditer(text)]


# --------------------------------------------------------------------------- #
# Value parsing
# --------------------------------------------------------------------------- #
_NUMBER_RE = re.compile(
    r"(?P<sign>[-\u2212])?\s*(?:€|eur\b)?\s*(?P<sign2>[-\u2212])?\s*(?P<num>\d[\d.,\s\u202f]*\d|\d)",
    re.IGNORECASE,
)


def parse_dutch_number(value: Any) -> float | None:
    """Parse a Dutch formatted number such as ``"1,65 euro"`` or ``"1.234,5 kg"``.

    Returns ``None`` for empty markers like ``"-"`` or unparsable input.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).replace("\xa0", " ").strip()
    if text.lower() in _EMPTY_VALUES:
        return None
    match = _NUMBER_RE.search(text)
    if not match:
        return None
    num = re.sub(r"[\s\u202f]", "", match.group("num"))
    if "," in num:
        num = num.replace(".", "").replace(",", ".")
    elif num.count(".") == 1 and len(num.split(".")[1]) != 3:
        pass  # a lone dot with 1-2 (or 4+) decimals is a decimal point
    else:
        num = num.replace(".", "")
    try:
        result = float(num)
    except ValueError:
        return None
    if match.group("sign") or match.group("sign2"):
        result = -result
    return result


def parse_row_date(row: Mapping[str, Any]) -> date | None:
    """Return the date of a history row (ISO ``date_sort_value`` or ``dd/mm/yyyy``)."""
    iso = row.get("date_sort_value")
    if isinstance(iso, str) and len(iso) >= 10:
        try:
            return date.fromisoformat(iso[:10])
        except ValueError:
            pass
    raw = row.get("date")
    if isinstance(raw, str):
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y"):
            try:
                return datetime.strptime(raw.strip()[:10], fmt).date()
            except ValueError:
                continue
    return None


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in _EMPTY_VALUES else text


def classify_service(label: str | None, row_id: str = "") -> str:
    """Map the Dutch service label to a stable service key."""
    key = (label or "").strip().lower()
    if key in _SERVICE_MAP:
        return _SERVICE_MAP[key]
    if row_id.startswith("emptying-"):
        return SERVICE_EMPTYING
    return SERVICE_OTHER


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class HistoryRow:
    """One normalised row of the balance history."""

    id: str
    date: date | None
    service: str
    service_label: str | None
    weight_kg: float | None
    amount_eur: float | None
    container: str | None
    location: str | None
    order_id: str | None

    @property
    def cost_eur(self) -> float | None:
        """Cost of an emptying (always positive)."""
        if self.service != SERVICE_EMPTYING or self.amount_eur is None:
            return None
        return abs(self.amount_eur)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {
            "id": self.id,
            "date": self.date.isoformat() if self.date else None,
            "service": self.service,
            "service_label": self.service_label,
            "weight_kg": self.weight_kg,
            "amount_eur": self.amount_eur,
            "container": self.container,
            "location": self.location,
            "order_id": self.order_id,
        }


@dataclass(frozen=True, slots=True)
class AccountInfo:
    """Non-identifying account information."""

    account_id: str | None
    first_name: str | None


@dataclass(slots=True)
class ContainerInfo:
    """A container seen in the history, optionally enriched from the containers page."""

    number: str
    fraction: str | None = None
    volume: str | None = None

    @property
    def short_id(self) -> str:
        """Last 4 characters of the container number."""
        return self.number[-4:]

    @property
    def label(self) -> str:
        """Human readable label."""
        parts = [p for p in (self.fraction, self.volume) if p]
        return f"{' '.join(parts)} …{self.short_id}" if parts else f"…{self.short_id}"


@dataclass(slots=True)
class BalanceData:
    """Everything parsed from the balance page."""

    current_amount: float | None
    minimum_amount: float | None
    invoices_url: str | None
    features: dict[str, Any]
    history: list[HistoryRow]
    account: AccountInfo
    containers: dict[str, ContainerInfo] = field(default_factory=dict)

    def rows_for(self, service: str) -> list[HistoryRow]:
        """Rows of one service type, newest first."""
        return [row for row in self.history if row.service == service]


# --------------------------------------------------------------------------- #
# Page parsing
# --------------------------------------------------------------------------- #
_APP_DIV_RE = re.compile(r"<div\b[^>]*\bid=[\"']app[\"'][^>]*>", re.IGNORECASE)
_DATA_PAGE_ATTR_RE = re.compile(r"\bdata-page=\"([^\"]*)\"|\bdata-page='([^']*)'", re.IGNORECASE)
_SCRIPT_PAGE_RE = re.compile(
    r"<script\b[^>]*\bdata-page\b[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL
)


def parse_inertia_html(text: str) -> dict[str, Any]:
    """Extract the Inertia page object from an HTML document."""
    candidates: list[str] = []
    if (div := _APP_DIV_RE.search(text)) and (attr := _DATA_PAGE_ATTR_RE.search(div.group(0))):
        candidates.append(attr.group(1) or attr.group(2) or "")
    candidates.extend(m.group(1) or m.group(2) or "" for m in _DATA_PAGE_ATTR_RE.finditer(text))
    candidates.extend(m.group(1) for m in _SCRIPT_PAGE_RE.finditer(text))
    for raw in candidates:
        try:
            page = json.loads(html.unescape(raw))
        except ValueError:
            continue
        if isinstance(page, dict) and "component" in page:
            return page
    raise ParseError("No Inertia page data found in HTML")


def hash_account_id(value: str) -> str:
    """Return a stable, non-reversible identifier for an account key."""
    return hashlib.sha256(f"afvalcontainer_antwerpen:{value.strip().lower()}".encode()).hexdigest()[
        :32
    ]


def parse_account(props: Mapping[str, Any]) -> AccountInfo:
    """Extract a hashed account id and the first name from ``props.auth.user``."""
    auth = props.get("auth")
    user = auth.get("user") if isinstance(auth, Mapping) else None
    if not isinstance(user, Mapping):
        return AccountInfo(None, None)
    account_id = None
    for key in ("email", "id", "uuid", "rrn"):
        value = user.get(key)
        if value not in (None, ""):
            account_id = hash_account_id(str(value))
            break
    first_name = None
    for key in ("first_name", "firstName", "firstname", "given_name", "givenName"):
        if isinstance(user.get(key), str) and user[key].strip():
            first_name = user[key].strip()
            break
    if first_name is None and isinstance(user.get("name"), str) and user["name"].strip():
        first_name = user["name"].strip().split()[0]
    return AccountInfo(account_id, first_name)


def normalise_row(row: Mapping[str, Any]) -> HistoryRow:
    """Normalise one raw history row."""
    raw_id = _clean_str(row.get("id"))
    label = _clean_str(row.get("service"))
    if raw_id is None:
        digest = hashlib.sha1(  # noqa: S324 - not used for security
            json.dumps(
                [row.get(k) for k in ("date_sort_value", "date", "service", "amount", "weight")],
                default=str,
            ).encode()
        ).hexdigest()[:12]
        raw_id = f"row-{digest}"
    return HistoryRow(
        id=raw_id,
        date=parse_row_date(row),
        service=classify_service(label, raw_id),
        service_label=label,
        weight_kg=parse_dutch_number(row.get("weight")),
        amount_eur=parse_dutch_number(row.get("amount")),
        container=_clean_str(row.get("containerNumber", row.get("container_number"))),
        location=_clean_str(row.get("location")),
        order_id=_clean_str(row.get("orderId", row.get("order_id"))),
    )


def parse_balance_page(page: Mapping[str, Any]) -> BalanceData:
    """Parse the props of the ``survey/balance/Index`` page."""
    props = page.get("props")
    if not isinstance(props, Mapping):
        raise ParseError("Page has no props")
    balance = props.get("balance") if isinstance(props.get("balance"), Mapping) else {}
    rows: list[HistoryRow] = []
    raw_rows = props.get("historyRows")
    if isinstance(raw_rows, list):
        for raw in raw_rows:
            if not isinstance(raw, Mapping):
                continue
            try:
                rows.append(normalise_row(raw))
            except Exception:
                _LOGGER.debug("Skipping unparsable history row", exc_info=True)
    rows.sort(key=lambda r: r.date or date.min, reverse=True)
    containers: dict[str, ContainerInfo] = {}
    for row in rows:
        if row.container and row.container not in containers:
            containers[row.container] = ContainerInfo(row.container)
    invoices = props.get("invoicesUrl")
    features = props.get("features")
    return BalanceData(
        current_amount=parse_dutch_number(balance.get("current_amount")),
        minimum_amount=parse_dutch_number(balance.get("minimum_amount")),
        invoices_url=invoices
        if isinstance(invoices, str) and invoices.startswith("http")
        else None,
        features=dict(features) if isinstance(features, Mapping) else {},
        history=rows,
        account=parse_account(props),
        containers=containers,
    )


_FRACTION_KEYS: Final = ("fraction_label", "fraction", "fractionLabel", "waste_type", "type")
_VOLUME_KEYS: Final = ("volume", "capacity", "size", "liters", "litres")


def enrich_containers(containers: Mapping[str, ContainerInfo], page: Mapping[str, Any]) -> None:
    """Best effort: add fraction/volume from the containers page to known containers.

    The containers page layout is not documented, so this walks the props and
    looks for objects that mention a known container number.
    """
    if not containers:
        return

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(node, Mapping):
            values = {str(v) for v in node.values() if isinstance(v, str | int)}
            for number, info in containers.items():
                if number in values:
                    info.fraction = info.fraction or _first_scalar(node, _FRACTION_KEYS)
                    info.volume = info.volume or _first_scalar(node, _VOLUME_KEYS)
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for value in node:
                walk(value, depth + 1)

    walk(page.get("props"))


def _first_scalar(node: Mapping[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        value = node.get(key)
        if isinstance(value, Mapping):
            value = value.get("label") or value.get("name")
        if isinstance(value, str | int | float) and not isinstance(value, bool):
            text = str(value).strip()
            if text and text.lower() not in _EMPTY_VALUES:
                return text
    return None


# --------------------------------------------------------------------------- #
# HTTP client
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Endpoints:
    """Base URLs (overridable for tests)."""

    base_url: str = BASE_URL
    sso_url: str = SSO_REALM_URL


def _parent_domain(host: str | None) -> str | None:
    """Return the registrable parent domain (``antwerpen.be``) or None for IPs/short hosts."""
    if not host:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        labels = host.split(".")
        return ".".join(labels[-2:]) if len(labels) > 2 else None
    return None


class AfvalcontainerClient:
    """Minimal async client for the balance page."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        cookies: Mapping[str, str] | None = None,
        *,
        inertia_version: str | None = None,
        endpoints: Endpoints | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialise the client with a session that owns its own cookie jar."""
        self._session = session
        self._endpoints = endpoints or Endpoints()
        self._base = URL(self._endpoints.base_url)
        self._sso = URL(self._endpoints.sso_url)
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self.inertia_version = inertia_version
        # Outcome of the last silent SSO attempt: None, "succeeded" or "failed".
        self.last_sso_result: str | None = None
        if cookies:
            self.set_cookies(cookies)

    # -- cookies ---------------------------------------------------------- #
    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        """Seed the cookie jar with (whitelisted) cookies."""
        parent = _parent_domain(self._base.host)
        for name, value in cookies.items():
            if not is_allowed_cookie(name) or not value:
                continue
            morsels: SimpleCookie = SimpleCookie()
            morsels[name] = value
            if name in KEYCLOAK_COOKIES:
                morsels[name]["path"] = self._sso.path or "/"
                response_url = self._sso
            else:
                morsels[name]["path"] = "/"
                response_url = self._base
                if name == DGP_SSO_COOKIE and parent:
                    morsels[name]["domain"] = parent
            self._session.cookie_jar.update_cookies(morsels, response_url)

    def export_cookies(self) -> dict[str, str]:
        """Return the current (renewed) whitelisted cookies from the jar."""
        result: dict[str, str] = {}
        for url in (self._base.join(URL(BALANCE_PATH)), self._sso):
            for name, morsel in self._session.cookie_jar.filter_cookies(url).items():
                if is_allowed_cookie(name) and morsel.value:
                    result[name] = morsel.value
        return result

    # -- public API ------------------------------------------------------- #
    async def async_get_balance(self, *, allow_sso: bool = True) -> BalanceData:
        """Fetch and parse the balance page.

        On "not logged in" a silent SSO re-login is tried once (when allowed)
        before :class:`NotLoggedInError` is raised.
        """
        try:
            page = await self._async_get_page(BALANCE_PATH, BALANCE_COMPONENT)
        except NotLoggedInError:
            if not allow_sso:
                raise
            _LOGGER.debug("Session not logged in, trying silent SSO re-login")
            if not await self.async_try_sso():
                self.last_sso_result = "failed"
                raise
            self.last_sso_result = "succeeded"
            _LOGGER.info("Session expired; logged in again through the A-profiel SSO")
            page = await self._async_get_page(BALANCE_PATH, BALANCE_COMPONENT)
        return parse_balance_page(page)

    async def async_get_containers_page(self) -> dict[str, Any]:
        """Fetch the containers page (used for optional metadata)."""
        return await self._async_get_page(CONTAINERS_PATH, None)

    async def async_try_sso(self) -> bool:
        """Try to create a new app session through the A-profiel SSO without a password.

        Returns False when the flow ends on the SSO login page.
        """
        url = self._base.join(URL(SSO_REDIRECT_PATH))
        try:
            async with self._session.get(
                url,
                headers=self._headers(),
                allow_redirects=True,
                max_redirects=10,
                timeout=self._timeout,
            ) as resp:
                await resp.read()
                status, final = resp.status, resp.url
        except aiohttp.TooManyRedirects:
            return False
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnectError(str(err)) from err
        if status >= 500:
            raise ServerError(f"SSO returned HTTP {status}")
        on_sso_page = "/protocol/openid-connect/" in final.path or "/login-actions/" in final.path
        if on_sso_page or final.path.rstrip("/") == LOGIN_PATH or status >= 400:
            _LOGGER.debug("Silent SSO re-login did not succeed (HTTP %s)", status)
            return False
        return True

    # -- internals -------------------------------------------------------- #
    def _headers(self, *, inertia: bool = False) -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "nl-BE,nl;q=0.9,en;q=0.5",
        }
        if inertia and self.inertia_version:
            headers.update(
                {
                    "Accept": "text/html, application/xhtml+xml",
                    "X-Inertia": "true",
                    "X-Inertia-Version": self.inertia_version,
                    "X-Requested-With": "XMLHttpRequest",
                }
            )
        return headers

    async def _async_get_page(self, path: str, component: str | None) -> dict[str, Any]:
        page: dict[str, Any] | None = None
        if self.inertia_version:
            page = await self._async_get_inertia_json(path)
        if page is None:
            page = await self._async_get_html(path)
        if isinstance(page.get("version"), str) and page["version"]:
            self.inertia_version = page["version"]
        return self._check_page(page, component)

    async def _async_get_inertia_json(self, path: str) -> dict[str, Any] | None:
        """Request the page as Inertia JSON. Returns None to fall back to HTML."""
        url = self._base.join(URL(path))
        try:
            async with self._session.get(
                url,
                headers=self._headers(inertia=True),
                allow_redirects=False,
                timeout=self._timeout,
            ) as resp:
                status = resp.status
                if status == 409:
                    _LOGGER.debug("Inertia version is stale, refetching HTML")
                    self.inertia_version = None
                    return None
                if status in (401, 403, 419):
                    raise NotLoggedInError(f"HTTP {status}")
                if 300 <= status < 400:
                    location = URL(resp.headers.get("Location", ""))
                    if self._is_login_url(self._base.join(location)):
                        raise NotLoggedInError("Redirected to login")
                    return None
                if status >= 500:
                    raise ServerError(f"HTTP {status}")
                if status != 200:
                    raise ParseError(f"Unexpected HTTP {status}")
                text = await resp.text()
                is_json = bool(resp.headers.get("X-Inertia")) or resp.content_type.endswith("json")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnectError(str(err)) from err
        if not is_json:
            return parse_inertia_html(text)
        try:
            page = json.loads(text)
        except ValueError as err:
            raise ParseError("Invalid Inertia JSON") from err
        if not isinstance(page, dict):
            raise ParseError("Inertia JSON is not an object")
        return page

    async def _async_get_html(self, path: str) -> dict[str, Any]:
        url = self._base.join(URL(path))
        try:
            async with self._session.get(
                url,
                headers=self._headers(),
                allow_redirects=True,
                max_redirects=10,
                timeout=self._timeout,
            ) as resp:
                status, final = resp.status, resp.url
                if status >= 500:
                    raise ServerError(f"HTTP {status}")
                if status in (401, 403, 419):
                    raise NotLoggedInError(f"HTTP {status}")
                if self._is_login_url(final) or final.path.rstrip("/") != path.rstrip("/"):
                    raise NotLoggedInError(f"Ended on {final.path} instead of {path}")
                if status != 200:
                    raise ParseError(f"Unexpected HTTP {status}")
                text = await resp.text()
        except aiohttp.TooManyRedirects as err:
            raise NotLoggedInError("Too many redirects") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnectError(str(err)) from err
        return parse_inertia_html(text)

    def _is_login_url(self, url: URL) -> bool:
        if url.host and self._sso.host and url.host == self._sso.host != self._base.host:
            return True
        path = url.path.rstrip("/")
        return path == LOGIN_PATH or "/protocol/openid-connect/" in path

    @staticmethod
    def _check_page(page: dict[str, Any], component: str | None) -> dict[str, Any]:
        props = page.get("props")
        auth = props.get("auth") if isinstance(props, Mapping) else None
        user = auth.get("user") if isinstance(auth, Mapping) else None
        actual = page.get("component")
        if str(actual).startswith("auth/"):
            raise NotLoggedInError(f"Got login component {actual}")
        if component is None or actual == component:
            return page
        if isinstance(auth, Mapping) and not user:
            raise NotLoggedInError(f"Got component {actual} without a logged-in user")
        raise ParseError(f"Unexpected page component {actual!r}, expected {component!r}")
