# Releasing

Versions follow SemVer. The release workflow takes the version from the **tag** and writes
it into `manifest.json` inside the release zip. HACS installs that zip
(`hacs.json`: `zip_release` + `filename`).

## Checklist

- [ ] `main` is green (Tests + Validate workflows)
- [ ] `pytest` and `ruff check .` pass locally
- [ ] Translations complete in `strings.json`, `translations/en.json`, `translations/nl.json`
- [ ] README updated (entities, options, screenshots without personal data)
- [ ] Bump `version` in `custom_components/afvalcontainer_antwerpen/manifest.json` to the new
      version (keeps the repo and the zip in sync), commit, push
- [ ] If the minimum HA version changed: update `hacs.json` → `homeassistant`, README, and
      `requirements_test.txt`
- [ ] GitHub → **Releases** → **Draft a new release**
  - Tag: `v0.1.0` (create on publish, target `main`)
  - Title: `v0.1.0`
  - **Generate release notes**, edit if needed. Call out breaking changes at the top
  - Tick **Set as a pre-release** for betas (`v0.2.0-beta.1`)
- [ ] **Publish**, then check the *Release* workflow attached `afvalcontainer_antwerpen.zip`
- [ ] In a test HA: HACS → Afvalcontainer Antwerpen → ⋮ → **Redownload**, pick the new version,
      restart, check the integration loads
