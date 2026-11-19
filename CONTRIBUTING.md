# Contributing

Thanks for helping! Bug reports with a diagnostics file, fixes for website changes and
translations are all welcome.

## Ground rule: no personal data

Never commit or post cookies, cURL commands, names, e-mail addresses, phone numbers,
street addresses, container numbers or order IDs. Not in code, fixtures, screenshots,
issues or commit messages. Test fixtures are synthetic (see `tests/fixtures/README.md`).
To look at the real site structure, use `scripts/probe`: it prints a redacted shape only.

## Branching (git-flow)

| Branch | Purpose |
|---|---|
| `main` | Released code only. Every merge into `main` is a release and gets a `vX.Y.Z` tag |
| `develop` | Integration branch. **Open pull requests against `develop`** |
| `feature/<name>` | New work, branched from `develop`, merged back with `--no-ff` |
| `release/X.Y.Z` | Release preparation from `develop`, merged into `main` **and** `develop` |
| `hotfix/X.Y.Z` | Urgent fix from `main`, merged into `main` **and** `develop` |

```bash
git switch develop && git pull
git switch -c feature/my-change
# ... commit ...
git push -u origin feature/my-change   # open a PR into develop
```

## Setup

Requires Python **3.14** (Home Assistant 2026.x).

```bash
python3.14 -m venv .venv && . .venv/bin/activate
scripts/setup          # installs pytest-homeassistant-custom-component (pulls in HA) + ruff
```

Or open the repository in the **devcontainer** (VS Code → "Reopen in Container").

## Run Home Assistant with the integration

```bash
scripts/develop          # real website: you need your own login data
scripts/develop --fake   # local fake website on :8765, no account needed
```

Open <http://localhost:8123>, finish onboarding and add **Afvalcontainer Antwerpen**.
With `--fake`, paste `afvalcontainers-antwerpen-session=demo-session`.

`--fake` sets `AFVALCONTAINER_ANTWERPEN_BASE_URL` / `AFVALCONTAINER_ANTWERPEN_SSO_URL`;
those variables are for development only.

## Tests and lint

```bash
pytest                    # all tests (API client against a fake site, flows, coordinator, entities)
ruff check . && ruff format --check .
scripts/lint              # auto-fix
```

CI runs the same plus **hassfest** and the **HACS action**. Run hassfest locally with:

```bash
docker run --rm -v "$PWD:/github/workspace" ghcr.io/home-assistant/hassfest
```

## Code layout

| File | Purpose |
|---|---|
| `api.py` | Pure async client + parsers, **no Home Assistant imports** (could become a PyPI lib) |
| `coordinator.py` | Polling, cookie persistence, SSO retry → reauth, backoff, new-row events |
| `config_flow.py` | Paste step, reauth, reconfigure, options |
| `sensor.py`, `binary_sensor.py`, `button.py` | Entities |
| `device_trigger.py` | "New emptying / top-up" device triggers |
| `diagnostics.py` | Redacted diagnostics |
| `strings.json`, `translations/*.json` | UI texts. Keep `strings.json` == `translations/en.json`, and update `nl.json` |
| `tests/fake_site.py` | Fake website (Inertia 401/409, sliding session, Keycloak SSO) |

## When the website changes

1. Run `scripts/probe` with your own login data and compare the shape with
   `tests/fixtures/balance_page.json` and `docs/site-notes.md`.
2. Update the parser in `api.py` defensively (unknown keys must never crash).
3. Update the synthetic fixture and add a test.
