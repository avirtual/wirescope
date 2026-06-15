# orderflow — internal order-processing service

## What this is
orderflow ingests customer orders off a queue, validates them against the
catalog, applies pricing + promotions, and emits fulfillment events. Python
3.12, asyncio throughout. Owned by team-payments.

## Build & test
- `make setup` — create the venv, install deps from requirements.lock
- `make test` — pytest; CI gates on 90% coverage, do NOT lower the threshold
- `make lint` — ruff + mypy strict; fix all findings before pushing
- `make run` — local server on :8400 against the docker-compose stack

## Conventions (enforced in review)
- All money is integer cents, never floats. Currency is always explicit.
- Public funcs are fully type-annotated; no bare `except`.
- New modules register their tables in the schema registry, never inline DDL.
- Log via the structured logger; never `print` in library code.
- Feature flags live in flags.py and default OFF.

## Architecture
queue consumer -> validator -> pricing -> promotions -> emitter. Each stage is
a pure-ish transform over an Order; side effects only at the edges. See
docs/adr/ for the decisions. The pricing stage is the hot path — profile before
touching it.

## Gotchas
- The promotions engine is order-dependent; stacking rules are in promo_rules.
- Don't call the catalog synchronously in the hot path; use the cache.
- Releases are cut Thursdays; freeze Wednesday afternoon.
