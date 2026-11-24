# Adversarial plan review

Status: approved in outline by the owner on 2026-11-24. To be built as a harness goal from this file.

## Why

A plan is written by one model, and that model is blind to its own mistakes: a step it never thought of, an
order that cannot work, a task too big for one pull request, an acceptance check that would pass without the
goal being met. Each pull request already gets a reviewer that is a different model from the one that wrote it.
A plan, which decides every pull request after it, gets no such reviewer.

Adversarial plan review gives a plan that reviewer. A second model, from a different family, reads the same
repos and the plan, and tries to find what is wrong with it. The planner fixes what it can, and the operator
sees what was found, what changed and what is still open before approving.

It is an option, for operators who want more certainty before work starts, and is on by default where the
operator has already asked to approve everything.

## What happens today

- A planning task writes a plan version (`source: planned`) and the operator approves it (`planner/planning.py`,
  `planner/plan_api.py`).
- After it is saved, one small model call on the updates model sorts the gaps the planner listed itself, and when
  a change to the plan would settle some, a second planning task writes a revised plan, offered as a suggestion
  the operator accepts with one click (`planner/suggestions.py`, `suggest_fixes` in `config/planner.json`).
- That check reads only the planner's own list of gaps. Nothing independent reads the repos and challenges the
  plan.

## The flow

1. The planner writes the plan, as now.
2. A plan reviewer, a different model, reads the goal, its spec, the repos and the plan, and records findings.
3. When a finding blocks, the planner revises the plan with the findings as its note, and the reviewer reviews
   the revision. This repeats at most `max_rounds` times.
4. The operator sees the plan with its findings: fixed ones folded, open ones open, and the drafts the review
   changed marked.
5. The operator approves, as now. A plan with open blocking findings can still be approved; the button says how
   many are open.

## What we want

### Choosing it on a goal

- New goal, panel 4 ("How it runs"), gains a switch: "Adversarial plan review", with the sentence "A second AI
  model tries to find what is wrong with the plan before you approve it."
- Its default follows the goal's autonomy, chosen in the same panel: on for *ask*, off for *standard* and *full*.
  Changing the autonomy changes the switch until the operator sets it by hand.
- An operator can set their own default in Settings, under the planner defaults: "Follow the autonomy" (the
  default), "Always" or "Never".
- It is shown only for a goal that is planned: a spec, a description, or issues with "Plan them together first".
  A goal that runs issues as they are has no plan to review.
- The panel's folded summary names it when on: "Ask before each step · adversarial plan review · budget $40".
- The goal page's settings show the same switch. Turning it on while a plan waits for approval reviews that plan
  at once.

### The review

- The review is a task of type `plan_review`, run like a planning task: read-only clones of the repos the
  planning task read, the goal's statement and spec, the plan version, and the planning task's findings about the
  code. It changes no file and publishes nothing.
- Its model is the first in `planning.adversarial_review.reviewers` whose model family differs from the model
  that wrote the plan. With none that differs, the review is not run, and the plan says so; the service checks at
  startup that the list holds at least two families.
- It is told its job is to find what is wrong, not to approve. Its prompt lists what to look for:
  - a step the goal needs that no draft does;
  - drafts in an order their dependencies cannot follow, or that edit the same code at once;
  - a draft too big for one pull request, or two that should be one;
  - a draft in the wrong repo;
  - an acceptance check that would pass without the work being right, or none that tests the goal itself;
  - a risk the plan does not handle: data loss, a breaking change, a migration, a security exposure;
  - a part of the spec no draft covers, or a draft that does more than the spec asks.
- It records findings with a tool, `submit_plan_review`, not as prose. Each finding has:

  | Field | Meaning |
  |---|---|
  | `kind` | One of `missing_step`, `order`, `size`, `repo`, `check`, `risk`, `scope` |
  | `severity` | `blocking`: the plan will fail or do harm as written. `advice`: it would be better another way |
  | `drafts` | The draft ids it is about; empty for the plan as a whole |
  | `says` | What is wrong, in one or two sentences, with the file or spec section that shows it |
  | `change` | The change to the plan that would settle it |

- A review that finds nothing says so, and that is a result: "No problems found".

