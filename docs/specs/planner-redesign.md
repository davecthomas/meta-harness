# Planner redesign: a Plan page

Status: asked for by the owner on 2026-11-25, reviewed against the code the same day. Muxxy builds it as a goal
from this file.

## Why

On 2026-11-25 the goal page for "Goals that span multiple repositories" asked the operator to accept a fix to
plan version 3. The fix settled two gaps. The ask showed them as one paragraph: two added tasks, then a line
for nearly every other task in the plan, joined by dots. Nobody reads that, so nobody can decide from it.

The page fails the operator in four ways:

- **It lists where it should count.** `suggestions.changes()` writes one line for each task whose text differs at
  all, and the console joins the lines into a paragraph.
- **The fix rewrote the plan.** The planner asked the revising run to settle two gaps and "change nothing else".
  The run still rewrote most tasks, so most of the list is noise.
- **The plan has no place of its own.** The goal page holds it in three spots: the planner's update, the "Needs
  you" box inside that update, and "The plan" section under it. Each has its own buttons.
- **It asks for a decision nobody needs to make.** The operator had not read version 3 yet. Asking them to
  accept a fix to it, and then to approve the result, is two decisions where one will do.

## A new first principle: reduce decision drag

The design's first principles and `config/charter.txt` gain this one:

> **Reduce decision drag.** Everything shown to a person helps them decide, and costs them as little reading as
> the decision allows.

It has rules a page can be checked against:

1. **The decision comes first.** The top of the page says what the operator is deciding, in two sentences at
   most, beside the buttons that decide it.
2. **Ask once.** Work the planner can do before the operator looks, it does. Two asks that always follow each
   other are one ask.
