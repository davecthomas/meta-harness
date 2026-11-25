# Planner redesign: a Plan page

Status: asked for by the owner on 2026-11-25. Muxxy builds it as a goal from this file.

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
- **The ask buries the decision.** The operator's choice is "accept this fix or not". The two gaps and their
  fixes, which are what the choice turns on, get one line. The noise gets the rest of the paragraph.

## A new first principle: reduce decision drag

The design's first principles gain this one:

> **Reduce decision drag.** Everything shown to a person helps them decide, and costs them as little reading as
> the decision allows.

It has rules a page can be checked against:

1. **The decision comes first.** The top of the page says what the operator is deciding, in two lines at most,
   beside the buttons that decide it.
2. **Count before listing.** A set of more than five things appears as a count with a name ("38 tasks
   reworded"). The list is one click away.
3. **Structure before prose.** Things that have parts appear as tiles, groups, tables and chips. A paragraph
   holds one idea.
4. **Say what matters, in order.** What changes the outcome comes first: scope, cost, risk, order. Wording
   changes come last.
5. **One place for one thing.** A plan, its changes and its approval live on one page. Other pages link there.
6. **Detail on request.** Every summary opens to its full detail, so the page hides nothing and forces nothing.

A test in the console fails a planner ask that renders more than five lines of text before its first button.

## The Plan page

A page of its own at `/goals/<id>/plan`. The goal page links to it and no longer holds the plan.

It has these parts, top to bottom:

| Part | What it shows |
|---|---|
| Header | The goal's title, the plan's version and state, and what planning has cost so far |
| Decision bar | The one thing the operator is deciding now, in two lines, with its buttons, best first. It stays in view while the page scrolls |
| At a glance | Tiles: tasks, repos, estimated cost against the budget, how many tasks can run at once, open questions |
| What changed | Only when a newer version or a proposed fix exists. See below |
| Open questions | The gaps only the operator can settle, each with a way to answer it |
| Tasks | The plan's tasks in groups, folded. See below |
| History | The plan's versions as a timeline: who or what made each, when, and its change counts |

The plan's states, shown as a chip in the header:

| State | Decision bar |
|---|---|
| Drafting | "The planner is writing the plan." Progress and cost so far. A Stop button |
| Has a proposed fix | "The planner found 2 gaps and drafted a fix." Buttons: Accept the fix, Keep the plan as it is |
| Ready to approve | "44 tasks in 1 repo, about $62 of your $100 budget." Buttons: Approve, Replan |
| Cannot be approved | Why, in one line, with the button that fixes it |
| Incomplete | "Planning stopped at its limit with 44 tasks drafted." Buttons: Carry on, Plan again |
| Approved | "Approved by you on Nov 25. 12 of 44 tasks done." A link to the goal's tasks |

### What changed

This part replaces the paragraph. It compares the version the operator last saw with the proposed one.

- **Why it changed** comes first: one row for each gap, with the gap in a few words and the fix beside it. For
  the fix above that is two rows.
- **Counts** come next, as chips: "2 added", "0 removed", "9 reordered", "3 changed", "38 reworded". A chip with
  a count of zero does not appear.
- **A chip opens its list.** Each entry is a task's title. A changed or reworded task opens to a side-by-side
  view of its old and new text.
- **Cost and time** come last, as one line: "Adds about $4 and 2 tasks to the plan."

The kinds of change, in the order they matter:

| Kind | Meaning |
|---|---|
| Added | A task the plan did not have |
| Removed | A task the plan no longer has |
| Reordered | A task that now waits for a different set of tasks |
| Changed | A task whose repo, acceptance checks or size changed |
| Reworded | A task whose title or description changed and nothing else |

A fix whose only changes are rewording says so in one line, and its Accept button stays.

### Tasks

- The page groups tasks by repo, or by stage: the tasks that can start now, then the ones that wait on them.
  The operator picks the grouping, and by-stage is the default.
- A group shows its name, its task count and its estimated cost, folded.
- A task row shows its title, size, repo and what it waits for. It opens to its full text and acceptance checks,
  with Edit and Remove.
- Marks on a row say what the proposed version does to it: added, changed, reordered, reworded.
- "Add a task" sits at the foot of each group.

### Replan

Replanning is work the operator directs, so it gets a form of its own on the Plan page. "Replan" in the decision bar
opens it.

| Field | Default | Purpose |
|---|---|---|
| What is wrong with this plan | Empty, required | The note the planner works from. Chips suggest common reasons: too many tasks, wrong order, missing work, too costly |
| Keep | Every task the operator edited by hand | Tasks the planner must leave as they are. The operator ticks more in the task list |
| Scope | The whole plan | Or only the ticked groups |
| Spend limit for this run | The planning limit | What the run may cost |

The form shows what the last planning run cost, and a Start button. While the run goes, the page is in the
Drafting state. The result arrives as a proposed version with its "What changed" part, for the operator to accept
or discard. A replan never replaces the current plan before the operator accepts it.

## The goal page

- The planner's panel keeps its headline and one or two sentences. Under them sits one button, "Open the plan",
  with a short line of what is waiting: "A fix is waiting: 2 gaps, 2 tasks added".
- The "Needs you" box no longer lists a plan's changes. A plan need is one sentence with a button that opens the
  Plan page at its decision bar.
- "The plan" section, with its Add a task, Plan again and Approve buttons, moves to the Plan page. The goal page
  shows the plan's state and task count in its place, as a link.
- The Overview and the needs list link a plan need to the Plan page.

## What the planner must do differently

- **A fix changes only what it names.** The revising run's result is the previous version's tasks with the run's
  changes over them. A task the run did not call `create_issue` for moves into the new version byte for byte.
  The adversarial plan review spec asks the same of its revisions, and one mechanism serves both.
- **The planner writes for the decision.** Its update on a plan leads with what the operator decides and gives
  counts. It names no task unless the operator must act on that task.
- **The service aggregates.** `suggestions.changes()` gives way to a summary the service computes: the gaps and
  their fixes, the counts by kind, the task ids in each kind, and the cost difference. The console draws it, and
  no model writes it.

## How this fits the other planner specs

- `docs/specs/adversarial-plan-review.md` puts its review summary and findings on a plan. They appear on this
  page: the summary in the decision bar and "What changed", each finding on its task row.
- `docs/specs/goals-span-repos.md` lists a plan's repos above its tasks. They appear as the repo tile and the
  by-repo grouping.
- Each of those specs builds its plan screens on this page. This one goes first.

## Data and API

| Field or call | Change | Default |
|---|---|---|
| `GET /v1/goals/{id}/plan` | Gains `state`, `glance` (the tile figures) and, when a proposed version exists, `proposed` with its `change_summary` | `proposed` is absent when there is none |
| `change_summary` | New: `reasons` (gap and fix pairs), `counts` by kind, `tasks` by kind, `cost_delta_usd` | n/a |
| `GET /v1/goals/{id}/plan/versions` | New: the versions with who made each, when, and its counts | n/a |
| `GET /v1/goals/{id}/plan/diff?from=&to=&task=` | New: one task's old and new text | n/a |
| `POST /v1/goals/{id}/replan` | Gains `keep` (task ids), `scope` (group ids) and `max_usd` | Keep the hand-edited tasks; the whole plan; the planning limit |
| A need's `changes` list | Stays for old clients, and holds the counts as short lines | n/a |
| `plan.kept` | New column, `jsonb NOT NULL DEFAULT '[]'`: the task ids a replan must leave alone | `[]` |

Every migration only adds. No event type changes.

## Screens

| Screen | File | After |
|---|---|---|
| Plan page | new `console/src/features/plan/` | The page above |
| Goal page | `goals/GoalPage.tsx`, `goals/PlannerUpdates.tsx` | The shorter panel and the link, as above |
| Plan section | `goals/GoalPlan.tsx`, `goals/PlanReview.tsx` | Their parts move to the Plan page's files |
| Needs | `goals/needActions.tsx`, `overview/needsYou.ts` | Plan needs open the Plan page |
| Help | `help/topics.ts` | A topic on reading and changing a plan |

The Plan page reads what needs the operator through `needsYou.ts` and draws the shared `NeedButton`, as every
page does.

## Tests

- A fix that settles two gaps on a 44-task plan gives two reasons and counts, and its ask renders no task title
  before its first button.
- A task the revising run did not touch is byte-identical in the new version.
- Each kind of change lands in its own count, and a reworded-only fix says so.
- A replan with kept tasks leaves them unchanged, and its result is a proposal until accepted.
- The console test for decision drag fails an ask with more than five lines of text before its first button.
- The Plan page draws each state's decision bar with its buttons.

## Decided (2026-11-25)

1. The plan gets a page of its own, and the goal page links to it.
2. "Reduce decision drag" joins the first principles.
3. Changes appear as reasons and counts, with the lists one click away.
4. A replan is a form on the Plan page, and its result is a proposal.
