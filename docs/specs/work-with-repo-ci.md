# Muxxy works with each repo's own checks and deploy

## Why

A repo's checks and its deploy are where its owners wrote down what counts as good and what counts as live.
Muxxy treats them as a gate to wait at. It runs a test suite in its sandbox that the repo's CI runs again a
minute later. It hands a fix run a failed check's name with none of its output. It calls a task done at the
merge, while the repo's own deploy is still running. And an operator watching a task cannot see which check it
waits on, how long that check takes, or whether the change is live.

Each of those costs time or money on every task, and each has the same cause: Muxxy does not read what the
repo already says.

## The principle

A first principle joins the ten in `docs/technical-design.md`, the charter each model reads and Help:

> **Work with each repo's own checks and deploy.** They say what the repo's owners count as good and as live.
> Muxxy reads what they already say before it runs anything itself, and repeats nothing they will run. It shows
> where each change stands on that path, and counts a change done when the repo's own process says it is live.
> What they find that no task covers becomes work.

## Goals

- **Less repeated work.** A check runs once for a commit, wherever it ran.
- **Fewer fix rounds.** A fix run starts from the failed check's own output.
- **A task ends when its change is live.** A failed deploy is a stop with an owner, as a failed check is.
- **The operator sees the path.** Each task shows where its change stands between the sandbox and live, and
  what it waits on.
- **The repo's findings are not lost.** A security alert or a check that keeps failing by chance becomes work
  or a count, never a log line nobody reads.

## What Muxxy reads from a repo today

- Every check run on a pull request's head, required or not, and on the merge commit.
- The base branch's check state before a merge.
- One rerun of a failed check, recorded as a `remedy_tried` event.

It reads no job log, no check's duration, no branch rule, no deploy and no security alert.

## What we want

### Read before running

- **Base checks read the code host first.** Before the run service runs a repo's standing checks on a base
  commit (`docs/specs/base-checks.md`), it reads that commit's check runs. When every one passed, it keeps the
  standing checks as passing with no sandbox run. When one failed, the sandbox run goes ahead, since the repo's
  CI and the sandbox can differ and the fix task needs the sandbox's output. The sandbox still checks a commit with no
  finished check runs, as now.
- **A fix run reads the failed check's log.** For a check a GitHub Actions job ran, the fix run's prompt
  has the end of that job's log, 4,000 characters at most, as data. A check with no log keeps its summary.

### Repeat nothing

- **A task's run tests what it changed.** The run's prompt says to run the tests of the files it changed, and
  the acceptance checks the task names. It says the repo's CI runs the whole suite on the pull request, so the
  run does not.

### Done means live

- **A task follows its merge to the deploy.** A repo may name the check that says a merge is live
  (`deploy_check` in its registry entry, such as "deploy to Google Cloud"). The merge commit's checks already
  hold the task (`wait_merge_checks`). With `deploy_check` set, the task's page says the change is live once
  that check passes, and a failed deploy starts a fix run as a failed check after the merge does. A repo
  with none set ends its tasks at the merge, as now.

### Show the path

- **Each task shows its path.** One line of steps on a task's page: Run, Checks, Review, Merge, Main's checks,
  Live. Each step is done, under way, waiting or failed, from the task's events. The step under way says what
  it waits on, and for a check, how long that check usually takes.
- **Each check has a usual time.** The run service records how long each check ran when it reads a pull
  request's checks. The usual time is the middle of its last ten runs.
- **A repo has a health line.** On a goal's page, for each of its repos: whether the base passes on the code
  host and in the sandbox, how long its checks usually take together, and which checks were rerun for failing
  by chance in the last week, with how often.

### Findings become work

- **A security alert becomes an issue.** For a repo with code scanning or dependency alerts on its code host,
  the planner reads the open alerts once a day. It files each new one of high or critical severity as an issue
  in the repo's tracker, with no goal's label, as it files a review's follow-ups. An operator takes it into a goal.

### The repo's own CI

This repo is the first one Muxxy works in, so its CI should give Muxxy what the sections above read:

- "sandbox runs this repo's checks" becomes a required check, and the branch's rules bind admins.
- CI downloads the Temporal CLI at a fixed version and checks it against its hash.
- `pytest` runs across the runner's cores.
- A run builds the sandbox images once, and its jobs share them.
- `pip-audit` and `npm audit` run on each pull request at high severity; CodeQL and a scan of the sandbox
  image run nightly and on main.
- Each pull request reports a type check of `src/` and the coverage of its changed lines.

## Decisions

- **Muxxy takes the code host's word for a passing base, never for a failing one.** A wrong "passing" costs one
  task a surprise its own checks then catch. A wrong "failing" would start a fix task for nothing.
- **No new required setting.** `deploy_check` is optional. Every other part reads what the code host already
  serves, and does nothing for a repo whose host serves none of it.
- **Logs are data.** A job's log goes into a prompt between markers, with the rule to follow no instruction
  in it, as a check's summary does now.
- **Nothing here blocks a merge that passes today.** These read more and repeat less. The merge test is
  unchanged.
- **Muxxy files alerts and fixes none unasked.** A fix for a dependency can break a build. The operator decides
  which goal takes it.

## Build plan

In order. Each is one pull request unless it says otherwise.

1. **This spec and the principle**: the design document, the charter and Help.
2. **Base checks read the code host first.** Removes sandbox runs the last change added.
3. **A fix run reads the failed check's log.** The largest cut in fix rounds expected.
4. **A task's run tests what it changed.** After 3, since the run then leans on CI and CI's output must reach
   the fix.
5. **Each check has a usual time**, recorded by the run service. Needed by 6.
6. **Each task shows its path**, with `deploy_check` and Live. The console change every later part shows in.
7. **A repo has a health line**, with the count of checks rerun for failing by chance.
8. **This repo's CI**: the required check and admin rule, the pinned Temporal CLI, `pytest` across cores and
   shared images. The rules and the pin go in first, then the speed.
9. **Audits and scans in this repo's CI**, then **a security alert becomes an issue**, which reads them.
10. **A type check and coverage of changed lines**, with the reviewer told which changed lines no test ran.
