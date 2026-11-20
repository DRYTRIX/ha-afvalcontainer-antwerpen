"""Tests for the HA-independent API client and parsers."""

from __future__ import annotations

from datetime import date
import html
import json

import aiohttp
import pytest

from custom_components.afvalcontainer_antwerpen.api import (
    SERVICE_DELIVERY,
    SERVICE_EMPTYING,
    SERVICE_OTHER,
    SERVICE_TOPUP,
    AfvalcontainerClient,
    Endpoints,
    InvalidLoginDataError,
    NotLoggedInError,
    ParseError,
    ServerError,
    enrich_containers,
    hash_account_id,
    parse_balance_page,
    parse_dutch_number,
    parse_inertia_html,
    parse_login_data,
)
from tests.fake_site import SESSION, VERSION, FakeSite, load_fixture

S = "eyJpdiI6IkFBQUEiLCJ2YWx1ZSI6IkJCQkIifQ%3D%3D"
X = "eyJpdiI6IlhYWFgiLCJ2YWx1ZSI6IllZWVkifQ%3D%3D"
EXPECTED = {SESSION: S, "XSRF-TOKEN": X}


# --------------------------------------------------------------------------- #
# Login data parsing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "pasted",
    [
        # raw header value, with and without prefix, extra cookies
        f"_ga=GA1.2.3; XSRF-TOKEN={X}; {SESSION}={S}; other=1",
        f"Cookie: XSRF-TOKEN={X}; {SESSION}={S}",
        f"cookie: {SESSION}={S};XSRF-TOKEN={X}",
        # Chrome / Edge "Copy as cURL (bash)" with -b
        (
            "curl 'https://afvalcontainer.antwerpen.be/mijn-containers/saldo' \\\n"
            "  -H 'accept: text/html,application/xhtml+xml' \\\n"
            f"  -b '_ga=GA1; XSRF-TOKEN={X}; {SESSION}={S}' \\\n"
            "  -H 'user-agent: Mozilla/5.0'"
        ),
        # Firefox "Copy as cURL" with -H 'Cookie: ...'
        (
            "curl 'https://afvalcontainer.antwerpen.be/mijn-containers/saldo' "
            "-H 'User-Agent: Mozilla/5.0' "
            f"-H 'Cookie: XSRF-TOKEN={X}; {SESSION}={S}' -H 'X-XSRF-TOKEN: zzz'"
        ),
        # --cookie with double quotes and ANSI-C quoting
        f'curl "https://afvalcontainer.antwerpen.be/" --cookie "XSRF-TOKEN={X}; {SESSION}={S}"',
        f"curl $'https://afvalcontainer.antwerpen.be/' -H $'cookie: XSRF-TOKEN={X}; {SESSION}={S}'",
        # Chrome "Copy as cURL (cmd)" with caret escaping
        (
            'curl ^"https://afvalcontainer.antwerpen.be/mijn-containers/saldo^" ^\n'
            '  -H ^"accept: text/html^" ^\n'
            '  -b ^"XSRF-TOKEN='
            + X.replace("%", "^%")
            + "; "
            + SESSION
            + "="
            + S.replace("%", "^%")
            + '^" ^\n  -H ^"user-agent: Mozilla/5.0^"'
        ),
        # Chrome "Copy as PowerShell"
        (
            "$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession\n"
            '$session.Cookies.Add((New-Object System.Net.Cookie("XSRF-TOKEN", "'
            + X
            + '", "/", "afvalcontainer.antwerpen.be")))\n'
            '$session.Cookies.Add((New-Object System.Net.Cookie("'
            + SESSION
            + '", "'
            + S
            + '", "/", "afvalcontainer.antwerpen.be")))\n'
            'Invoke-WebRequest -UseBasicParsing -Uri "https://afvalcontainer.antwerpen.be/" '
            "-WebSession $session"
        ),
        # PowerShell with a Cookie header instead of a WebSession
        (
            'Invoke-WebRequest -Uri "https://afvalcontainer.antwerpen.be/" -Headers @{\n'
            f'  "Cookie"="XSRF-TOKEN={X}; {SESSION}={S}"\n}}'
        ),
        # Firefox "Copy Request Headers"
        (
            "GET /mijn-containers/saldo HTTP/2\nHost: afvalcontainer.antwerpen.be\n"
            "User-Agent: Mozilla/5.0\nAccept: text/html\n"
            f"Cookie: XSRF-TOKEN={X}; {SESSION}={S}\nUpgrade-Insecure-Requests: 1\n"
        ),
        # Cookie-Editor JSON export (with a foreign cookie that must be ignored)
        json.dumps(
            [
                {
                    "domain": "afvalcontainer.antwerpen.be",
                    "name": SESSION,
                    "value": S,
                    "httpOnly": True,
                    "path": "/",
                },
                {"domain": "afvalcontainer.antwerpen.be", "name": "XSRF-TOKEN", "value": X},
                {"domain": ".example.com", "name": "tracker", "value": "1"},
            ],
            indent=2,
        ),
        # Netscape cookies.txt
        (
            "# Netscape HTTP Cookie File\n"
            f"#HttpOnly_afvalcontainer.antwerpen.be\tFALSE\t/\tTRUE\t0\t{SESSION}\t{S}\n"
            f"afvalcontainer.antwerpen.be\tFALSE\t/\tTRUE\t0\tXSRF-TOKEN\t{X}\n"
        ),
    ],
)
def test_parse_login_data_formats(pasted: str) -> None:
    assert parse_login_data(pasted) == EXPECTED


