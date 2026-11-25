# Adversarial plan review

Status: approved in outline by the owner on 2026-11-24, reviewed against the code on 2026-11-25. Muxxy builds
it as a goal from this file, after `docs/specs/planner-redesign.md`, whose Plan page and plan-change model it
uses.

## Why

A single model writes a plan, and it misses its own mistakes: a step it never thought of, an order that cannot work,
a task too big for one pull request, an acceptance check that would pass without the goal being met. Each pull
request already gets a reviewer that is a different model from the one that wrote it. A plan, which decides
every pull request after it, gets no such reviewer.

Adversarial plan review gives a plan that reviewer. A second model, from a different family, reads the same
repos and the plan, and looks for what is wrong with it. The planner fixes what it can. Before approving, the
operator sees what the reviewer found, what changed and what is still open.

It is an option on each goal, because it costs a planning run or two and some minutes before work starts. It
is on by default when the goal's autonomy is *ask*: an operator who chose to decide every step has chosen care
over speed. Every plan needs the operator's approval whatever the autonomy, and the review makes that approval
better informed.

## What happens today

- A planning task writes a plan version (`source: planned`) and the operator approves it (`planner/planning.py`,
  `planner/plan_api.py`).
- After the planner saves a version, one small call on the updates model sorts the gaps the planner listed
  itself. When a change to the plan would settle some, a second planning task writes a revised plan. The
  operator accepts it as a suggestion with one click (`planner/suggestions.py`, `suggest_fixes` in
  `config/planner.json`).
- That check reads only the planner's own list of gaps. Nothing independent reads the repos and challenges the
  plan.
- A planning task that ends at a limit wastes its work. Its tasks survive as a plan the operator cannot
  approve (`planning.gather`), but the next planning task's prompt holds only their titles (`planning.prompt`),
  so it writes every task again.

## What a failed plan showed

On 2026-11-24 the first plan for the goal "Goals that span multiple repositories" (a 27,000-character spec)
failed. Its events show:

- It recorded 44 tasks, one `create_issue` call a turn, and ended at the planning profile's 60-turn limit with
  the plan unfinished. It had also spent $3.04 against a $3.00 limit.
- The 44 tasks became a plan the operator could not approve. A second planning task got their titles only,
  wrote every task again and paid for the same reading twice.
- The second task recorded 42 tasks in 27 turns, because it happened to make several tool calls a turn.

A review adds runs to planning: a review, then a revision, then another review. On a plan that size each would
meet the same limits, so this spec first fixes what that failure showed. The rules below apply to planning,
revision and review runs alike.

## The flow

1. The planner writes the plan, as now.
2. A plan reviewer, a different model, reads the goal, its spec, the repos and the plan, and records findings.
3. When a finding blocks, the planner revises the plan with the findings as its note, and the reviewer reviews
   the revision. This repeats at most `max_rounds` times. All of it happens before the page asks the
   operator anything.
4. The operator opens the Plan page to one decision. It says what the review found, what the planner fixed and
   what is still open, as counts, with the open problems in full.
5. The operator approves, has the planner fix what is open, or dismisses it.

## What we want

### Choosing it on a goal

- New goal, panel 4 ("How it runs"), gains a switch: "Adversarial plan review", with the sentence "A second AI
  model tries to find what is wrong with the plan before you approve it. Adds a few minutes and up to $4."
- Its default follows the goal's autonomy, chosen in the same panel: on for *ask*, off for *standard* and *full*.
  Changing the autonomy changes the switch until the operator sets it by hand.
- An operator can set their own default in Settings, under the planner defaults: "Follow the autonomy" (the
  default), "Always" or "Never".
- The service works out the default, so a goal created through the API gets the same one as a goal created in
  the console.
- New goal shows the switch only for a goal the planner plans: a spec, a description, or issues with "Plan them
  together first". A goal that runs issues as they are has no plan to review.
- The panel's folded summary names it when on: "Ask before each step · adversarial plan review · budget $40".
- The goal page's settings show the same switch. Turning it on while a plan waits for approval reviews that plan
  at once.

