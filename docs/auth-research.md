# Can login be automated with username + password?

**Short answer: no. Cookie paste stays the only method.**

## What was checked (2026-11-19)

Following `/digipolis/individual/redirect` leads to the A-profiel login page
(Keycloak realm `a-profiel`, client `slimmeafvalcontainers`, `acr_values=200`).
That page is a *credential selector* (`kc-select-credential-form`) with exactly
these options:

1. **itsme** (app approval on the phone)
2. **eID / eVK** (card reader + PIN)
3. **"Via de app van de federale overheid"** (one-time code from the federal
   authenticator app)
4. **"Met sms code van de federale overheid"** (one-time code by SMS)

There is **no username + password-only option** for this client at this
assurance level. Every option needs a second factor that is held by the
person (phone, card, SMS), which is by design.

## Conclusion

* Automating any of these would mean scripting or bypassing a strong-
  authentication step (relaying SMS codes, emulating itsme, storing TOTP
  seeds of the federal app). That is fragile, possibly against the terms of
  use, and it weakens the user's account security. It is **not implemented**.
* What *is* implemented to reduce re-pasting:
  * polling (default 30 min, max 90 min) keeps the 2 h sliding session alive;
  * renewed cookies are written back to the config entry so restarts within
    the 2 h window resume seamlessly;
  * a silent SSO re-login is attempted once before asking the user, which
    helps when Keycloak SSO cookies were pasted and are still valid
    (see `docs/site-notes.md`).
* If the city ever offers a password option, an official API or OAuth
  device flow for this client, revisit this.
