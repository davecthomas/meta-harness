# An operator sets whether a second model reviews their goals' plans

## Why

Whether a new goal's plan gets an adversarial review follows its autonomy: on for "Ask before each step", off
otherwise. An operator who always wants a review, or never, has to set the switch on every goal
(`docs/specs/adversarial-plan-review.md`, "Choosing it on a goal").

## What we want

- Settings, under New goal defaults, gains "Adversarial plan review" with three choices: "Follow the autonomy"
  (the default), "Always" and "Never".
- The planner service works out a new goal's default from the creating operator's setting, so a goal made
  through the API gets the same default as one made in the console. A request that says whether to review
  keeps what it said.
- New goal's switch starts from the operator's setting.

## Where

Paths: src/meta_harness/planner/settings.py, src/meta_harness/planner/plan_review.py,
src/meta_harness/planner/service.py, console/src/features/settings, console/src/features/goals/NewGoal.tsx

## Checks

- A planner test for each choice: the goal's review setting when the request does not say, and that a request
  that says wins.
- A console test: the Settings field saves each choice, and New goal's switch starts from it.
- `uv run pytest -q tests/planner` and `npm test` in `console/` pass.