def test_parse_login_data_keeps_sso_cookies_only_from_whitelist() -> None:
    pasted = json.dumps(
        {
            "cookies": [
                {"name": "dgp.auth.ssokey", "value": "sso1", "domain": ".antwerpen.be"},
                {
                    "name": "KEYCLOAK_IDENTITY",
                    "value": "kc1",
                    "domain": "authentication.antwerpen.be",
                },
                {"name": "_ga", "value": "x", "domain": ".antwerpen.be"},
            ]
        }
    )
    assert parse_login_data(pasted) == {"dgp.auth.ssokey": "sso1", "KEYCLOAK_IDENTITY": "kc1"}


REMEMBER = "remember_web_0123456789abcdef0123456789abcdef01234567"


def test_parse_login_data_keeps_laravel_remember_cookie() -> None:
    pasted = f"Cookie: {REMEMBER}=rem1; afvalcontainers-antwerpen-session=s1; _ga=x"
    assert parse_login_data(pasted) == {REMEMBER: "rem1", "afvalcontainers-antwerpen-session": "s1"}
    # A remember cookie alone is enough to try logging in.
    assert parse_login_data(f"{REMEMBER}=rem1") == {REMEMBER: "rem1"}
    # Lookalikes are not kept.
    with pytest.raises(InvalidLoginDataError):
        parse_login_data("remember_web_short=rem1")


async def test_remember_cookie_round_trips_through_jar(endpoints: Endpoints) -> None:
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
        client = AfvalcontainerClient(session, {REMEMBER: "rem1"}, endpoints=endpoints)
        assert client.export_cookies() == {REMEMBER: "rem1"}


def test_parse_login_data_does_not_match_header_names() -> None:
    with pytest.raises(InvalidLoginDataError):
        parse_login_data("X-XSRF-TOKEN: abc\nmy-afvalcontainers-antwerpen-session=nope")


@pytest.mark.parametrize("pasted", ["", "   ", "hello world", "[1, 2]", "{bad json", "a=b; c=d"])
def test_parse_login_data_invalid(pasted: str) -> None:
    with pytest.raises(InvalidLoginDataError):
        parse_login_data(pasted)


# --------------------------------------------------------------------------- #
# Value parsing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,65 euro", 1.65),
        ("50,00 euro", 50.0),
        ("0,00 euro", 0.0),
        ("8,5 kg", 8.5),
        ("12 kg", 12.0),
        ("1.234,56 euro", 1234.56),
        ("1 234,5 kg", 1234.5),
        ("€ 3,20", 3.2),
        ("-1,65 euro", -1.65),
        ("€ -2,10", -2.1),
        ("\u22124,00 euro", -4.0),
        ("8.5 kg", 8.5),
        ("1.000 kg", 1000.0),
        (26.94, 26.94),
        (5, 5.0),
        ("-", None),
        ("", None),
        ("n.v.t.", None),
        (None, None),
        (True, None),
        ("geen", None),
    ],
)
def test_parse_dutch_number(raw: object, expected: float | None) -> None:
    assert parse_dutch_number(raw) == expected


