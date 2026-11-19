"""A small fake of afvalcontainer.antwerpen.be + the A-profiel SSO for tests and development.

It mimics the behaviour observed on the real site (2026-11):

* unauthenticated HTML request -> 302 to /login
* unauthenticated Inertia (X-Inertia) request -> 401
* stale X-Inertia-Version -> 409 with X-Inertia-Location
* every authenticated response renews the session cookie (sliding session)
* /digipolis/individual/redirect -> Keycloak auth endpoint -> callback (only
  when an SSO cookie is valid) -> new app session

The app and the SSO run on different hostnames; requests are routed on the
Host header so a single (unix) socket can serve both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import html
import itertools
import json
from pathlib import Path
from typing import Any

from aiohttp import web

FIXTURES = Path(__file__).parent / "fixtures"
APP_HOST = "afvalcontainer.antwerpen.test"
SSO_HOST = "authentication.antwerpen.test"
APP_URL = f"http://{APP_HOST}"
SSO_URL = f"http://{SSO_HOST}/auth/realms/a-profiel/"
SESSION = "afvalcontainers-antwerpen-session"
VERSION = "945201f29f1ef9549c7fd75a7c3a3cea"


def load_fixture(name: str) -> dict[str, Any]:
    """Load a JSON fixture."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@dataclass
class FakeSite:
    """State of the fake site."""

    version: str = VERSION
    app_url: str = APP_URL
    sso_url: str = SSO_URL
    valid_sessions: set[str] = field(default_factory=set)
    valid_sso: set[str] = field(default_factory=set)
    balance_page: dict[str, Any] = field(default_factory=lambda: load_fixture("balance_page.json"))
    containers_page: dict[str, Any] = field(
        default_factory=lambda: load_fixture("containers_page.json")
    )
    fail_with: int | None = None
    renew: bool = True
    requests: list[tuple[str, str, bool]] = field(default_factory=list)
    _counter: itertools.count = field(default_factory=lambda: itertools.count(1))

    def new_session(self) -> str:
        """Create and register a new session cookie value."""
        value = f"sess{next(self._counter):04d}%3D"
        self.valid_sessions.add(value)
        return value

    # -- helpers -------------------------------------------------------- #
    def _render(self, request: web.Request, page: dict[str, Any]) -> web.Response:
        if request.headers.get("X-Inertia"):
            return web.json_response(page, headers={"X-Inertia": "true", "Vary": "X-Inertia"})
        body = (
            "<!DOCTYPE html><html><head><title>Afvalcontainer</title></head><body>"
            f'<div id="app" data-page="{html.escape(json.dumps(page))}"></div>'
            "<script>const Ziggy={};</script></body></html>"
        )
        return web.Response(text=body, content_type="text/html")

    def _renew(self, response: web.StreamResponse) -> None:
        if not self.renew:
            return
        response.set_cookie(SESSION, self.new_session(), max_age=7200, httponly=True)
        response.set_cookie("XSRF-TOKEN", f"xsrf{next(self._counter):04d}", max_age=7200)

    # -- handlers ------------------------------------------------------- #
    async def handle(self, request: web.Request) -> web.StreamResponse:
        """Route on host + path."""
        host = request.host.split(":")[0]
        self.requests.append((host, request.path, bool(request.headers.get("X-Inertia"))))
        if self.fail_with:
            return web.Response(status=self.fail_with, text="error")
        if host == SSO_HOST or request.path.startswith("/auth/realms/"):
            return self._sso(request)
        return self._app(request)

    def _app(self, request: web.Request) -> web.StreamResponse:
        path = request.path.rstrip("/") or "/"
        logged_in = request.cookies.get(SESSION) in self.valid_sessions
        if path == "/login":
            page = {
                "component": "auth/Login",
                "version": self.version,
                "url": "/login",
                "props": {"auth": {"user": None}},
            }
            return self._render(request, page)
        if path == "/digipolis/individual/redirect":
            raise web.HTTPFound(
                f"{self.sso_url}protocol/openid-connect/auth?client_id=slimmeafvalcontainers"
                f"&redirect_uri={self.app_url}/digipolis/individual/callback&state=x"
            )
        if path == "/digipolis/individual/callback":
            response = web.HTTPFound("/mijn-containers")
            self._renew(response)
            raise response
        pages = {
            "/mijn-containers/saldo": self.balance_page,
            "/mijn-containers/containers": self.containers_page,
            "/mijn-containers": {
                "component": "survey/my-containers/Index",
                "version": self.version,
                "url": "/mijn-containers",
                "props": {"auth": self.balance_page["props"]["auth"]},
            },
        }
        if path not in pages:
            raise web.HTTPNotFound
        if not logged_in:
            if request.headers.get("X-Inertia"):
                raise web.HTTPUnauthorized
            raise web.HTTPFound("/login")
        if (
            request.headers.get("X-Inertia")
            and request.headers.get("X-Inertia-Version") != self.version
        ):
            raise web.HTTPConflict(headers={"X-Inertia-Location": f"{self.app_url}{path}"})
        page = dict(pages[path])
        page["version"] = self.version
        response = self._render(request, page)
        self._renew(response)
        return response

    def _sso(self, request: web.Request) -> web.StreamResponse:
        if request.path.endswith("/protocol/openid-connect/auth"):
            sso_ok = request.cookies.get("KEYCLOAK_IDENTITY") in self.valid_sso or (
                request.cookies.get("dgp.auth.ssokey") in self.valid_sso
            )
            if sso_ok:
                raise web.HTTPFound(
                    f"{self.app_url}/digipolis/individual/callback?code=abc&state=x"
                )
            return web.Response(
                text="<html><title>Log in bij A-Profiel</title><form></form></html>",
                content_type="text/html",
            )
        raise web.HTTPNotFound


def make_app(site: FakeSite) -> web.Application:
    """Build the aiohttp application."""
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", site.handle)
    return app


def main() -> None:  # pragma: no cover - manual development helper
    """Run the fake site on http://localhost:8765 (used by ``scripts/develop --fake``)."""
    import sys  # noqa: PLC0415

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    base = f"http://localhost:{port}"
    demo = FakeSite(app_url=base, sso_url=f"{base}/auth/realms/a-profiel/")
    demo.valid_sessions.add("demo-session")
    print(  # noqa: T201
        f"Fake site on {base}\nPaste this as login data:  {SESSION}=demo-session",
        file=sys.stderr,
        flush=True,
    )
    web.run_app(make_app(demo), host="127.0.0.1", port=port, print=None)


if __name__ == "__main__":  # pragma: no cover
    main()
