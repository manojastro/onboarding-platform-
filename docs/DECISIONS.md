# Phase 0 — Decisions

These are made up front on purpose. Each one is enforced in code, and the
enforcement point is listed so a reviewer can check it rather than trust it.

| # | Decision | Choice | Enforced in |
|---|---|---|---|
| D1 | Primary demo destination | Local **mock destination** service (idempotency keys, rate limits, injectable 500s / timeouts) | `mock_destination/` |
| D2 | Real sandbox destination | **Stripe test mode** (`api.stripe.com` with an `sk_test_…` key) | Stripe adapter (Phase 6); refuses live keys |
| D3 | Operator model | **One operator, no auth, no multi-tenancy** | README scope section; every actor is `settings.operator_name` |
| D4 | Entity types | `customer`, `order` — nothing else | `backend/app/policy.py` (`ENTITY_TYPES`), DB `CHECK` constraints |
| D5 | Currencies | Fixed allowlist: USD, EUR, GBP, INR, JPY, CAD, AUD | `policy.SUPPORTED_CURRENCIES` |
| D6 | Date formats | Fixed allowlist of 9 named formats; ambiguous input raises a clarification | `policy.DATE_FORMATS`, `policy.AMBIGUOUS_DATE_FORMAT_GROUPS` |
| D7 | Max upload size | **10 MiB** (10,485,760 bytes) hard limit, plus 200 columns / 100,000 rows | `policy.MAX_UPLOAD_BYTES`, ASGI body-limit middleware + handler check |
| D8 | Money type | `Decimal` everywhere; `NUMERIC` in Postgres; decimal strings on the wire | parser keeps number literals as text; test asserts no float columns |

Policy values are hardcoded constants, not environment variables. Changing one
is a reviewed code change.

---

## D1 — Mock destination (primary demo target)

A small FastAPI service that behaves like a typical SaaS API and can be told to
misbehave. It is a separate process: killing the import worker does not reset
it, which is what makes the resume and duplicate tests meaningful.

| Capability | Behaviour |
|---|---|
| Auth | `Authorization: Bearer <MOCK_API_KEY>`; missing or wrong key → `401` |
| Create | `POST /v1/customers`, `POST /v1/orders` → `201` |
| Update | `PATCH /v1/customers/{id}`, `PATCH /v1/orders/{id}` → `200` |
| Reconciliation lookup | `GET /v1/customers?external_id=…`, `GET /v1/orders?external_id=…` |
| Idempotency keys | `Idempotency-Key` on POST/PATCH. Same key + same body → original response replayed (`Idempotent-Replayed: true`). Same key + different body → `400 idempotency_error`. Same key while the first request is still running → `409` |
| Rate limiting | Token bucket; exceeding it → `429` with `Retry-After` |
| Validation | Unknown fields, bad email, money sent as a JSON number, too many decimals for the currency, unknown customer → `400` (never worth retrying) |
| Fault injection | `POST /_admin/faults` with rules: `error_500`, `error_503`, `rate_limit_429`, `timeout_before_commit`, `timeout_after_commit`. Rules can skip the first N matching requests (`after_requests`) and fire a bounded number of times (`times`) |
| Evidence | `GET /_admin/stats` (request counts, idempotent replays, duplicate `external_id`s), `GET /_admin/records/{entity}`, `POST /_admin/reset` |

Like Stripe, the mock does **not** enforce uniqueness of `external_id`. A
careless client can create duplicates, and `/_admin/stats` will show them. The
platform has to earn "zero duplicates"; the destination does not hand it out.

Injected `500`/`503`/`429`/`timeout_before_commit` faults happen before the
request executes, so nothing is written and nothing is stored under the
idempotency key. `timeout_after_commit` writes the record, stores the
idempotent response, then stalls long enough for the client to time out — the
"did my write land?" case that reconciliation exists for.

State is in memory. Restarting the mock container clears it (documented
limitation; `/_admin/reset` does the same on purpose).

## D2 — Stripe test mode (proves the adapter against a real API)

Chosen because Stripe's `Idempotency-Key` semantics are the reference
implementation that the mock imitates.

| Canonical entity | Stripe object | Notes |
|---|---|---|
| customer | `POST /v1/customers` | `email`, `name`, `phone`, `metadata[external_id]` |
| order | `POST /v1/payment_intents` (left unconfirmed) | `amount` in minor units computed exactly from `Decimal`, `currency`, `customer`, `metadata[external_id]`, `metadata[order_status]`, `metadata[order_date]` |

Caveat, stated plainly: Stripe has no first-class order object any more. An
unconfirmed PaymentIntent is the closest "order awaiting payment" object. It is
used to prove the adapter works end-to-end against a real API with real
idempotency semantics, not as a modelling recommendation.

Reconciliation on Stripe is done by replaying the identical request with the
same idempotency key (Stripe returns the original result for keys younger than
24h). Stripe's Search API is eventually consistent, so it is not used as the
primary read-after-write check.

Safety: the adapter refuses to start with any key that is not a test-mode key
(`sk_test_` / `rk_test_`). The key comes from `STRIPE_API_KEY` in `.env`,
which the operator fills in themselves.

## D3 — Single operator