# --------------------------------------------------------------------------- #
# Page parsing
# --------------------------------------------------------------------------- #
def test_parse_balance_page() -> None:
    data = parse_balance_page(load_fixture("balance_page.json"))
    assert data.current_amount == 26.94
    assert data.minimum_amount == 5.0
    assert data.invoices_url == "https://winkelmand.antwerpen.be/betalingen"
    assert data.account.first_name == "Jan"
    assert data.account.account_id == hash_account_id("jan.testpersoon@example.com")
    assert "example" not in data.account.account_id
    assert [row.id for row in data.history][:2] == ["emptying-228580003", "emptying-228580002"]
    last = data.history[0]
    assert last.date == date(2026, 11, 18)
    assert last.service == SERVICE_EMPTYING
    assert last.weight_kg == 8.5
    assert last.cost_eur == 1.65
    assert last.container == "0040000000000001"
    assert last.order_id is None
    services = {row.id: row.service for row in data.history}
    assert services["event-111105250"] == SERVICE_DELIVERY
    assert services["110586338"] == SERVICE_TOPUP
    assert set(data.containers) == {"0040000000000001", "0040000000000002"}
    assert data.containers["0040000000000002"].label == "…0002"


def test_parse_balance_page_defensive() -> None:
    page = {
        "component": "survey/balance/Index",
        "props": {
            "balance": {"current_amount": "-3,50", "new_field": 1},
            "historyRows": [
                {
                    "service": "Teleportatie",
                    "date": "02/03/2026",
                    "amount": "9,99 euro",
                    "brandNewColumn": {"x": 1},
                },
                {"id": "emptying-1", "date_sort_value": "not-a-date", "date": "garbage"},
                "not a row",
                None,
                {},
            ],
        },
    }
    data = parse_balance_page(page)
    assert data.current_amount == -3.5
    assert data.minimum_amount is None
    assert data.invoices_url is None
    assert data.account.account_id is None
    unknown = next(row for row in data.history if row.service_label == "Teleportatie")
    assert unknown.service == SERVICE_OTHER
    assert unknown.id.startswith("row-")
    assert unknown.date == date(2026, 3, 2)
    emptying = next(row for row in data.history if row.id == "emptying-1")
    assert emptying.service == SERVICE_EMPTYING
    assert emptying.date is None
    assert len(data.history) == 3


@pytest.mark.parametrize("rows", [[], None, "nope"])
def test_parse_balance_page_empty_history(rows: object) -> None:
    data = parse_balance_page({"props": {"balance": {"current_amount": 0}, "historyRows": rows}})
    assert data.history == []
    assert data.containers == {}
    assert data.current_amount == 0.0


def test_parse_balance_page_without_props() -> None:
    with pytest.raises(ParseError):
        parse_balance_page({"component": "x"})


def test_parse_inertia_html_variants() -> None:
    page = {"component": "survey/balance/Index", "version": "v1", "props": {"a": "<b>&'\""}}
    attr = html.escape(json.dumps(page))
    assert parse_inertia_html(f'<div class="x" id="app" data-page="{attr}"></div>') == page
    assert parse_inertia_html(f"<div data-page='{attr}' id='app'></div>") == page
    script = f'<script data-page="app" type="application/json">{json.dumps(page)}</script>'
    assert parse_inertia_html(script) == page
    with pytest.raises(ParseError):
        parse_inertia_html("<html><body>Maintenance</body></html>")


def test_enrich_containers() -> None:
    data = parse_balance_page(load_fixture("balance_page.json"))
    enrich_containers(data.containers, load_fixture("containers_page.json"))
    first = data.containers["0040000000000001"]
    assert first.fraction == "Restafval"
    assert first.volume == "120 l"
    assert first.label == "Restafval 120 l …0001"
    enrich_containers({}, {"props": {}})  # no crash


# --------------------------------------------------------------------------- #
# HTTP behaviour (against the fake site)
# --------------------------------------------------------------------------- #
async def test_fetch_html_then_inertia_json(
    http: aiohttp.ClientSession, site: FakeSite, endpoints: Endpoints
) -> None:
    session = site.new_session()
    client = AfvalcontainerClient(http, {SESSION: session}, endpoints=endpoints)
    data = await client.async_get_balance()
    assert data.current_amount == 26.94
    assert client.inertia_version == VERSION
    assert site.requests[-1][2] is False  # first request: HTML
    # the session cookie was renewed by the response
    renewed = client.export_cookies()
    assert renewed[SESSION] != session
    assert "XSRF-TOKEN" in renewed
    await client.async_get_balance()
    assert site.requests[-1][2] is True  # second request: Inertia JSON