### The review

- The review is a task of type `plan_review`, run like a planning task. It gets read-only clones of the repos the
  planning task read, the goal's statement and spec, the plan version, and the planner's own summary and gaps.
  It changes no file and publishes nothing.
- A review starts only on a settled plan: no planning run in flight, no resize round left, and nothing
  stopping approval.
- Its model is the first in `planning.adversarial_review.reviewers` whose model family differs from the model
  that wrote the plan. A model's family is the engine in its candidate name, `harness:<profile>/<engine>:<model>`. When none differs, the planner skips the review and the plan says so. The service checks
  at startup that the list holds at least two families.
- Its prompt tells it to find what is wrong with the plan, and lists what to look for:
  - a step the goal needs that no task does;
  - tasks in an order their dependencies cannot follow, or that edit the same code at once;
  - a task too big for one pull request, two that should be one, or a plan cut into more tasks than the work
    needs;
  - a task in the wrong repo;
  - an acceptance check that would pass without the work being right, or none that tests the goal itself;
  - a risk the plan does not handle: data loss, a breaking change, a migration, a security exposure;
  - a part of the spec no task covers, or a task that does more than the spec asks.
- It records every finding in one call of the `submit_plan_review` tool. A finding has:

  | Field | Meaning |
  |---|---|
  | `kind` | One of `missing_step`, `order`, `size`, `repo`, `check`, `risk`, `scope` |
  | `severity` | `blocking`: the plan will fail or do harm as written. `advice`: it would be better another way |
  | `tasks` | The ids of the tasks it is about; empty for the plan as a whole |
  | `says` | What is wrong, in one or two sentences, with the file or spec section that shows it |
  | `change` | The change to the plan that would settle it |

- The service gives each finding an id that stays the same across rounds, and stores it as a reason in the
  planner redesign's shape, with `source: review`.
- A review that finds nothing records "No problems found".

### The reviewer's instructions are a locked skill

- What the reviewer looks for, how it judges severity and how it words a finding live in a skill,
  `adversarial-plan-review`, shipped in `config/skills/` and named in `config/skills/locked.json`. The
  `pi-plan-review` profile loads it.
- The lock works as it does for `no-ai-slop2`: a pull request changes the skill, and a save or an import under
  its name fails.
  A deploy sets the stored copy to the shipped files.
- The operator can read it. Configuration, Skills lists it with the lock, and the plan's review summary links
  to it: "Reviewed with the adversarial-plan-review skill".
- Locking it keeps every review to the same standard, so two reviews of the same plan agree and a finding's
  severity means the same thing on every goal.

### Revising

- When a round has blocking findings, a planning run revises the plan with the findings as its note. It merges
  through the planner redesign's one function: it changes the tasks the findings name, and every other task
  moves into the result byte for byte. The planner also gets the advice, and takes what it agrees with.
- The planner sizes the revision as it sizes any plan.
- Before the operator has acted on the plan, a revision applies itself as a new version with source `reviewed`.
  After, it arrives as a proposal. The planner redesign sets that rule for every cause of change.
- The reviewer then reviews the revision. It sees its earlier findings and says of each whether it is now fixed.
- The review stops after `max_rounds` reviews (default 2), or when a round has no blocking finding. What is still
  open is the operator's.
- The planner may disagree with a finding. It says why in the revision, and the finding stays open as
  `disputed`, with the planner's answer under it, for the operator to settle.
- For a goal with adversarial review on, the planner skips the existing gap fix (`suggestions.py`), since the
  review covers it. Goals without review keep it.

### When the plan changes while a review runs

- **The operator approves.** "Approve now" on the Plan page cancels the running review or revision and approves
  the plan as it stands. The page shows it as a link while a review runs, since waiting is the recommended
  course.
- **The operator edits.** The edit makes a new version (`edited`), as now, and cancels the running review, whose
  findings would be about a plan that is gone. The Plan page offers "Review again (up to $4)".
