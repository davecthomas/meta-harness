# A plan's tasks grouped by repo, and each group's spend

## Why

The plan page groups a plan's tasks by stage only. An operator approving a plan that changes several repos cannot see
at a glance what each repo gets, and no group says what it may spend (`docs/specs/planner-redesign.md`,
"Tasks").

## What we want

- Over the plan's tasks, a two-way switch: "By stage" (the default) and "By repo". It shows only when the plan's
  tasks are in more than one repo.
- By repo, the repo's name and its task count head each group. A task's stage shows on its row.
- Each group, by stage or by repo, shows the most its tasks may spend, as "up to $X": the sum of each task's
  spend limit for its size. A group with an unsized task shows no amount.
- The address holds the choice, so a reload keeps it.

## Where

Paths: console/src/features/goals/GoalPlan.tsx, console/src/features/plan

## Checks

- A console test: the switch shows only for a plan in more than one repo; by repo, each task is in its repo's
  group with its stage; each group's amount is the sum of its tasks' limits, and no amount with an unsized task.
- `npm test` in `console/` passes.
