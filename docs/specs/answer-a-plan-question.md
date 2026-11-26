# The operator answers a plan's question on the plan

## Why

An operator can only read a plan's "Worth weighing before you approve" list. To act on one, an operator presses Plan again
and writes a note that repeats the question (`docs/specs/planner-redesign.md`, "Open questions").

## What we want

- Each item in the list gets a short answer field and two buttons:
  - "Send to the planner (up to $X)", X being one planning run's limit, which plans again with the note "About
    `<the item>`: `<the answer>`", through the existing `POST /v1/goals/{id}/plan/replan`.
  - "Not a problem", which removes the item from this version of the plan and records who dismissed it.
- A dismissed item stays dismissed when the page reloads, and the figure "To weigh first" counts only the rest.
- With no items left, the list is gone.

## Where

Paths: src/meta_harness/planner/plan_api.py, src/meta_harness/planner/plans.py, console/src/features/plan

## Checks

- A planner test: dismissing an item removes it from that version's gaps and records who did it.
- A planner test: sending an answer starts a planning run whose note names the item.
- A console test: both buttons, and that the list goes once the operator settles the last item.
- `uv run pytest -q tests/planner` and `npm test` in `console/` pass.
