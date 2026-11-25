# The review button says what it can cost

## Why

The Plan page's "Review this plan" button starts an adversarial review, which runs a model and spends money. The
button does not say how much, so an operator who wants to know before pressing it has to open Configuration.

## What we want

- The button reads "Review this plan (up to $4.00)", with the limit the review would run under.
- That limit is the one `plan_review.py` already uses: `planning.adversarial_review.max_usd`, else
  `planning.max_usd`.
- The plan API (`GET /v1/goals/{id}/plan`) returns it as `review_usd`, a string such as `"4.00"`. The console
  shows it as given and works out nothing itself.
- When `review_usd` is missing, the button reads "Review this plan", as now.

## Where

Paths: src/meta_harness/planner/plan_api.py, src/meta_harness/planner/plan_review.py,
console/src/features/plan

## Checks

- A planner test: the plan API returns `review_usd` from the review setting, and from `planning.max_usd` when the
  review has none.
- A console test: the button shows the limit the API gives, and the plain label without one.
- `uv run pytest -q tests/planner` and `npm test` in `console/` pass.
