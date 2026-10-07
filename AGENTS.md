# Repository Guidelines

## Project Structure & Module Organization

`task.md` defines the checkout assignment. `DECISIONS.md`, `DECISION_CONTEXT.md`, and `EDGE_CASES.md` record accepted architecture and pending product rules. The foundation lives in `src/config/` (Django settings, routing, logging), `src/store/` (API, service, payment interface, worker entry point), and `src/fake_payments/` (future independent fake-provider persistence). Foundation tests live in `tests/`; Docker initialization lives in `docker/`. Domain models and checkout behavior are not implemented yet.

## Build, Test, and Development Commands

Start Docker Desktop, then run `make setup` to create local configuration, build images, start PostgreSQL, and migrate both databases. `make up` starts the API and worker; `make down` stops them without deleting data. `make logs` follows service output. `make check` runs Django checks and migration-drift detection; `make lint` runs Ruff; `make test` runs tests against isolated PostgreSQL databases; `make worker-once` exercises the worker entry point. See `README.md` for equivalent Compose commands and troubleshooting.

## Coding Style & Naming Conventions

Use Python 3.13, four-space indentation, descriptive domain names, and Ruff formatting (`make format`) and linting. Keep HTTP views thin; put business transitions in explicit services and database transactions. Keep fake-provider effects on the `payments` database alias and application state on `default`. Do not add cross-database relationships or perform provider calls inside application transactions.

## Testing Guidelines

Use Django's test runner and DRF's API client. Name tests after observable behavior, such as `test_retry_returns_existing_order`. Use PostgreSQL and separate connections for concurrency tests; Django `TransactionTestCase` is appropriate for lock behavior. Assert final persisted state as well as responses. Prioritize the overlapping requests and recovery failures listed in `EDGE_CASES.md`. Run `make check`, `make lint`, and `make test` before submitting changes.

## Commit & Pull Request Guidelines

Use focused commits with imperative subjects, for example `Add PostgreSQL readiness checks`. PRs should describe behavior, linked requirements, and validation. Update endpoint documentation in `README.md` when contracts change. Keep `DECISIONS.md` concise but complete against `task.md`; place detailed context in `DECISION_CONTEXT.md`. Never commit `.env`, credentials, or private AI transcripts.