- **The operator plans again.** The result is a proposal. The planner reviews it once the operator accepts it.
- A canceled review keeps the findings it had recorded, marked partial.

### Approving

The buttons are the Plan page's, and this spec adds none.

- With no open blocking finding: "Approve and start (up to $62)".
- With open blocking findings, in order: "Fix it (up to $4)", "Approve with 1 open (up to $62)", "Not a
  problem". With several, the first reads "Fix all 3 (up to $4)" and each finding's card has its own.
- "Fix it" sends the open findings to one more revision. "Not a problem" dismisses the finding, with who
  dismissed it.

### A run that ends at a limit keeps its work

- A planning, revision or review run that ends at its turn, time or spend limit keeps what it recorded. A plan
  records which limit stopped it (`plan.stopped_at`), and the operator cannot approve it. A review keeps its
  findings so far and marks its round partial.
- The next run continues from there. Its prompt holds the saved tasks in full, says which parts of the spec they
  cover, and asks for what is missing. It writes no task twice.
- The planner starts one continuation by itself. If that run also ends at a limit, the Plan page's Incomplete
  state asks the operator: "Carry on planning (up to $4)" or "Plan again (up to $4)".
- The plan never reports a partial review as clean. It says "The review stopped early: 3 findings so far, 12
  tasks not read", with "Carry on reviewing (up to $4)".

### Runs sized to the plan

- The planning and review prompts say to record in batches: several `create_issue` calls, or every finding, in
  one turn, and to batch reads the same way.
- The reviewer's prompt holds the whole plan as text, so the reviewer spends its turns reading the code.
- A run's turn limit grows with its input: the profile's `max_turns`, plus `planning.turns_per_draft` for each
  task in a review's or revision's prompt, plus `planning.turns_per_spec_thousand` for each 1,000 characters of
  spec in a planning run's prompt, up to `planning.max_turns_ceiling`.
- A run's spend limit does not grow.

### Spend

- The review and its revisions are planning work: their cost counts as planning in the goal's costs and on the
  Performance page.
- `planning.adversarial_review.max_usd` caps one review run, as `planning.max_usd` caps a planning run. Its
  default is the planning limit, because a reviewer reads the same code the planner did.
- Reviews and revisions count toward the planner redesign's limit on a goal's whole planning phase,
  `planning.max_phase_usd`. Before each run the planner checks that limit and the goal's remaining budget.
- When either cannot cover another run, the review stops there, the plan says so with the figures, and the plan
  stays approvable.

## The skill's text

The recommended content of `config/skills/adversarial-plan-review/SKILL.md`. The implementer may tighten its
wording, and keeps its tests, its severity rule and its limits on findings.

````markdown
---
name: adversarial-plan-review
description: "Review a goal's plan before any work starts, as a reviewer whose job is to find what will make it fail. Read the goal, its spec and the code, test the plan against them, and record each problem with submit_plan_review."
---

# Adversarial plan review

You are reviewing a plan another model wrote. The plan is a list of tasks. Each task becomes one pull
request in one repo. Your job is to find what will make this plan fail or do harm, before anyone pays
for the work. Assume the plan has problems and look for them. Do not rewrite the plan, and do not
praise it.

## What you have

- The goal and its spec, in your prompt.
- The plan: every task with its id, repo, title, description, acceptance checks and the tasks it waits
  for, in your prompt.
- Read-only clones of the repos, under `/workspace`.
- The planner's own summary and the gaps it listed. Do not repeat a gap it already listed.
- On a second round: your earlier findings, and what the planner did about each.

## How to work

1. Read the goal and the spec first, and write down for yourself what "done" means: the behaviors that
   must exist when every task has merged.
2. Read the plan as a whole before any task. Note its size and its order.
3. Read the code the plan touches. Batch your reads: several files or searches in one turn. Check the
   plan's claims about the code. A task that says "extend X" needs X to exist.
