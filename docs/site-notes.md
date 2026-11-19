# How afvalcontainer.antwerpen.be works (notes for maintainers)

Observed on **2026-11-19** with unauthenticated requests only, plus the
captured authenticated balance page that the integration was designed from.
Re-check with `scripts/probe` (redacted output) when something breaks.

## Stack

* Laravel + Inertia.js. Every page embeds its page object as HTML-entity
  encoded JSON in `<div id="app" data-page="…">`.
* Asset version seen on the login page: `945201f29f1ef9549c7fd75a7c3a3cea`
  (identical to the captured balance page).
* Routes are exposed through Ziggy (`const Ziggy = {…}` in every page).

## Verified behaviour

| Request | Result |
|---|---|
| `GET /mijn-containers/saldo` without session (HTML) | `302 → /login`, sets `XSRF-TOKEN` + `afvalcontainers-antwerpen-session` (Max-Age 7200, httponly) |
| same with `X-Inertia: true` + current version | **`401`** (no redirect) |
| any page with `X-Inertia` + stale version | **`409`** + `X-Inertia-Location: <same url>` |
| `GET /login` with Inertia headers | `200 application/json`, `X-Inertia: true`, component `auth/Login` |
| `GET /digipolis/individual/redirect` | `302 →` `https://authentication.antwerpen.be/auth/realms/a-profiel/protocol/openid-connect/auth?client_id=slimmeafvalcontainers&…&acr_values=200` |
| Keycloak auth page without SSO session | `200` "Log in bij A-Profiel", sets `KC_AUTH_SESSION_HASH` (path `/auth/realms/a-profiel/`) |

The client's "not logged in" detection therefore uses: HTTP 401/403/419, a
redirect to `/login` or to the SSO host, a final URL other than the requested
page, or an `auth/*` component. A *different* component while a user is still
logged in is reported as a parse error (not a reauth), so a site redesign does
not lock users into a reauth loop.

## SSO

The SSO is **Keycloak** (realm `a-profiel` on `authentication.antwerpen.be`),
not a cookie on the app domain. Keycloak keeps its SSO session in
`KEYCLOAK_IDENTITY` / `KEYCLOAK_SESSION` / `AUTH_SESSION_ID`, which are
host-only cookies for `authentication.antwerpen.be`, path
`/auth/realms/a-profiel/`. A "Copy as cURL" of the saldo request does **not**
contain them (different host). `dgp.auth.ssokey` (`.antwerpen.be`) is still
accepted and sent if present, but it was not observed during this check.

**Silent re-login: implemented, not verified with a real account.** The flow
`/digipolis/individual/redirect → Keycloak auth → /digipolis/individual/callback`
creates a new app session without user interaction *if* Keycloak still has a
valid SSO session cookie in our jar. That only happens when the user pasted the
Keycloak cookies (cookie-editor JSON export of `authentication.antwerpen.be`)
and the Keycloak SSO session has not yet idled out (typically much shorter than
days). In practice the integration relies on **polling every ≤ 90 min**: each
response renews the 2 h sliding app session, so the session only dies when HA
is off (or the site is down) for more than ~2 h, or when the site ends sessions
server-side.

To verify, run `scripts/probe --sso` with a cookie-editor export that includes
the `authentication.antwerpen.be` cookies.

## Not verified (needs an account)

* Exact shape of `props.auth.user` (the parser accepts `email`/`id` and
  `first_name`/`firstName`/`name`).
* `/mijn-containers` and `/mijn-containers/containers`: used best-effort for
  container fraction/volume, at most once per 24 h. Failures are ignored.
* `/mijn-containers/saldo/export`: not used. `scripts/probe` prints its
  status, content type and size so it can be evaluated later.

## Other routes (Ziggy, 2026-11)

`survey.my-containers.index` `/mijn-containers`, `…containers.index`
`/mijn-containers/containers`, `…linked-address.show`
`/mijn-containers/containers/{linkedAddress}`, `…balance.index`
`/mijn-containers/saldo`, `…balance.report.show` `/mijn-containers/saldo/export`,
`…balance.top-up.index` `/mijn-containers/saldo/aanvullen`, `login` `/login`,
`logout` `POST /logout`, `digipolis.redirect.individual`
`/digipolis/individual/redirect`, `digipolis.callback.individual`
`/digipolis/individual/callback`. The integration only ever sends `GET`
requests.
