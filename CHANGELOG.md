# Changelog

All notable changes to this project are documented here. Versions follow
[SemVer](https://semver.org/).

## [Unreleased]

### Added
- The Laravel "remember me" cookie (`remember_web_…`) is kept if the site sets one, so the
  site can log the integration back in after the session cookie expired.
- A warning in the log whenever the session ends, with the reason, the result of the silent
  SSO attempt, the time since the last successful update and since the login data was pasted.
  The same timing is in the diagnostics (`session`).
- `scripts/probe` shows cookie lifetimes (no values), including those in a cookie-editor export.

## [0.1.1] - 2026-11-19

### Changed
- README screenshots now come from a real installation instead of placeholders.

## [0.1.0] - 2026-11-19

First release.

### Added
- UI setup by pasting login data (cURL from Chrome/Firefox/Edge/Safari, Cookie header,
  Firefox request headers, cookie-editor JSON, cookies.txt). Only the needed cookies are kept.
- Reauth and reconfigure flows; options for update interval (10–90 min) and low-balance threshold.
- Session keep-alive: renewed cookies are persisted; silent A-profiel SSO re-login is tried
  before asking to re-authenticate.
- Sensors: balance, minimum balance, last emptying (date/weight/cost), weight and cost this
  month/year, emptyings this year, last top-up, history; per-container sensors for 2+ containers.
- Low-balance binary sensor, refresh button.
- Events `afvalcontainer_antwerpen_new_emptying` / `_new_topup` and matching device triggers.
- Redacted diagnostics, Dutch and English translations, local brand icon.

[0.1.1]: https://github.com/DRYTRIX/ha-afvalcontainer-antwerpen/releases/tag/v0.1.1
[0.1.0]: https://github.com/DRYTRIX/ha-afvalcontainer-antwerpen/releases/tag/v0.1.0
