# Repository Guidelines

## Project Structure & Module Organization

`task.md` is the assignment contract. Architecture and verification context live in `DECISIONS.md`, `DECISION_CONTEXT.md`, and `EDGE_CASES.md`. Django configuration is in `src/config/`. APIViews and strict request/response serializers live in `src/store/api/`. Domain classes live in `src/store/services/`; models and migrations remain in `src/store/`. `src/fake_payments/` contains the independently persisted provider adapter. Tests live in `tests/`; database initialization and automation live in `docker/`, `scripts/`, `compose.yaml`, and `Makefile`.

## Build, Test, and Development Commands

Run `make setup` once to create `.env`, build images, migrate both databases, and seed demo data. Use `make up`, `make down`, and `make logs` for daily development. `make reset-inventory` restores available stock for stable demo products without changing order history. `make check` runs Django checks and migration-drift detection. `make lint` checks Ruff lint and formatting; `make format` applies formatting. `make test` runs the suite against isolated PostgreSQL databases. `make api-smoke` calls every API against a running seeded stack and consumes one unit. `make demo` exercises payment and local-finalization recovery; `make worker-once` drains one worker batch.

## Coding Style & Naming Conventions

Use Python 3.13, four-space indentation, descriptive domain names, and Ruff. Preserve the flow `urls.py → APIView → serializer → service → ORM`. Use plain DRF serializers for client input and service output; serializers may delegate through `create()` or `update()` but must not save models directly. Keep ORM queries and `transaction.atomic()` inside class-based services. APIViews, workers, and management commands must only construct and call services. The fake-payment adapter may access its isolated database. Acquire locks in the documented order, never call a provider inside an application transaction, and never add cross-database relationships.

## Testing Guidelines

Use Django’s test runner, DRF’s API client, and PostgreSQL. Name tests for observable behavior, such as `test_last_unit_competition_has_one_winner`. Unit-test serializer normalization, strict fields, and service delegation. Use `TransactionTestCase`, separate connections, and coordination barriers for lock behavior. Assert responses and durable state across both database aliases. The architecture test must continue to reject direct persistence in entry points. Map new edge cases in `EDGE_CASES.md`, then run `make check`, `make lint`, and `make test`.

## Commit & Pull Request Guidelines

Use focused imperative commits, for example `Add leased payment recovery worker`. PRs should explain behavior, invariants, deferred limits, and validation. Update README examples when contracts change. Never commit `.env`, credentials, generated caches, or private AI transcripts.