4. Run the seven tests below. Stop looking in a test once you have what you need for a finding.
5. Record everything in one `submit_plan_review` call, then stop.

## The seven tests

**Coverage.** Walk the spec section by section. For each requirement, name the task that delivers it.
A requirement with no task is a `missing_step`. A task that delivers nothing the spec asks for is `scope`.

**Order.** For each task, ask what must already be merged for its pull request to pass its checks. If
that work is in a task it does not wait for, that is `order`. Two tasks that edit the same files and do
not wait on each other are `order` too.

**Size.** A task should fit one reviewable pull request. One that touches many unrelated areas, or
needs a migration, a service change and a console change together, is `size`: say where to split it.
Also look the other way: tasks so small that several always ship together, or a plan with far more
tasks than the work needs, are `size`: say which to merge.

**Repo.** Check each task names the repo where its code lives. Look in the clone. A task in the wrong
repo is `repo`.

**Checks.** Read each task's acceptance checks. Ask whether they would still pass if someone did the task
wrong, or skipped it. If so, that is `check`: name a check that would fail. A plan in which no task tests
the goal's main behavior end to end is `check` on the plan as a whole.

**Risk.** Look for what the plan could break or expose: data lost or rewritten, a migration that
cannot run beside the old code, an API change with callers the plan does not update, a credential or
permission widened, a cost with no limit. A real risk the plan does not handle is `risk`.

**The repo's own rules.** Read each repo's `AGENTS.md` or contributor guide. A task that would break
one of its rules is the kind that fits: often `missing_step` (a required doc or test update) or `risk`.

## Severity

A finding is `blocking` only when the plan, built as written, would fail or do harm:

- the goal would not be met when every task merged;
- a task could not pass its checks, or could pass them while wrong;
- a task would break something that works, lose data, or open a security hole;
- the plan would spend well past its budget.

Everything else is `advice`: a better order, a cleaner split, a clearer description. If you are unsure,
it is `advice`. A plan can be approved with advice open.

## Writing a finding

- `says`: what is wrong, in one or two sentences, for a person deciding whether to approve. Name the
  file, function or spec section that shows it. State facts you checked. If you could not check
  something, say so.
- `change`: the one change to the plan that would settle it, concrete enough to act on: "Make task
  t-12 wait for t-7", "Add a task that migrates the existing rows", "Replace the check with one that
  calls the endpoint as a user with no access".
- `tasks`: the ids of the tasks it is about. Leave it empty only for a problem with the plan as a
  whole.
- One problem, one finding. Do not split one problem across tasks, and do not fold two into one.
- No finding about wording, style or naming.
- At most 12 findings. If you have more, keep every blocking one and the advice that matters most.

## On a second round

For each earlier finding, say whether the revision fixed it. If the planner disagreed, read its answer
and the code again. Keep the finding only if the answer is wrong, and say why. Then run the seven
tests on what changed. Do not raise as new a problem you could have raised in the first round unless
it blocks.

## When you find nothing

Say so: call `submit_plan_review` with no findings. Do not invent a finding to have one.
````

## Screens

| Screen | File | After |
|---|---|---|
| New goal, panel 4 | `goals/NewGoal.tsx` | The switch, its default from the autonomy and the operator's setting, and its words in the folded summary |
| Settings, planner defaults | `settings/SettingsPage.tsx` | "Adversarial plan review": Follow the autonomy, Always or Never |
| Goal page, settings | `goals/GoalFields.tsx` | The switch; turning it on while a plan waits starts a review |
| Planner panel | `goals/PlannerUpdates.tsx` | The steps below, in the planner's usual voice |
| Plan page | `console/src/features/plan/` (from the planner redesign) | The decision bar's sentence, the review tile, findings as reasons, open findings as cards |
| What needs the operator | `planner/actions.py`, `planner/needs.py` | The need below |

### The planner panel

The service writes these lines from counts, as the planner redesign specifies for every plan-progress line. No
model writes them. `<reviewer>` is the reviewer's model name as the configuration spells it.

