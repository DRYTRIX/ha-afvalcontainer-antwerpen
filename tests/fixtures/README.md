# Test fixtures

All fixtures are **synthetic and anonymised**. Names, e-mail addresses, phone
numbers, addresses, container numbers and order IDs are made up.

* `balance_page.json` follows the structure of the real `survey/balance/Index`
  Inertia page (captured 2026-11, values replaced).
* `containers_page.json` is a **guess**: the structure of the real containers
  page has not been verified. The parser only uses it on a best-effort basis.

Never add real captures here. Run `scripts/probe` locally if you need to look
at the real structure; it prints a redacted shape only.
