# Coding Standards

## Structure

HTTP code follows `urls.py → APIView → serializer → class-based service → ORM`. Views translate HTTP concerns. Plain DRF serializers validate request and response shapes without persisting models. Services own database reads, writes, transactions, locking, and state-dependent validation. Workers and management commands call the same services.

Use Python 3.13, four-space indentation, descriptive domain names, type-stable DTOs, and Ruff formatting and linting. Store money as integer cents and timestamps in UTC. Keep application and fake-provider data in separate PostgreSQL databases without cross-database relationships.

## Testing

Development follows a risk-focused TDD loop: define observable behavior, write a failing test for the invariant or regression, implement the smallest complete change, and run focused then full checks. Use PostgreSQL `TransactionTestCase`, separate connections, and coordination events for concurrency behavior. Use DRF’s client for API contracts and serializers for validation tests.

Before handoff, run:

```sh
make check
make lint
make test
make api-smoke
make demo
```

Docker Compose supplies reproducible API, worker, and PostgreSQL services. `uv.lock` pins Python dependencies.

## Intentionally avoided

- ORM queries or transactions in views, serializers, workers, or commands.
- `ModelSerializer` persistence and implicit per-request transactions.
- Floating-point money or provider calls inside application transactions.
- In-process locks, cross-database foreign keys, and unmeasured distributed infrastructure.
- Logging request bodies, emails, coupon codes, credentials, or other sensitive values.
- Authentication, real payments, refunds, taxes, shipping, and multi-warehouse behavior; these are documented deferred scope.
