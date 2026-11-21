# Onboarding Platform

AI-assisted onboarding of customer and order data from CSV/JSON files into a
destination API, with human-approved mappings, deterministic validation, and
imports that are safe to retry.

> **Deterministic code decides. The LLM only suggests and explains.** No model
> output can trigger a destination write, choose a transformation outside the
> approved registry, or bypass validation. Every write is gated by a human
> approval step, and uploaded file content is only ever treated as data.

This is a portfolio project built to be attacked: the failure paths are meant
to be as convincing as the success path.

## Status

Built phase by phase; a phase starts only after the previous phase's
acceptance test passes.

| Phase | Scope | State |
|---|---|---|
| 0 | Decisions ([docs/DECISIONS.md](docs/DECISIONS.md)), mock destination service | Done |
| 1 | Upload CSV/JSON, strict parse, original-file storage, schema fingerprint | Done |
| 2 | Profiling, manual mapping, transformation registry | Next |
| 3 | Deterministic baseline mapper | Not started |
| 4 | LLM mapping suggestions | Not started |
| 5 | Review, approve, validate, dry run | Not started |
| 6 | Import execution and idempotency | Not started |
| 7 | Schema drift | Not started |
| 8 | Evaluation harness | Not started |
| 9 | Demo and docs | Not started |

## Quick start

Requires Docker with Compose v2.

```bash
docker compose up --build -d
```

| Service | URL |
|---|---|
| Web UI | http://localhost:3000 |
| API + OpenAPI docs | http://localhost:8000/docs |
| Mock destination + docs | http://localhost:9000/docs |
| Postgres | `localhost:55432`, user/password `onboarding` |

Then upload the files in [`samples/`](samples/):

- `customers_malformed.csv` is rejected with one row per problem (line, data
  row, problem, explanation). No dataset is created and no file is kept; the
  rejection is still written to the audit log.
- `customers_prompt_injection.csv` is accepted. The instruction-like cells are
  kept verbatim as data, flagged in the UI, and logged. Nothing acts on them.
- `orders_valid.json` shows money literals such as `19.990` preserved exactly
  as text. The parser never creates a float.

## Tests

Every suite runs against real services: Postgres (never SQLite) and a real
socket for the timeout cases.

```bash
make test
```

Without `make`:

```bash
docker compose run --rm --build api pytest -q
```

```bash
docker compose run --rm --build mock-destination pytest -q
```

```bash
docker build --target test -t onboarding-web-test ./frontend && docker run --rm onboarding-web-test
```

Each phase ships failure tests, not only happy-path tests. Phase 1's
acceptance test is
`backend/tests/test_upload_api.py::test_malformed_csv_is_rejected_with_row_level_errors_and_nothing_is_stored`,
with the UI side in `frontend/src/components/UploadPanel.test.tsx`.

## Scope decisions

- **One operator, no auth, no multi-tenancy.** Authentication would add surface
  area without making the import path more reliable, which is what this
  project is judged on.
- **Two entity types only:** customers and orders. Customers import before orders.
- **Fixed allowlists**, hardcoded: 7 currencies, 9 date formats, 10 MiB uploads.
  An ambiguous date such as `03/04/2026` raises a clarification; it is never guessed.
- **Primary destination is a local mock** that supports idempotency keys, rate
  limits, and injectable 500s and timeouts. Stripe test mode is the real
  sandbox used to prove the adapter.

Details and enforcement points: [docs/DECISIONS.md](docs/DECISIONS.md).

Deliberately out of scope: authentication, multi-tenancy, streaming ingestion,
more entity types, multi-agent orchestration, and any LLM-generated code.

## Repository layout

```
backend/            FastAPI app, SQLAlchemy models, Alembic migrations, tests
  app/policy.py     Hardcoded product policy (limits, allowlists)
  app/ingest/       Strict CSV/JSON parser, fingerprint, content-addressed storage
mock_destination/   The deliberately unreliable destination API, with its own tests
frontend/           React + TypeScript + Vite + Tailwind operator UI
samples/            Hand-made files for trying the UI
docs/DECISIONS.md   Phase 0 decisions and where each is enforced
```

## Known limitations (so far)

- The mock destination keeps state in memory; restarting its container clears it.
- CSV must be comma-delimited UTF-8. Semicolon/tab delimiters and legacy
  encodings are rejected with a clear message rather than guessed.
- JSON must be an array of flat objects; nested values are rejected per record.
- An orphaned stored file is possible if the process dies between writing the
  file and committing its dataset row (the file is content-addressed and harmless).