async def test_stale_version_falls_back_to_html(
    http: aiohttp.ClientSession, site: FakeSite, endpoints: Endpoints
) -> None:
    client = AfvalcontainerClient(
        http, {SESSION: site.new_session()}, inertia_version="old", endpoints=endpoints
    )
    data = await client.async_get_balance()
    assert data.current_amount == 26.94
    assert [r[2] for r in site.requests] == [True, False]
    assert client.inertia_version == VERSION


@pytest.mark.parametrize("version", [None, VERSION])
async def test_not_logged_in_without_sso(
    http: aiohttp.ClientSession, site: FakeSite, endpoints: Endpoints, version: str | None
) -> None:
    client = AfvalcontainerClient(
        http, {SESSION: "expired"}, inertia_version=version, endpoints=endpoints
    )
    with pytest.raises(NotLoggedInError):
        await client.async_get_balance()
    # SSO was attempted and ended on the A-profiel login page
    assert any(path.endswith("/openid-connect/auth") for _, path, _ in site.requests)


async def test_silent_sso_relogin(
    http: aiohttp.ClientSession, site: FakeSite, endpoints: Endpoints
) -> None:
    site.valid_sso.add("kc-identity")
    client = AfvalcontainerClient(
        http, {SESSION: "expired", "KEYCLOAK_IDENTITY": "kc-identity"}, endpoints=endpoints
    )
    data = await client.async_get_balance()
    assert data.current_amount == 26.94
    cookies = client.export_cookies()
    assert cookies[SESSION] in site.valid_sessions
    assert cookies["KEYCLOAK_IDENTITY"] == "kc-identity"


async def test_silent_sso_with_parent_domain_cookie(
    http: aiohttp.ClientSession, site: FakeSite, endpoints: Endpoints
) -> None:
    site.valid_sso.add("dgp")
    client = AfvalcontainerClient(http, {"dgp.auth.ssokey": "dgp"}, endpoints=endpoints)
    data = await client.async_get_balance()
    assert data.account.first_name == "Jan"
    assert client.export_cookies()["dgp.auth.ssokey"] == "dgp"


async def test_no_sso_when_disabled(
    http: aiohttp.ClientSession, site: FakeSite, endpoints: Endpoints
) -> None:
    client = AfvalcontainerClient(http, {SESSION: "expired"}, endpoints=endpoints)
    with pytest.raises(NotLoggedInError):
        await client.async_get_balance(allow_sso=False)
    assert all("digipolis" not in path for _, path, _ in site.requests)


async def test_server_error(
    http: aiohttp.ClientSession, site: FakeSite, endpoints: Endpoints
) -> None:
    site.fail_with = 503
    client = AfvalcontainerClient(http, {SESSION: "x"}, endpoints=endpoints)
    with pytest.raises(ServerError):
        await client.async_get_balance()


async def test_logged_in_but_unexpected_component_is_parse_error(
    http: aiohttp.ClientSession, site: FakeSite, endpoints: Endpoints
) -> None:
    site.balance_page = {**site.balance_page, "component": "survey/balance/NewDesign"}
    client = AfvalcontainerClient(http, {SESSION: site.new_session()}, endpoints=endpoints)
    with pytest.raises(ParseError):
        await client.async_get_balance()


async def test_containers_page(
    http: aiohttp.ClientSession, site: FakeSite, endpoints: Endpoints
) -> None:
    client = AfvalcontainerClient(http, {SESSION: site.new_session()}, endpoints=endpoints)
    page = await client.async_get_containers_page()
    assert page["props"]["linkedAddresses"]


async def test_set_cookies_ignores_unknown(endpoints: Endpoints) -> None:
    jar = aiohttp.CookieJar()

    class _Session:
        cookie_jar = jar

    client = AfvalcontainerClient(
        _Session(),  # type: ignore[arg-type]
        {"evil": "1", SESSION: "abc", "XSRF-TOKEN": ""},
        endpoints=endpoints,
    )
    assert client.export_cookies() == {SESSION: "abc"}
