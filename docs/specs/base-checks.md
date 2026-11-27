# Muxxy finds a base branch's failing checks once and fixes them

## Why

A task's run that meets a failing check has to work out whether its own change broke it. Seven pull requests in
a row met the same three failing tests, each showed they also failed without its change, and each went on. Each
reviewer read that and passed the pull request. Nobody was told the base branch was failing in the sandbox, and
nothing fixed it (`docs/technical-design.md`, Friction rules, "A check that fails on the base gets fixed").

## What we want

- **The run service checks the base once.** Before a task's first run starts, the run service looks up each of the task's
  acceptance checks for the repo's base commit. It runs a check with no result on the untouched base, in the
  task's sandbox, and keeps the result. Later tasks on that commit read it and run nothing.
- **A run is told what already fails.** The run's prompt lists the checks that fail on the base, with the end of
  each one's output. The run leaves them alone and does not prove again that they fail.
- **A reviewer is told the same.** A check on that list does not hold the pull request. A check that passes on
  the base and fails on the pull request blocks it.
- **A failing base is one thing to fix.** The goal whose task found it gets one fix task for that repo and
  commit, named for the failing checks and carrying their output. Muxxy reuses a fix task that is already open for the
  repo, whichever goal made it.
- **Who starts the fix follows the goal's autonomy.**
  - Chill: Muxxy asks. The goal's page and the needs list say "main fails 3 checks in the sandbox", with a
    button, "Fix it", that starts the fix task.
  - Standard: Muxxy starts the fix task. Its pull request waits for the operator to merge, as every task's does.
  - Assertive: Muxxy starts the fix task and merges its pull request once its checks and review pass.
- **Work goes on meanwhile.** Tasks start on a failing base, told what fails. The fix task runs first among the
  goal's waiting tasks.

## Decisions

- **Which checks.** The task's own acceptance checks, which default to the repo's `default_checks`. No new
  setting names them.
- **When.** At a task's first run, never on every merge. The run service checks no repo that has no task running,
  and checks a base commit at most once for each check command.
- **Two tasks at once.** One of them runs the base checks. The other starts without the list, and its reviewer
  gets it.
- **Where the result lives.** A table in the run service's database, keyed by repo, base commit and check
  command, holding whether it passed, its exit code and the last 2,000 characters of its output. Each run of a
  base check is a `base_check_ran` event.
- **Spend.** The base checks call no model. The fix task counts toward its goal's budget as any task does.
- **A check that cannot run.** A base check that times out or cannot start counts as failing, with that as its
  output.

## Where

Paths: src/meta_harness/run_service/base_checks.py, src/meta_harness/run_service/migrations,
src/meta_harness/run_service/workflows.py, src/meta_harness/run_service/activities.py,
src/meta_harness/run_service/prompts, src/meta_harness/events/types.py, src/meta_harness/planner/base_fix.py,
src/meta_harness/planner/actions.py, console/src/features/overview

## Checks

- A run service test: two tasks on one base commit run each base check once, and the second task's prompt lists
  the failing check with its output.
- A run service test: a review's prompt says a check failing on the base does not hold the pull request, and a
  check that passes on the base and fails on the pull request does.
- A replay test: a workflow recorded before this change replays, and the test file holds a history of the new path
  (`tests/run_service/test_replay.py`).
- A planner test for each autonomy: Chill lists the need and starts nothing; Standard and Assertive start one fix
  task; a second failing task on the same repo starts no second fix task.
- A console test: the need shows its sentence and "Fix it" through the shared `NeedButton`.
- `uv run pytest -q` and `npm test` in `console/` pass.
