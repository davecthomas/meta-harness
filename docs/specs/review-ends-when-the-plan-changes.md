# A running plan review ends when the operator decides or edits

## Why

An operator who approves a plan while its adversarial review is still running gets the review's findings later,
about a plan they already approved. An operator who edits the plan gets findings about a version that is gone.
Both pay for a review nobody reads (`docs/specs/adversarial-plan-review.md`, "When the plan changes while a
review runs").

## What we want

- **Approving while a review runs.** The Plan page's decision bar shows "Approve now" as a link beside the
  review's spinner. Pressing it cancels the running review and approves the plan as it stands.
- **Editing while a review runs.** Saving an edit (`PUT /v1/goals/{id}/plan`) cancels the running review. The
  review part then offers "Review again (up to $X)", the review's spend limit.
- A canceled review keeps the findings it recorded, and its state reads "Canceled".
- Planning again while a review runs leaves the review alone; the planner reviews the new version once it saves
  it, as now.

## Where

Paths: src/meta_harness/planner/plan_review.py, src/meta_harness/planner/plan_api.py,
src/meta_harness/planner/actions.py, console/src/features/plan

## Checks

- A planner test: approving while a review runs cancels its task and approves; the review's state is
  `canceled` and its findings stay.
- A planner test: an edit while a review runs cancels it, and the plan's decision offers "Review again".
- A console test: the decision bar shows "Approve now" only while a review runs.
- `uv run pytest -q tests/planner` and `npm test` in `console/` pass.