| When | Line |
|---|---|
| A review starts | "Getting <reviewer> to pick holes in the plan." |
| A round ends with blocking findings | "It found 4 problems, 2 serious. Fixing those." |
| The planner saves a revision | "Version 2 is up, with those fixed." |
| A round finds nothing that blocks | "Second look done: nothing serious. 1 suggestion for you." |
| Review stops with blocking findings open | "Two rounds in, 1 serious problem is still open. Your call." |
| Review skipped | "Skipped the second look: the configuration names no reviewer from another model family." |
| A run ends at a limit | "Ran out of turns with 44 tasks drafted. Kept them, carrying on from there." |
| The continuation ends at a limit too | "Still not finished after two goes ($6.10). Carry on, or start over?" |

### The plan

The review appears on the Plan page that `docs/specs/planner-redesign.md` specifies, and follows that spec's
rules for reducing decision drag. It adds no screen and no list of its own.

- **While it runs** the page is in its Improving state: "<reviewer> is checking the plan."
- **Two sentences in the decision bar.** "The review found 5 problems. The planner fixed 4. 1 is open: t-12
  would run before the table it needs exists." The buttons follow, as under Approving.
- **One tile.** "Review: 5 found · 4 fixed · 1 open", beside the plan's other tiles. It names the reviewer and
  links to the skill it used.
- **Findings are reasons.** In "What changed", each finding the planner fixed is a row under "Why it changed":
  the problem in a few words, the fix beside it. The change counts follow, as they do for any change.
- **Only open findings show in full.** Each is a card under "Open questions": one sentence for what is wrong,
  one for the change that would settle it, the tasks it names as links, and "Fix it" and "Not a problem". The
  file or spec section that shows the problem opens from the card.
- **More than five open findings group by kind**, each kind with its count, folded: "Order: 4", "Checks: 3".
- **Fixed findings and advice are counts.** "4 fixed" and "2 suggestions" each open to their list.
- **Rounds fold into one result.** The operator sees the plan against its baseline, with every round's findings
  merged. History holds each round.
- **A task row shows a mark, never a finding's text.** The mark says how many open findings name the task, and
  opens them.

### What needs the operator

`planner/actions.py` already has `settle_review`, for a pull request's review. A plan's review gets its own
state action, `settle_plan_review`, decided from the goal's records like every plan state: "The plan has 1
serious problem the review found and the planner could not fix." Its `ASKS` label is "Settle the plan review".
On the goal page, the Overview and the needs list its button opens the Plan page at the decision bar, where
"Fix it" is one click.

The planner redesign supplies `continue_plan` for a plan or review left incomplete.

## Data and settings

| Field | Where | Default | Existing rows |
|---|---|---|---|
| `goal.adversarial_review` | `boolean NOT NULL DEFAULT false` | What the request sends. When it sends none, the service works it out from the creator's setting and the goal's autonomy | `false`: no existing goal changes |
| `planner.adversarial_review` | user setting | `"follow"`; also `"always"`, `"never"` | `"follow"` |
| `plan.source` | existing | Gains `reviewed`, recorded through `plan_saved` version 2, which the planner redesign registers | Unchanged |
| `plan.stopped_at` | from the planner redesign | The limit a run ended at | Null |
| `planner.plan_review` | new table: goal id, plan version, round, task id, reviewer model, state (`running`, `done`, `partial`, `canceled`, `skipped`), cost, started and ended at | One row per round | None |
| Findings | Stored as reasons with `source: review`, each with its id, round and status | `open`; then `fixed`, `disputed` or `dismissed` (with who) | n/a |
| Task type `plan_review` | `tasks/store.py` `TASK_TYPES`, `CHECKLESS_TASK_TYPES` | Runs on the reviewer's profile | n/a |

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

`config/harness_profiles.json` gains the profile `pi-plan-review`: read-only, the `submit_plan_review`
tool, no write tools. The pi model client extension gains that tool, beside `update_plan` and `create_issue`.

## Services

