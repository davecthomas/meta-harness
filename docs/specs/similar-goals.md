# New goal shows similar goals that are already open

## Why

Nothing compares a new goal with the goals already open. An operator created "Keep the task through context
compaction" on 2026-11-25 while "Keep task through context compaction", created three days earlier, still sat in
planning. Two goals for the same work plan it twice and can build it twice.

Muxxy already holds duplicate issues inside one goal (`planner/copies.py`). This does the same for whole goals,
when an operator creates one.

## The flow

1. The operator writes a goal's description on New goal.
2. Muxxy compares it with the open goals.
3. When one or more are close, a panel under the description lists them. With none close, the form shows nothing.
4. The operator opens one, says a row is not a duplicate, or keeps writing. Nothing blocks saving.

## What we want

### The comparison

- Muxxy embeds the description's text with one call to the HTTP Gateway's `POST /v1/embeddings`.
- It compares that, by cosine similarity, with the stored embedding of each open goal: one whose status is
  planning, active, paused, stalled or budget exhausted. It leaves out a met or canceled goal.
- Muxxy makes a goal's embedding from its description when an operator creates the goal and again when its
  description changes, and keeps it with the goal. A goal with no embedding yet gets one at its first comparison.
- The check runs when the description field loses focus, or two seconds after typing stops, and only when the
  description has changed and has 40 characters or more.

### The panel

Up to three goals at or above the cut-off, the closest first, under the heading "Similar goals already open".
Each row shows:

- the goal's title;
- its status, and since when, as the goals list words them;
- the similarity as a whole percentage, such as "91% similar";
- **Open it**, which opens that goal's page in a new tab, so the form keeps what the operator typed;
- **Not a duplicate**, which removes the row and records the answer.

The panel takes its place in the page's flow under the description. It is not a dialog and it moves nothing else
on the page when it appears after the form is first drawn, since it sits above nothing the operator has filled in.

When the check cannot run, the panel's place holds one line: "Could not look for similar goals: `<why>`. You can
still save." The form works as it does today.

### The one question at save

When the operator saves while a row still shows, the form asks once, in place, about the closest row left:
"Is this the same work as `<title>`?" with **Yes, same work** and **No, different**.

- **No, different** records the answer and saves.
- **Yes, same work** records the answer and offers **Open that goal** and **Create this one anyway**. Nothing is
  merged for the operator.

A form with no row showing asks nothing.

### The record of each answer

Each time the panel shows a goal, and each answer, is an event, so an operator can set the cut-off from what
operators said. A new event type, `goal_similarity_judged`, version 1, holds:

- the goal shown and its similarity score;
- the embedding model and the cut-off in force;
- the answer: `shown`, `opened`, `not_duplicate`, `same_work` or `different`;
- the new goal's id, once the operator saves it.

The new description's text is not in the event.

### Setting the cut-off

The Performance page gains a table, "Similar goals shown", with a row for each goal shown: the two titles, the
score and the answer. It reads the events above. An operator moves the cut-off in Configuration once the answers
show where "same work" and "different" part.

## Not in this version

**Adding the new description to the similar goal.** Widening a goal that has a plan or is running means a new plan,
a budget decision and one description written from two. The recorded answers will show whether operators want it.

## Screens

| Screen | Change |
|---|---|
| New goal (`console/src/features/goals/NewGoal.tsx`) | The panel under the description, and the question at save. Both are new files in `console/src/features/goals/` that the form renders |
| Performance (`console/src/features/performance/`) | The "Similar goals shown" table, a new file |
| Configuration, Planning (`config/kinds.py`, `PlanningView.tsx`) | A "Similar goals" group with the settings below |

## Services

| Area | File | Change |
|---|---|---|
| Embedding a text | new `planner/embeddings.py` | One call to `/v1/embeddings`, on a client built with `retrying.async_transport()` |
| Finding similar goals | new `planner/similar_goals.py` | The stored embeddings, the comparison, and `POST /v1/goals/similar`, which takes a description and returns the rows |
| Recording an answer | `planner/similar_goals.py` | `POST /v1/goals/similar/answer` |
| Keeping a goal's embedding | `planner/service.py` | One call after an operator creates a goal or changes its description. The planner logs a failure, and the save still works |
| Listing the showings | `planner/similar_goals.py` | `GET /v1/goals/similar/shown`, for the Performance table |
| The route | `edge/app.py` | `/v1/goals` already goes to the planner |

## New fields and their defaults

| Field | Where | Default | Existing rows |
|---|---|---|---|
| `goal_embedding` | new table: goal id (key), model, the vector as `real[]`, a hash of the text it was made from, made at | No row until Muxxy compares or creates the goal | None; made on first comparison |
| `goal_similarity_judged` | new event type, version 1 | n/a | n/a |

The migration only adds. Muxxy makes a vector again before comparing it when another model than the configured one made it, so changing
the model needs no migration.

## Settings

All in the planning configuration, under `similar_goals`, checked at startup.

| Setting | Default | A bad value |
|---|---|---|
| `enabled` | `true` | Falls back to the default through `fallback.py` |
| `model` | An embedding model, as `<engine>:<model>`, picked in the console from the gateway's `/v1/models` list | Stops the service with its name: another model costs differently |
| `cutoff` | `0.70` | Outside 0 to 1, falls back to the default, and what needs the operator lists it until fixed |
| `most` | `3` | Outside 1 to 5, falls back to the default |

The default cut-off is low on purpose: early on the panel should show too much, so the answers cover both sides
of the right number.

## Cost

One embedding call for each description checked and one for each goal created or edited. A description is a few
hundred tokens, so each call costs well under a cent. Muxxy records the cost as planner spend, as it does for the planner's other
model calls.

## Tests

- The comparison: closest first, the cut-off, the limit on rows, and a met or canceled goal left out.
- Muxxy makes a goal's embedding at creation, and again when its description or the model changes.
- The gateway failing: the save works, and the form says the check could not run.
- Each answer records its event, without the description's text.
- The console: no panel with nothing similar; the rows and their two buttons; the question at save, asked once and
  only with a row showing.
- No test calls the gateway.

## Decided (2026-11-26)

- At most three rows above a cut-off, with the percentage on each.
- A row offers Open it and Not a duplicate.
- One question at save, for a clean answer.
- Widening the similar goal waits for the recorded answers.
