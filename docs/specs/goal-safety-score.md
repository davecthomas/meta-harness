# Muxxy scores a goal's plan for safety and shows the score

## Why

Nothing reads a goal for what it would do to its repos before its tasks run
(`docs/specs/goal-safety.md`, "Why"). This builds the part of that design that reads and shows: the score, its
badge and an admin's answer on whether it was right.

## Not in this goal

Holding or blocking a plan. Approving a plan works exactly as it does now, whatever a goal's score is. This
goal saves the thresholds and uses them for the badge's word, and for nothing else. A person builds that part
of the design ("Held and blocked goals") apart, after this is live. A task that changes who may approve a plan, or
adds a refusal to `POST /v1/goals/{id}/approve`, is outside this goal.

## What we want

- **The score** as `docs/specs/goal-safety.md` says under "The score": one structured call for each saved plan
  version, on `planning.safety.model`, kept with the version, its cost recorded as `goal_safety_scored`, and
  tried again at the next sweep when it cannot be made.
- **The prompts** from that file's "The prompts", word for word: the system prompt, the prompt's layout between
  its markers, and the answer's schema. A test compares the system prompt in the code with the one in that
  file, so the two cannot differ.
- **The settings** in Configuration, Planning, as "What an admin sets" says: score goals, ask at, block at, and
  the model picked from the models the Gateway lists. The settings refuse a block threshold below the ask threshold.
- **The badge** beside the goal's status, as "What the operator sees" says: Safe, Check, Unsafe or Not scored,
  with the reasons on hover, and the reasons in the plan's decision box above its buttons when the badge is not
  Safe. The plan's read (`GET /v1/goals/{id}/plan`) serves the score, the reasons and the badge's word, so the
  console decides nothing.
- **Right or wrong**: an admin can say of a scored goal that its score was right or wrong, from the badge's
  hover. Each answer is a `goal_safety_judged` event with the score. Performance shows, for the time in view,
  the goals scored, how many read Check and Unsafe, and how many answers said the score was wrong.

## Where

Paths: src/meta_harness/planner/goal_safety.py, src/meta_harness/planner/migrations,
src/meta_harness/events/types.py, tests/events/registered_schemas.json, config/planner.json,
src/meta_harness/config/kinds.py, src/meta_harness/planner/plan_api.py, src/meta_harness/tasks/performance.py,
console/src/features/goals/GoalSafety.tsx, console/src/features/plan, console/src/features/config,
console/src/features/performance, tests/planner/test_goal_safety.py

## Checks

- A planner test: the planner scores a saved plan once, with the system prompt of
  `docs/specs/goal-safety.md` word for word, the goal's text between the markers, and a line of it that reads
  as a marker dropped.
- A planner test for each of the three goals in that file's table, with a fake Gateway: the score kept, its
  reasons cut to three, and `goal_safety_scored` recorded with its cost.
- A planner test: an answer outside the schema is no score, and the next sweep asks again.
- A planner test: with scoring off, the planner scores nothing.
- A planner test: an operator approves a goal scored above the block threshold as they approve any goal.
  Nothing here holds a plan.
- A console test: the badge for each state, the reasons on hover, and the reasons in the decision box above
  its buttons for a plan that is not Safe.
- A console test: the settings pick the model from the list, refuse a block threshold below the ask
  threshold, and save.
- `uv run pytest -q tests/planner tests/events` and `npm test` in `console/` pass.
