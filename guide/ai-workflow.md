# AI-Assisted Engineering Workflow

AI supported the work, but architecture choices and accepted code remained subject to human review and executable verification.

1. **Analyze requirements.** Extract invariants, ambiguities, failure modes, and the few decisions that determine the architecture.
2. **Evaluate trade-offs.** Compare credible options and consult primary engineering material where useful. Shopify’s inventory-reservation work informed the discussion here. Prefer one transactional system when PostgreSQL can preserve the invariant; adding queues, caches, or distributed stores creates coordination and operational costs.
3. **Define edge behavior.** Decide what retries, concurrent requests, stock changes, payment uncertainty, coupon competition, and partial failures should produce. Record accepted and deferred cases before coding.
4. **Review the plan.** Map every requirement and important edge case to a component, transaction boundary, API contract, test, or explicit deferral.
5. **Implement with tests.** Build in small stages, test the risky invariant alongside each stage, and use the real PostgreSQL behavior for locking and isolation.
6. **Verify manually.** Exercise APIs with curl, run deterministic payment-failure demonstrations, inspect durable state, and reconcile reports.
7. **Review critically.** Use defect-first reviews, correct AI suggestions that add unjustified complexity, and update decisions when evidence changes the design.

Examples in this project include rejecting a bounded inventory-pool design for simpler unit rows at assignment scale, correcting transaction tests that used Django `TestCase`, and tightening worker locks after reviewing the generated SQL semantics.
