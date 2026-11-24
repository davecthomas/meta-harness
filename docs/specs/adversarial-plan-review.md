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
- A planning task that ends at a limit loses its work. Its drafts are recorded as events as it goes, but a task
  that failed "saves a plan with no drafts and the reason" (`planning.py`), and the next planning task starts
  from nothing.

## What a failed plan showed

On 2026-11-24 the first plan for the goal "Goals that span multiple repositories" (a 27,000-character spec)
failed. The facts, from its events:

- It recorded 44 drafts, one `create_issue` call a turn, and ended at the planning profile's 60-turn limit with
  the plan unfinished. It had also spent $3.04 against a $3.00 limit.
- All 44 drafts were discarded. A second planning task started over and paid for the same reading again.
- The second task recorded 42 drafts in 27 turns, because it happened to make several tool calls a turn.

A review adds runs to planning: a review, then a revision, then another review. Each would meet the same limits
on a plan that size, so this spec does not add them without fixing what that failure showed. The rules below
apply to planning, revision and review runs alike.

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
  planning task read, the goal's statement and spec, the plan version, and the planner's own summary and gaps. It changes no file and publishes nothing.
- Its model is the first in `planning.adversarial_review.reviewers` whose model family differs from the model
  that wrote the plan. With none that differs, the review is not run, and the plan says so; the service checks at
  startup that the list holds at least two families.
- It is told its job is to find what is wrong, not to approve. Its prompt lists what to look for:
  - a step the goal needs that no draft does;
  - drafts in an order their dependencies cannot follow, or that edit the same code at once;
  - a draft too big for one pull request, two that should be one, or a plan cut into more tasks than the work
    needs;
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

### A run that ends at a limit keeps its work

- A planning, revision or review run that ends at its turn, time or spend limit keeps what it recorded. For a
  plan: the drafts so far are saved as a plan version marked incomplete, which cannot be approved. For a review:
  the findings so far are kept, and the round is marked partial.
- The next run carries on from there. It is given the saved drafts in full, told which parts of the spec they
  cover, and asked for what is missing. It does not draft again what is already drafted.
- This happens once by itself. If the second run also ends at a limit, the plan stays incomplete and is the
  operator's: "Carry on planning" or "Plan again from the start", each a button, with what the two runs cost.
- A partial review is never reported as a clean one. The plan says "Review stopped early: 3 findings so far, the
  drafts after <draft> were not read", with "Carry on reviewing".

### Runs sized to the plan

- The planning and review prompts say to record in batches: several `create_issue` calls, or every finding, in
  one turn. Reading is batched the same way.
- A revision changes only what the findings name. It is given the whole plan and the findings, calls
  `create_issue` with an existing `draft_id` to change a draft and a new one to add a draft, and records which
  drafts it removes. Every other draft is carried into the new version untouched. A revision never rewrites the
  plan.
- The reviewer is given the plan as text in its prompt, not as issues to look up one by one, so its turns go to
  reading the code.
- A run's turn limit grows with what it is given: the profile's `max_turns` is the base, plus
  `planning.turns_per_draft` for each draft a review or revision is given, and
  `planning.turns_per_spec_thousand` for each 1,000 characters of spec a planning run is given, up to
  `planning.max_turns_ceiling`.
- A run's spend limit does not grow. Each run, a continuation included, has its own, and the goal's budget
  bounds them together.
- Before a review starts, its cost is estimated from the planning run's own cost. If the goal's budget or the
  review's limit cannot cover it, the review is not started, and the plan says so with the figure.

### Spend

- The review and its revisions are planning work: their cost counts as planning in the goal's costs and on the
  Performance page, within the goal's budget.
- `planning.adversarial_review.max_usd` caps one review task, as `planning.max_usd` caps a planning task. Its
  default is the planning limit: a reviewer reads the same code the planner did.
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
| A run ends at a limit | "Ran out of turns with 44 tasks drafted. Kept them, carrying on from there." |
| The continuation ends at a limit too | "Still not finished after two goes ($6.10). Want me to carry on or start over?" |

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