One trusted operator, no login, no roles, no tenants. `approved_by` and audit
`actor` fields are filled from `OPERATOR_NAME` (default `operator`). Background
work records `system` as the actor. This is a scope decision, not an oversight:
authentication would add surface area without making the import path any more
reliable, which is what this project is judged on.

## D4 — Canonical schemas (fixed now so every later phase lines up)

**customer**

| Field | Required | Type | Notes |
|---|---|---|---|
| `external_id` | yes | text | Source system ID; part of the idempotency anchor |
| `email` | yes | email | `validate_email` |
| `first_name` | no | text | |
| `last_name` | no | text | |
| `phone` | no | text | Preserved as text; no phone normalisation |
| `country_code` | no | text | ISO 3166-1 alpha-2 shape (`^[A-Z]{2}$`) |
| `signup_date` | no | date | `parse_date(<format>)` |
| `status` | no | enum | `active`, `inactive` |

**order**

| Field | Required | Type | Notes |
|---|---|---|---|
| `external_id` | yes | text | Idempotency anchor |
| `customer_external_id` | yes | text | Must reference a customer that imported successfully, else relationship error |
| `order_date` | yes | date | `parse_date(<format>)` |
| `total_amount` | yes | decimal | `parse_decimal`; `>= 0`; no more fractional digits than the currency allows |
| `currency` | yes | enum | Allowlist D5 |
| `status` | no | enum | `pending`, `paid`, `shipped`, `cancelled`, `refunded` |

Customers import fully before orders.

## D5 — Currencies

| Code | Minor-unit digits |
|---|---|
| USD | 2 |
| EUR | 2 |
| GBP | 2 |
| INR | 2 |
| JPY | 0 |
| CAD | 2 |
| AUD | 2 |

An amount with more fractional digits than its currency allows
(`10.005 USD`, `1000.5 JPY`) is **rejected**, never rounded. JPY is in the
list specifically to exercise the zero-decimal path.

## D6 — Date formats

Referenced by name in `parse_date(<name>)`. Nothing outside this list is parsed.

| Name | Shape | Example (3 April 2026) |
|---|---|---|
| `ISO_DATE` | `YYYY-MM-DD` | `2026-04-03` |
| `ISO_DATETIME` | `YYYY-MM-DDTHH:MM:SS[.ffffff][Z\|±HH:MM]` | `2026-04-03T14:05:00Z` |
| `YMD_SLASH` | `YYYY/MM/DD` | `2026/04/03` |
| `DMY_SLASH` | `DD/MM/YYYY` | `03/04/2026` |
| `MDY_SLASH` | `MM/DD/YYYY` | `04/03/2026` |
| `DMY_DASH` | `DD-MM-YYYY` | `03-04-2026` |
| `MDY_DASH` | `MM-DD-YYYY` | `04-03-2026` |
| `D_MON_YYYY` | `D Mon YYYY` (English month name or abbreviation) | `3 Apr 2026` |
| `MON_D_YYYY` | `Mon D, YYYY` | `Apr 3, 2026` |

Rules:

1. **Ambiguity is never guessed.** `DMY_*` and `MDY_*` form ambiguity groups.
   If every value in a column parses under both members of a group
   (e.g. `03/04/2026`, `11/12/2025`), the column needs a **clarification**:
   the operator picks the format, and that choice is recorded in the mapping
   version. A single value with a day above 12 (`25/04/2026`) resolves it.
2. Once a mapping names a format, parsing is strict: a value that does not
   match is a row-level validation error, not a fallback to another format.
3. Two-digit years are never accepted.
4. `ISO_DATETIME` → date keeps the calendar date **as written**. No timezone
   conversion, because converting would require guessing the business's
   timezone.

## D7 — Upload limits and accepted files

| Limit | Value | Failure |
|---|---|---|
| File size | 10 MiB | `413 file_too_large`, nothing stored |
| Columns | 200 | `422 too_many_columns` |
| Data rows | 100,000 | `422 too_many_rows` |
| Reported parse errors | first 200 (total count always reported) | — |

Accepted formats:

- **CSV** — UTF-8 (a BOM is tolerated), comma-delimited, RFC 4180 quoting,
  header row required, every row must have exactly as many fields as the header.
- **JSON** — a top-level array of flat objects. Nested objects/arrays are
  rejected per row. Number literals are kept as their exact source text
  (`19.990` stays `"19.990"`); `NaN`/`Infinity` are rejected; duplicate keys in
  one object are rejected.

A file with any parse error is rejected as a whole. The rejection is written
to `audit_event` (with the first errors), and no dataset row or stored file is
created.

## D8 — Money

- The parser never produces a `float`: CSV cells are text and JSON number
  literals are captured as text.
- `parse_decimal` (Phase 2) converts text to `Decimal` with an explicit grammar.
- Postgres money-like columns are `NUMERIC`; a test fails the build if any
  `REAL` / `DOUBLE PRECISION` column exists.
- On the wire, amounts are decimal strings. The mock destination rejects money
  sent as a JSON number.

## Uploaded content is data

Cells are never interpreted. As a visibility aid (not as the defence), the
parser flags cells that look like instructions to an AI (`ignore previous
instructions`, role tags, `SYSTEM:` prefixes). The file is still accepted, the
value is kept verbatim, and an `audit_event` records where it was found. The
actual defence is structural: the LLM only ever returns suggestions that are
validated against a schema and an allowlist, and it has no path to a write.
