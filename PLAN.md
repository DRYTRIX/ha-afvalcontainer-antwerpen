# PLAN — Afvalcontainer Antwerpen (HACS integration)

Running checklist. Each phase ends with a commit.

## Phase 0 — Research (verified 2026-11-19)
- [x] Current HA: 2026.11 (Python >= 3.14.2); test harness `pytest-homeassistant-custom-component` 0.13.375 (HA 2026.10.4)
- [x] Live site: unauthenticated HTML → `302 /login`; unauthenticated Inertia JSON → `401`; stale `X-Inertia-Version` → `409` + `X-Inertia-Location`
- [x] Inertia version on the login page equals the captured one; Ziggy route list extracted (see `docs/site-notes.md`)
- [x] SSO = Keycloak realm `a-profiel` on `authentication.antwerpen.be`; login methods: itsme, eID, federal app code, SMS code (no password)
- [x] Brand images: local `custom_components/<domain>/brand/icon.png` (HA >= 2026.3; HACS `brands` check accepts it)
- [x] `hacs.json` schema (render_readme, homeassistant, zip_release, filename, country) checked against HACS source
- [x] Config-flow APIs: `_get_reauth_entry`, `_get_reconfigure_entry`, `async_update_reload_and_abort(data_updates=)`, `OptionsFlowWithReload`

## Phase 1 — Scaffolding
- [x] .gitignore, LICENSE (MIT), pyproject (ruff + pytest), requirements_test.txt
- [x] hacs.json, manifest.json (0.1.0; owner `DRYTRIX`, codeowner `@evilguy4000`)

## Phase 2 — API client (`api.py`, no HA imports)
- [x] Login-data paste parser (cookie header, cURL bash/cmd/PowerShell, Firefox headers, cookie-editor JSON, cookies.txt)
- [x] Dutch number parsing, defensive history normalisation, unknown services
- [x] Inertia JSON with version → 409/HTML fallback → 401/redirect = not logged in
- [x] Silent SSO re-login attempt, cookie export for persistence, 5xx → server error
- [x] Tests against a local fake site (aiohttp test server)

## Phase 3 — Core integration
- [ ] const.py, `__init__.py` (runtime_data, dedicated ClientSession + CookieJar)
- [ ] coordinator: cookie persistence only on change, SSO retry → ConfigEntryAuthFailed, 5xx backoff, seen-ID Store + events
- [ ] config flow (paste step, errors, unique ID = hash of email, reauth, reconfigure, options)
- [ ] strings.json + translations en/nl
- [ ] Tests: config flow, coordinator, events

## Phase 4 — Entities & extras
- [x] sensors (balance, minimum, last emptying/weight/cost, year/month totals, emptyings, last top-up, history)
- [x] per-container sensors when more than one container
- [x] binary_sensor low balance, refresh button
- [x] device triggers, diagnostics (redacted), icons.json
- [x] Tests

## Phase 5 — HACS repository polish
- [x] Brand icon (original: container + euro), PNG 256/512 + SVG source
- [x] GitHub Actions: validate (HACS + hassfest), tests, release (zip + version from tag), Dependabot
- [x] Issue/PR templates, CONTRIBUTING.md, README.md, RELEASING.md
- [x] Devcontainer + scripts/develop, scripts/setup, scripts/lint

## Phase 6 — Verification & optional research
- [x] Dev HA instance (`scripts/develop --fake`, HA 2026.10.4): added the integration via the
      config-flow API the UI uses: form, `invalid_format` error, cURL paste → entry + 19 entities;
      options flow reload; diagnostics download (no leaks); restart → expired session → reauth
      flow → `reauth_successful`; duplicate → `already_configured`
- [x] Fixed a bug found there: HA-created client sessions must be detached, not closed
- [x] hassfest (official Docker image): 0 invalid integrations
- [x] Release workflow steps simulated locally (tag → manifest version, zip layout)
- [x] `scripts/probe` against the real site with a dummy cookie: correctly "not logged in"
- [x] docs/auth-research.md: A-profiel offers only itsme / eID / federal app / SMS → no password
      automation; cookie paste stays the only method
- [x] Personal-data sweep of tracked files and history contents (clean; see note on commit author)

## Phase 7 — Publish (git-flow)
- [x] Commit authors rewritten to the GitHub noreply address
- [x] Branches: `main` (releases), `develop` (integration), `feature/*`, `release/*`
- [x] Repository description and topics set, Issues enabled
- [x] `release/0.1.0` merged into `main` + `develop`, tag `v0.1.0`, pushed
- [ ] Publish the GitHub release for `v0.1.0` (the release workflow attaches the zip)

## Still open (needs a real account or GitHub)
- [ ] Verify silent SSO re-login with real Keycloak cookies (`scripts/probe --sso`)
- [ ] Check `/mijn-containers/containers` and `/saldo/export` shapes (`scripts/probe`)
- [ ] HACS action run (repo pushed; check the Validate workflow)
- [ ] Replace placeholder screenshots
