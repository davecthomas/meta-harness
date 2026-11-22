# Long runs keep their task through context compaction

## Why

A run on a large task fills its model's context window. The coding agent then compacts: it replaces the older part of the conversation with a summary and keeps working. A run that compacts badly forgets its task, repeats work it already did, or stops short and reports success.

Production shows the problem is real. Runs have compacted 30 times, and some of those compactions started only after a model call had already failed for being too long.

## What happens today

- The harness server tells the agent that every model has a context window of 200,000 tokens and answers of 8,192 tokens (`images/harness-pi/server/harness.ts`). The real limits differ by model.
- The agent compacts with its own defaults: it starts when 16,384 tokens are left and keeps the most recent 20,000.
- The run's own model writes the summary, and that is the most expensive model the run uses.
- Nothing fixes what a summary must hold. The task's text, its acceptance checks and the agent's plan survive only when the summarizing model happens to keep them.
- The run records a compaction as one `compacted` event with a reason. The task page shows a line that says "context compacted" and no more.

## What we want

### Each model's real limits

The context window and the largest answer come from configuration, for each model a run can be given. The harness server uses the limits of its run's model. A model with no limits configured stops the service at startup, by name, like any other bad setting.

### Compaction starts before a call fails

A run compacts while it still has room, so no model call fails for length. How much room to keep is a setting of the harness profile, since a profile with long tool output needs more.

When a call still fails for length, the run compacts and repeats that call once. It does not end the run.

### A cheaper model writes the summary

The cheapest tier's model writes the summary, through the HTTP Gateway, inside the run's own spend limit. Its cost counts against the run like any other call.

When that call fails, the run's own model writes the summary, so a run never stops because the cheaper model was unavailable.

### What a summary always holds

After any compaction the conversation still has, word for word:

- the task's title and description;
- its acceptance checks;
- the agent's current plan, with its finished steps marked;
- the files the run has changed so far.

The harness puts these back itself after each compaction. The summarizing model writes only the rest.

### A compaction can be seen

The task page shows each compaction on the run's timeline, with:

- when it happened, and why: near the limit, or after a call failed;
- how many tokens the conversation had before and after;
- which model wrote the summary, and what it cost.

A run's totals say how many times it compacted. The goal's page marks a task whose run compacted more than a set number of times. That task was probably too large for one run.

### A task that is too large is said to be

A run that compacts more than the profile's limit ends. Its task fails with a reason that names the limit and what to do: split the issue. The planner's sizing counts that as evidence the next time it sizes an issue like it.

## Out of scope

- Carrying a conversation from one run of a task to the next. Each run starts fresh from the task and its notes, and that stays.
- Changing the coding agent itself. It stays unmodified; the harness server and its model client extension set it up.
- Compaction in the planner's own model calls, which are single calls with no conversation.

## How we will know it works

- No run in production records a compaction that followed a failed call.
- A run that compacts three times still passes its acceptance checks at the rate of a run that never compacts.
- The cost of compaction per run falls, since a cheaper model writes the summaries.
- The zero-cost end-to-end check has a run that compacts and still finishes its task.

## Constraints

- Code names no model: limits and the summarizing tier live in `config/`.
- The service checks every new setting at startup.
- A change to what the run service's workflow runs goes behind a patch, with a recorded history.
- An event type's schema never changes once merged: a compaction's new fields are a new version of the event.
- The harness image, the sandbox and the run service must keep the zero-cost end-to-end check passing.