| Area | File | Change |
|---|---|---|
| Starting a review | new `planner/plan_review.py` | On a goal with review on, once a plan settles: pick the reviewer, start the task, record the round |
| Ending a round | `planner/plan_review.py` | Reads the task's findings, decides whether to revise, starts the revision with the findings as its note, or stops |
| Planning | `planner/planning.py`, `planner/plans.py`, `run_service/prompts/` | A revision is a planning task that saves its plan with source `reviewed`: the previous version's tasks, with the run's changes over them. A task that ended at a limit saves its tasks as an incomplete version, and one continuation starts from them. The prompts say to batch. A new prompt file beside `plan-v1.txt` |
| Turn limits | `run_service`, `planner/planning.py` | The run's turn limit worked out from the profile's base and the run's input |
| Suggestions | `planner/suggestions.py` | Skipped for a goal with review on |
| Plan API | `planner/plan_api.py` | `POST /v1/goals/{id}/plan/continue` (carry on planning or reviewing), `POST /v1/goals/{id}/plan/review` (review again), `POST /v1/goals/{id}/plan/findings/{n}` (the Fix this and Not a problem buttons); the plan's GET includes its review rounds and findings |
| Goal create and edit | `planner/service.py`, `planner/store.py` | `adversarial_review` on the goal |
| User settings | `planner/settings.py` | `adversarial_review` in the planner defaults |
| Reviewer skill | `config/skills/adversarial-plan-review/`, `config/skills/locked.json` | The locked skill the reviewer loads |
| Runs | `run_service`, `extension/`, `config/harness_profiles.json` | The `plan_review` task type and its profile. If the RunTask workflow branches on the new type, the branch goes behind `workflow.patched("plan-review")` with a recorded history |
| Planner voice | `planner/updates.py` | The panel's lines above, through the writing pass |
| Costs | `costs/model_source.py` | A `plan_review` task's cost is a planning cost |
| Design doc | `docs/technical-design.md` | The Planner section's planning and suggestion paragraphs, the task types, the console's New goal and plan text |

Every migration only adds. No existing event type changes.

## Tests

- No test calls a model. Tests feed the review a fake task result holding findings.
- The planner revises a plan with a blocking finding, the reviewer reviews it again, and review stops at
  `max_rounds`. A plan with only advice gets no revision. A review with no findings says "No problems found".
- A planning task that ends at its turn limit saves its tasks as an incomplete plan, which cannot be approved.
  A single continuation starts from them and tasks only what is missing. A second failure asks the operator.
- A revision copies untouched tasks into the new version unchanged.
- A review that ends early records its round as partial and never reads as "No problems found".
- A save that changes the `adversarial-plan-review` skill fails, and the Skills page shows it locked.
- A review starts only on a settled plan, and never while a resize round is running.
- Approving while a review runs cancels it. An edit cancels it and offers "Review again".
- A revision applies itself before the operator has acted on the plan, and is a proposal after.
- `actions.py` decides `settle_plan_review`, and the pull request action `settle_review` behaves as before.
- Reviews and revisions stop at the planning phase limit.
- The reviewer is never the plan's own model family. With no other family, the planner skips the review and says
  why.
- The default follows the autonomy and the operator's setting. A goal that runs issues as they are offers no
  switch.
- A goal with review on gets no gap-sorting suggestion; one without keeps it.
- Console tests cover the switch's default, the panel's lines, findings on task cards, change marks and the
  approve button's words.
- The zero-cost end-to-end check gains a planned goal with review on, against the fake HTTP Gateway.

## Decided (2026-11-24 and 2026-11-25)

1. Its name is adversarial plan review.
2. It is an option on a goal, on by default when the goal's autonomy is *ask*.
3. The reviewer is always a different model family from the planner.
4. Approval stays the operator's, one click, whatever the review found.
5. No run of planning or review throws away what it recorded when it ends at a limit.
6. The review, its revisions and its display use the planner redesign's proposals, reasons and Plan page, and
   add none of their own.