3. **Count before listing.** A set of more than five things appears as a count with a name ("38 tasks
   reworded"). The list is one click away.
4. **Structure before prose.** Things that have parts appear as tiles, groups, tables and chips. A paragraph
   holds one idea.
5. **Say what matters, in order.** What changes the outcome comes first: scope, cost, risk, order. Wording
   changes come last.
6. **One place for one thing.** A plan, its changes and its approval live on one page. Other pages link there.
7. **Detail on request.** Every summary opens to its full detail, so the page hides nothing and forces nothing.

It works with the sixth principle, which already says an ask takes two lines and offers its actions as buttons,
best first, each saying what it may spend.

A console test holds every planner ask to rule 1: before its first button an ask renders at most two sentences
and 280 characters, and no list.

## One way a plan changes

Today a plan changes in three unrelated ways: a gap fix is a `plan_suggestion` row with its own accept call, a
replan saves a new latest version at once, and a resize round does the same. This spec gives them one model.

### Who may change a plan without asking

- **Before the operator has acted on a plan**, the planner's own improvements apply themselves: a gap fix, a
  resize, an adversarial review's revision. Each saves a new version. The operator opens the page to one plan
  and one decision, approve it, and "What changed" says what the planner did since its first draft and why.
- **After the operator has acted**, by editing a task, answering a question or approving, nothing changes the
  plan without them. Every later change arrives as a proposal.
- **A replan the operator asks for** is always a proposal, so the plan they have stays until they accept the new
  one.

The operator can undo any of it: History offers "Use this version" on every earlier version.

### Proposals

A proposal is a full set of the plan's tasks that is not the plan yet. A goal has at most one open proposal.

| Field | Meaning |
|---|---|
| `cause` | `gap_fix`, `review`, `resize` or `replan` |
| `for_version` | The plan version it would replace |
| `tasks`, `items`, `summary`, `gaps` | What the plan would hold |
| `reasons` | Why, in the shape below |
| `status` | `open`, `accepted`, `discarded`, or `stale` when the plan moved under it |

`planner.plan_proposal` replaces `planner.plan_suggestion`. The old table stays, unread, until a later pull
request drops it. `POST /v1/goals/{id}/plan/proposal/accept` and `.../discard` replace the suggestion's accept
and dismiss calls, which keep working for a release and answer through the new ones.

### A run changes only what it names

A revising run of any cause starts from the previous tasks and records its changes over them:

- `create_issue` with an existing id changes that task. With a new id it adds one.
- A new planning tool, `remove_issue`, removes a task, and records an `issue_removed` event.
- A task the run did not name moves into the result byte for byte. So do the plan's `items`, `summary` and
  `gaps`, unless the run records them again.
- The run's prompt holds the previous tasks in full. Today it holds their titles only (`planning.py`), which is
  why a revising run rewrites them.

Resize rounds (`draft_sizes.py`), gap fixes, review revisions and replans all merge this way, through one
function.

### Reasons

Every cause yields reasons, in one shape, and the page draws them the same way whatever their cause.

| Field | Meaning |
|---|---|
| `id` | Stable across rounds and versions |
| `source` | `gap`, `review`, `size` or `operator` |
| `kind` | For a review finding, its kind; otherwise empty |
| `says` | What was wrong, in one sentence |
| `change` | What settles it, in one sentence |
| `tasks` | The ids of the tasks it names |
| `severity` | `blocking` or `advice`; a gap, a size problem and an operator's note are `blocking` |
| `status` | `fixed`, `open`, `disputed` or `dismissed` |
| `evidence` | Where to look: a file, a spec section, the planner's answer |

| Status | Where it appears | How |
|---|---|---|
| `fixed` | "What changed", under "Why it changed" | One row; past five, a count that opens |
| `open` and `blocking` | The decision bar, and a card under "Open questions" | In full, with its buttons |
| `open` and `advice` | A count under "Open questions" | Opens to its list |
| `disputed` | A card under "Open questions" | With the planner's answer beside it |
| `dismissed` | History | Who dismissed it |

### The baseline of a comparison

"What changed" compares the plan with its baseline: the last version an operator made, accepted or approved. A
plan no operator has acted on has the planner's first version as its baseline. The plan stores its baseline
version, so every operator sees the same comparison.

## The Plan page

A page of its own at `/goals/<id>/plan`. The goal page links to it and no longer holds the plan.

| Part | What it shows |
|---|---|
| Header | The goal's title, the plan's version and state, and what planning has cost against its limit |
| Decision bar | The one thing the operator is deciding now, in two sentences, with its buttons, best first |
| At a glance | Tiles: tasks, repos, the most the plan may spend against the budget, how many tasks can start at once, open questions |
| What changed | Only when the plan differs from its baseline, or a proposal is open. See below |
| Open questions | What only the operator can settle, as cards. See below |
| Tasks | The plan's tasks in groups, folded. See below |
| History | The plan's versions as a timeline: who or what made each, when, its change counts, and "Use this version" |

On a wide screen the decision bar stays at the top while the page scrolls. On a narrow one it stays at the
foot. `#decision` and `?task=<id>` link into the page, and the browser's Back button returns to where the
operator came from.

### States and their buttons

`planner/actions.py` decides the plan's state and its decision, from the goal's records, as it decides every
need. The Plan page draws that answer and decides nothing itself, so the page and `GET /v1/needs` cannot
disagree. The first button is the recommended one.

| State | The decision bar says | Buttons, in order |
|---|---|---|
| Drafting | "The planner is writing the plan. 12 tasks so far, $1.40 of $4." | "Stop planning", which keeps the tasks drafted so far |
| Improving | "The planner is fixing 2 gaps it found." or "gpt-6-sol is checking the plan." | "Approve now", which cancels the run; shown as a link, since waiting is the recommended course |
| Ready to approve | "44 tasks in 1 repo. Running them may spend up to $62 of your $100 budget." | "Approve and start (up to $62)", "Plan again" |
| Ready, with open problems | "The review found 5 problems. The planner fixed 4. 1 is open: t-12 would run before the table it needs exists." | "Fix it (up to $4)", "Approve with 1 open (up to $62)", "Not a problem" |
| Proposal waiting | "Your replan is ready: 3 tasks added, 9 removed. It may spend up to $48, down from $62." | "Use the new plan", "Keep the current plan" |
| Cannot be approved | The first reason, and how many more: "t-9 is too large for one pull request, and 2 more." | The button that fixes the most reasons: "Split the 3 large tasks (up to $4)", then "Plan again (up to $4)" |
| Incomplete | "Planning stopped at its 60-turn limit with 44 tasks drafted, after one more try." | "Carry on planning (up to $4)", "Plan again (up to $4)" |
| Planning limit reached | "Planning has spent $12 of its $12 limit." | "Raise the limit to $16", "Approve what is there", when it can be approved |
| Approved | "Approved by you on Nov 25. 12 of 44 tasks done." | "See the tasks", "Plan again (up to $4)" |
| No plan | "This goal runs its issues as they are, so it has no plan." | "Plan them together (up to $4)" |

Rules for the buttons, which the console tests:

- A button that spends says the most it may spend.
- One action has one name on every page: "Approve and start", "Plan again", "Carry on planning", "Fix it",
  "Use the new plan", "Stop planning".
- No button's action is to read. "See the tasks" appears only in a state that asks nothing.
- With more than one open problem, the first button is "Fix all 3 (up to $4)" and each card has its own.
- "Approve and start" approves the plan, creates its issues and starts the goal, as approval does today.

### What changed

- **Why it changed** comes first: one row for each fixed reason, the problem in a few words and the fix beside
  it. For the fix that prompted this spec that is two rows.
- **Counts** come next, as chips: "2 added", "9 reordered", "3 changed", "38 reworded". A chip with a count of
  zero does not appear.
- **A chip opens its list.** Each entry is a task's title. A changed or reworded task opens to its old and new
  text side by side.
- **Cost** comes last, as one line: "The plan may now spend up to $66, up from $62."

| Kind | Meaning |
|---|---|
| Added | A task the plan did not have |
| Removed | A task the plan no longer has |
| Reordered | A task that now waits for a different set of tasks |
| Changed | A task whose repo, acceptance checks or size changed |
| Reworded | A task whose title or description changed and nothing else |

### Open questions

A plan's gaps are plain strings today, so the operator can read them and do nothing with them. Each becomes a
reason with `source: gap`, and each open one is a card:

- the question, in one sentence, with the tasks it touches;
- a short answer field, with "Send to the planner (up to $4)", which makes a proposal from that answer alone;
- "Not a problem", which dismisses it.

Open blocking review findings are cards here too, with "Fix it" and "Not a problem".

### Tasks

- The page groups tasks by stage: those that can start now, then those that wait on them. The operator can
  switch to grouping by repo.
- A group shows its name, its task count and the most it may spend, folded.
- A task row shows its title, size, repo and what it waits for. It opens to its full text and acceptance checks,
  with Edit and Remove.
- Marks on a row say what changed about it against the baseline, and how many open problems name it.
- "Add a task" sits at the foot of each group.

Where the figures come from:

| Figure | Source |
|---|---|
| A task's size | `draft_sizes.py` sizes each task. The plan now stores it on the task |
| The most a task may spend | The task policy's `max_usd_by_size` for its size |
| The most the plan may spend | The sum over its tasks, shown as "up to", never as an estimate |
| Tasks that can start at once | Tasks that wait for nothing, capped at the goal's parallel limit |
| Hand-edited | A task now records who last edited it, the planner or an operator |

### Plan again

"Plan again" opens a form on the Plan page.

| Field | Default | Purpose |
|---|---|---|
| What should change | Empty. Chips fill it: too many tasks, wrong order, missing work, costs too much | The note the planner works from. Required |
| Keep | Every task an operator edited, and every task whose issue already has a run | Tasks the planner must leave as they are. The operator ticks more in the task list |
| Scope | The whole plan | Or only the ticked groups |

Its button reads "Plan again (up to $4)": the planning limit, which the form cannot raise. While the run goes
the page is Drafting, and the current plan stays in force. The result is a proposal.

### After approval

- Approving creates the plan's issues, as now. A proposal accepted after that changes them: an added task
  becomes a new issue; a changed task's issue gets its new text; a removed task's issue closes with a comment,
  through `outbound.prepare`.
- A task whose issue already has a run is always kept. The planner cannot change or remove it, and the Keep
  field shows it ticked and locked.

## The goal page

- The planner's panel keeps its headline and one or two sentences. Under them sits the plan's decision, drawn
  by the shared `NeedButton`: its sentence and its first button, named for the decision ("Approve and start").
  The button opens the Plan page at its decision bar.
- The "Needs you" box no longer lists a plan's changes.
- "The plan" section moves to the Plan page. The goal page shows the plan's state and task count in its place,
  as a link.
- The Overview and the needs list show a plan need the same way.
- Old links keep working: `?do=approve` and `?do=replan` on a goal's address open the Plan page.

## What the planner writes

- The plan-progress lines in the planner's panel are fixed sentences the service fills with counts. No model
  writes them, and they do not count against a goal's update limit.
- The update model no longer gets a fix's change lines as a fact (`updates.py`). It gets the counts by kind and
  the reasons.
- An update about a plan leads with the decision and names no task, unless the operator must act on that task.

## Spend

- Every planning run has the planning limit, `planning.max_usd`.
- A new limit, `planning.max_phase_usd`, caps everything planning spends on a goal between one approval and the
  next: plans, continuations, resizes, gap fixes, reviews, revisions and replans. The planner checks it, and the
  goal's remaining budget, before it starts each run.
- Reaching it stops planning and asks the operator, with raising it as the button. Nothing is spent past it.

## Two people, and things that go wrong

- Every call that changes a plan names the version the operator was looking at. If the plan has moved, the call fails
  with "The plan changed while you were looking. Reload to see version 5", and the page offers Reload.
- A proposal whose plan moved is stale. The page says so and offers "Plan again".
- Every error on the page says what to do next and offers it as a button, as every console error must.
- A run that fails for a reason of the system retries with backoff before the page reports anything.

## Data and API

| Field or call | Change | Default |
|---|---|---|
| `planner.plan_proposal` | New table, as above | No row |
| `plan.baseline_version` | New column: the version "What changed" compares with | The plan's first version |
| `plan.stopped_at` | New nullable column: the limit a run ended at, `turns`, `time` or `spend` | Null |
| A task in `plan.drafts` | Gains `size`, `edited_by` and `kept` | Sized on save; `planner`; `false` |
| `GET /v1/goals/{id}/plan` | Gains `state`, `decision` (from `actions.py`), `glance`, `changes` against the baseline, and `proposal` | `proposal` absent when none is open |
| `GET /v1/goals/{id}/plan/versions` | New: each version's maker, time and counts | n/a |
| `GET /v1/goals/{id}/plan/diff?from=&to=&task=` | New: one task's old and new text | n/a |
| `POST /v1/goals/{id}/plan/proposal/accept`, `.../discard` | New | n/a |
| `POST /v1/goals/{id}/plan/use?version=` | New: makes an earlier version the plan | n/a |
| `POST /v1/goals/{id}/plan/stop` | New: cancels the planning run and keeps its tasks | n/a |
| `POST /v1/goals/{id}/plan/questions/{id}` | New: answer or dismiss an open question | n/a |
| `POST /v1/goals/{id}/replan` | Gains `keep` and `scope`; its result is a proposal | Keep as above; the whole plan |
| A need's `changes` list | Stays for old clients, holding the counts as short lines | n/a |

Events: `plan_saved` gets a version 2, since version 1 allows only the sources `planned` and `edited`. Version 2
adds `suggested`, `reviewed`, `resized` and `replanned`. New types at version 1: `issue_removed`,
`plan_proposed`, `plan_proposal_settled`, `plan_question_settled`. No existing schema changes.

Settings, checked at startup:

| Setting | Default | A bad or missing value |
|---|---|---|
| `planning.max_phase_usd` | `"12.00"` | Stops the service, naming it: a wrong value could cost money |

Every migration only adds.

## Screens and services

| Area | File | Change |
|---|---|---|
| Plan page | new `console/src/features/plan/` | The page above |
| Goal page | `goals/GoalPage.tsx`, `goals/PlannerUpdates.tsx` | The shorter panel and the plan's decision |
| Plan section | `goals/GoalPlan.tsx`, `goals/PlanReview.tsx` | Their parts move to the Plan page's files |
| Needs | `planner/actions.py`, `planner/needs.py`, `goals/needActions.tsx`, `overview/needsYou.ts` | The plan's states as state actions with their sentences and `ASKS` labels. `accept_fix` goes; `continue_plan`, `use_proposal` and `raise_planning_limit` arrive. `plan_waits` and the rule that a fix comes before approval follow the new states |
| Proposals and merging | new `planner/proposals.py`; `planner/suggestions.py`, `planner/draft_sizes.py`, `planner/planning.py` | One proposal store and one merge. Gap fixes and resizes apply themselves before the operator acts, and propose after |
| Planning tools | `images/harness-pi/extension/owned-tools.ts`, `run_service/prompts/` | `remove_issue`; a new prompt file that holds the previous tasks in full |
| Spend | `planner/planning.py` | The phase limit, checked before each run |
| Updates | `planner/updates.py` | Fixed progress lines; counts and reasons as facts |
| Help | `help/topics.ts` | A topic on reading and changing a plan |
| Design | `docs/technical-design.md`, `config/charter.txt` | The new principle; the Planner and console sections |

## How the other planner specs use this

- `docs/specs/adversarial-plan-review.md` supplies reasons with `source: review`, a revision that merges through
  the function above, and the "Improving" state while it runs. It adds no screen.
- `docs/specs/goals-span-repos.md` supplies the repo tile and the by-repo grouping.
- Both build on this page, so this spec goes first.

## Tests

- A gap fix on a plan no operator has touched applies itself, and the page shows one decision, approve.
- The same fix on a plan an operator edited arrives as a proposal.
- A revising run that names two tasks leaves every other task, and the plan's items, summary and gaps, byte for
  byte. `remove_issue` removes a task.
- Each kind of change lands in its own count.
- A replan's result is a proposal, and it leaves kept tasks and tasks with a run unchanged.
- `actions.py` gives each state its sentence and buttons, and the Plan page and `GET /v1/needs` agree for every
  state.
- Every button that spends names its limit, and no state's first button is a read.
- Planning stops at the phase limit and asks, with raising it as the button.
- A call against an old version fails with its remedy.
- The decision-drag test holds every planner ask to two sentences, 280 characters and no list before its first
  button.

## Decided (2026-11-25)

1. The plan gets a page of its own, and the goal page links to it.
2. "Reduce decision drag" joins the first principles.
3. Before the operator acts on a plan, the planner's own fixes apply themselves. After, they are proposals.
4. Changes appear as reasons and counts, with the lists one click away.
5. A replan is a form on the Plan page, and its result is a proposal.
6. Planning on a goal has one spend limit over all its runs.