### Revising

- When a round has blocking findings, a planning task revises the plan with the findings as its note. The
  revision is a new plan version with source `reviewed`. Advice is passed along too, and the planner takes it
  where it agrees.
- The reviewer then reviews the revision. It sees its earlier findings and says of each whether it is now fixed.
- After `max_rounds` reviews (default 2), or when a round has no blocking finding, review stops. What is still
  open is the operator's.
- The planner may disagree with a finding. It says why in the revision, and the finding stays open with the
  planner's answer under it, for the operator to settle.
- While adversarial review is on for a goal, the existing gap-sorting suggestion (`suggestions.py`) is not run
  for it: the review does that job and more. Goals without it keep the suggestion as today.

### When the plan changes by hand

- An operator's edit makes a new version (`edited`), as now. It is not reviewed by itself. The plan panel offers
  "Review again".
- "Plan again" makes a new planned version, which is reviewed again from the first round.

### Approving

- Approve stays one click. While a review is running, the button reads "Approve without the review".
- With open blocking findings, it reads "Approve with 2 open findings". Nothing else changes about approval.
- Accepting or dismissing a single finding is possible: "Fix this" sends that finding alone to a revision; "Not a
  problem" closes it, with who closed it.

### Spend

- The review and its revisions are planning work: their cost counts as planning in the goal's costs and on the
  Performance page, within the goal's budget.
- `planning.adversarial_review.max_usd` caps one review task, as `planning.max_usd` caps a planning task.
- When the goal's budget cannot cover another round, the review stops there and says so. The plan stays
  approvable.

## Screens

| Screen | File | After |
|---|---|---|
| New goal, panel 4 | `goals/NewGoal.tsx` | The switch, its default from the autonomy and the operator's setting, and its words in the folded summary |
| Settings, planner defaults | `settings/SettingsPage.tsx` | "Adversarial plan review": Follow the autonomy, Always or Never |
| Goal page, settings | `goals/GoalFields.tsx` | The switch; turning it on while a plan waits starts a review |
| Planner panel | `goals/PlannerUpdates.tsx` | The steps below, in the planner's usual voice |
| Plan | `goals/GoalPlan.tsx`, `goals/PlanReview.tsx` | The review summary, findings on drafts, change marks, and the approve button's words |
| What needs the operator | `planner/actions.py`, `planner/needs.py` | The need below |

### The planner panel

The planner's panel already narrates planning. It gains these steps, each one line:

| When | Line |
|---|---|
| A review starts | "Getting GPT-6 Sol to pick holes in the plan." |
| A round ends with blocking findings | "It found 4 problems, 2 serious. Fixing those." |
| A revision is saved | "Version 2 is up, with those fixed." |
| A round finds nothing that blocks | "Second look done: nothing serious. 1 suggestion for you." |
| Review stops with blocking findings open | "Two rounds in, 1 serious problem is still open. Your call." |
| Review is skipped | "Skipped the second look: no reviewer from another model family is set up." |

The model's name is the reviewer's display name from the router's catalog. While a review runs the panel shows
the same progress indicator as planning.

### The plan

- Above the drafts, a review summary: "Reviewed by GPT-6 Sol · 2 rounds · 5 found, 4 fixed, 1 open", which
  expands to every finding, grouped by status.
- The version line names it: "Version 2, revised after the adversarial review".
- Each draft card shows its findings under it: fixed ones folded to one line with a check mark, open ones
  expanded, blocking ones marked as such. A finding about the whole plan sits in the summary.
- A draft the review changed is marked: "new", "split", "merged", "moved to <repo>", "reordered" or "check
  changed", against the version before the review. "Show changes" shows the previous version beside it.
- Each open finding has "Fix this" and "Not a problem".

### What needs the operator

A new action in `planner/actions.py`, `settle_review`, with its sentence and `ASKS` label, served through
`GET /v1/needs`: "The plan has 1 serious problem the review found and the planner could not fix." Its button
opens the plan at the first open finding. It replaces the plan's approval need while it is open, and approval
stays on the plan itself.

## Data and settings