A second action, `continue_plan`, for a plan or review left incomplete after its one continuation: "The plan
is not finished: planning stopped at its limit twice." Its buttons are "Carry on planning" and "Plan again".

## Data and settings

| Field | Where | Default | Existing rows |
|---|---|---|---|
| `goal.adversarial_review` | `boolean NOT NULL DEFAULT false` | What New goal sends. The console works out the preselection from the operator's setting and the autonomy; a goal created through the API without it gets `false` | `false`: no existing goal changes |
| `planner.adversarial_review` | user setting | `"follow"`; also `"always"`, `"never"` | `"follow"` |
| `plan.source` | existing | Gains `reviewed` | Unchanged |
| `plan.incomplete` | `boolean NOT NULL DEFAULT false` | True for a version saved from a run that ended at a limit. It cannot be approved | `false` |
| `planner.plan_review` | new table: goal id, plan version, round, task id, reviewer model, findings (`jsonb`), state (`running`, `done`, `partial`, `skipped`), cost, started and ended at | One row per round | None |
| Finding status | inside `findings` | `open`; then `fixed`, `disputed` or `dismissed` (with who) | n/a |
| Task type `plan_review` | `tasks/store.py` `TASK_TYPES`, `CHECKLESS_TASK_TYPES` | Runs on the reviewer's harness profile | n/a |

Events, each a new type at version 1: `plan_review_started`, `plan_review_finished` (round, counts by severity
and status, cost), `plan_finding_settled` (finding, how, by whom).

Settings in `config/planner.json`, under `planning`, checked at startup:

| Setting | Default | A bad or missing value |
|---|---|---|
| `adversarial_review.reviewers` | `["harness:pi-plan-review/openai-responses:gpt-6-sol", "harness:pi-plan-review/claude:claude-opus-5-5"]` | Fewer than two model families, or a name the router does not know: stops the service, naming it |
| `adversarial_review.max_rounds` | 2 | Falls back to the default through `fallback.py` |
| `adversarial_review.max_usd` | The value of `planning.max_usd` | Stops the service: a wrong value could cost money |
| `turns_per_draft` | 1 | Falls back to the default |
| `turns_per_spec_thousand` | 1 | Falls back to the default |
| `max_turns_ceiling` | 150 | Falls back to the default |

The harness profile `pi-plan-review` is added to `config/harness_profiles.json`: read-only, the
`submit_plan_review` tool, no write tools. The tool is added to the pi model client extension beside
`update_plan` and `create_issue`.

## Services

| Area | File | Change |
|---|---|---|
| Starting a review | new `planner/plan_review.py` | After a planned or reviewed version is saved on a goal with review on: pick the reviewer, start the task, record the round |
| Ending a round | `planner/plan_review.py` | Reads the task's findings, decides whether to revise, starts the revision with the findings as its note, or stops |
| Planning | `planner/planning.py`, `planner/plans.py`, `run_service/prompts/` | A revision is a planning task whose plan is saved with source `reviewed`: the previous version's drafts, with the run's changes over them. A task that ended at a limit saves its drafts as an incomplete version, and one continuation starts from them. The prompts say to batch. A new prompt file beside `plan-v1.txt` |
| Turn limits | `run_service`, `planner/planning.py` | The run's turn limit worked out from the profile's base and what the run is given |
| Suggestions | `planner/suggestions.py` | Not run for a goal with review on |
| Plan API | `planner/plan_api.py` | `POST /v1/goals/{id}/plan/continue` (carry on planning or reviewing), `POST /v1/goals/{id}/plan/review` (review again), `POST /v1/goals/{id}/plan/findings/{n}` (fix this, not a problem); the plan's GET includes its review rounds and findings |
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
- A planning task that ends at its turn limit saves its drafts as an incomplete plan, which cannot be approved; one
  continuation starts from them and drafts only what is missing; a second failure asks the operator.
- A revision carries untouched drafts into the new version unchanged.
- A review that ends early is marked partial and never reads as "No problems found".
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
5. No run of planning or review throws away what it recorded when it ends at a limit.