| Field | Where | Default | Existing rows |
|---|---|---|---|
| `goal.adversarial_review` | `boolean`, nullable | From New goal; null means "follow the operator's setting and the autonomy" | Null: off for existing goals, since none is `ask` by this switch |
| `planner.adversarial_review` | user setting | `"follow"`; also `"always"`, `"never"` | `"follow"` |
| `plan.source` | existing | Gains `reviewed` | Unchanged |
| `planner.plan_review` | new table: goal id, plan version, round, task id, reviewer model, findings (`jsonb`), state, cost, started and ended at | One row per round | None |
| Finding status | inside `findings` | `open`; then `fixed`, `disputed` or `dismissed` (with who) | n/a |
| Task type `plan_review` | `tasks/store.py` `TASK_TYPES`, `CHECKLESS_TASK_TYPES` | Runs on the reviewer's harness profile | n/a |

Events, each a new type at version 1: `plan_review_started`, `plan_review_finished` (round, counts by severity
and status, cost), `plan_finding_settled` (finding, how, by whom).

Settings in `config/planner.json`, under `planning`, checked at startup:

| Setting | Default | A bad or missing value |
|---|---|---|
| `adversarial_review.reviewers` | `["harness:pi-plan-review/openai-responses:gpt-6-sol", "harness:pi-plan-review/claude:claude-opus-5-5"]` | Fewer than two model families, or a name the router does not know: stops the service, naming it |
| `adversarial_review.max_rounds` | 2 | Falls back to the default through `fallback.py` |
| `adversarial_review.max_usd` | `"2.00"` | Stops the service: a wrong value could cost money |

The harness profile `pi-plan-review` is added to `config/harness_profiles.json`: read-only, the
`submit_plan_review` tool, no write tools. The tool is added to the pi model client extension beside
`update_plan` and `create_issue`.

## Services

| Area | File | Change |
|---|---|---|
| Starting a review | new `planner/plan_review.py` | After a planned or reviewed version is saved on a goal with review on: pick the reviewer, start the task, record the round |
| Ending a round | `planner/plan_review.py` | Reads the task's findings, decides whether to revise, starts the revision with the findings as its note, or stops |
| Planning | `planner/planning.py` | A revision is a planning task like a replan, whose plan is saved with source `reviewed`. Its prompt says to answer each blocking finding |
| Suggestions | `planner/suggestions.py` | Not run for a goal with review on |
| Plan API | `planner/plan_api.py` | `POST /v1/goals/{id}/plan/review` (review again), `POST /v1/goals/{id}/plan/findings/{n}` (fix this, not a problem); the plan's GET includes its review rounds and findings |
| Goal create and edit | `planner/service.py`, `planner/store.py` | `adversarial_review` on the goal |
| User settings | `planner/settings.py` | `adversarial_review` in the planner defaults |
| Runs | `run_service`, `extension/`, `config/harness_profiles.json` | The `plan_review` task type and its profile. If the RunTask workflow branches on the new type, the branch goes behind `workflow.patched("plan-review")` with a recorded history |
| Planner voice | `planner/updates.py` | The panel's lines above, through the writing pass |
| Costs | `costs/model_source.py` | A `plan_review` task's cost is a planning cost |
| Design doc | `docs/technical-design.md` | The Planner section's planning and suggestion paragraphs, the task types, the console's New goal and plan text |

Every migration only adds. No existing event type changes.

## Tests

- No test calls a model. The review is tested with a fake task result holding findings.
- A plan with a blocking finding is revised, reviewed again and stops at `max_rounds`; one with only advice is not
  revised; one with none says "No problems found".
- The reviewer is never the plan's own model family; with no other family the review is skipped and says why.
- The default follows the autonomy and the operator's setting; a goal that runs issues as they are offers no
  switch.
- A goal with review on gets no gap-sorting suggestion; one without keeps it.
- Console tests cover the switch's default, the panel's lines, findings on draft cards, change marks and the
  approve button's words.
- The zero-cost end-to-end check gains a planned goal with review on, against the fake HTTP Gateway.

## Decided (2026-11-24)

1. It is called adversarial plan review.
2. It is an option on a goal, on by default when the goal's autonomy is *ask*.
3. The reviewer is always a different model family from the planner.
4. Approval stays the operator's, one click, whatever the review found.
