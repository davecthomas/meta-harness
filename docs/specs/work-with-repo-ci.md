# Muxxy works with each repo's own checks and deploy

## Why

A repo's checks and its deploy are where its owners wrote down what counts as good and what counts as live.
Muxxy treats them as a gate to wait at. It runs a test suite in its sandbox that the repo's CI runs again a
minute later. It hands a fix run a failed check's name with none of its output. It waits for a repo's deploy
without saying the change is live. And nothing shows an operator how a repo's own checks are doing: whether
its base passes, or which checks fail by chance.

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
- **A task says when its change is live.** It already waits for the repo's deploy where that is a check on the
  merge. The operator should read "Live" where the page now says "Merge commit's checks passed".
- **The operator sees each repo's health.** Whether its base passes, how long its checks take, and which fail
  by chance: on one page, and beside the repo wherever an operator picks or watches it.
- **The repo's findings are not lost.** A security alert or a check that keeps failing by chance becomes work
  or a count, never a log line nobody reads.

## What Muxxy reads from a repo today

- Every check run on a pull request's head, required or not, and on the merge commit. A merged task stays open
  until the merge commit's checks pass (`wait_merge_checks`), and where the repo deploys from a workflow on its
  base branch, the deploy's job is one of those checks. So such a task already ends when its change is live.
  Nothing says so.
- The base branch's check state before a merge.
- How long each check took on the base's newest commit, shown beside a task's running checks as their usual
  time (`GET /v1/pulls/{task_id}/checks`).
- One rerun of a failed check, recorded as a `remedy_tried` event.

A task's page already draws its path from its events: its runs, its pull request's checks, its reviews, the
merge and the merge commit's checks (`console/src/features/tasks/path.ts`).

It reads no job log, no branch rule and no security alert, and it counts no rerun.

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

- **A task says when its change is live.** A repo may name the check that says a merge is live (`deploy_check`
  in its registry entry, such as "deploy to Google Cloud"). The task already waits for it with the merge
  commit's other checks. With it named, the last step of the task's path reads "Live" once that check passes,
  and "The deploy failed" when it does not, in place of "Merge commit's checks passed". A repo with none named
  reads as now.

### Repos in the console

A repo's state has no home today. Its settings are under Admin, and nothing says whether its base passes. A
mockup of what follows: https://claude.ai/artifact/66KSCSEhznifi7A8abdhN9 (private to the owner until shared).

**A Repos page, in the sidebar after Tasks.** Goals stay the unit of work, and this page starts none: it is
read, with links out. One box for each repo that has an open goal or an entry in Configuration, Repositories,
a failing base first:

```
Repos                                              3 repos with an open goal or settings here

+-----------------------------------------------------------------------------------------+
| stagwell-machine/console-api   [! Base fails 3 checks]              [Open the fix task]  |
|                                                                                         |
| BASE                    CHECKS                 WORK HERE              LIVE              |
| Passes on GitHub        Usually 8m 20s         1 goal                 No deploy check   |
| Fails in the sandbox    integration tests:     2 tasks running,       named             |
| npm test                rerun 4 of 22 this     1 fixing the base      Name one in       |
| at 4c1e9d0, found by    week                   1 open pull request    settings          |
| "Add CSV export"                                                                        |
+-----------------------------------------------------------------------------------------+
| davecthomas/meta-harness       [ok Base passes]                         Edit settings   |
|                                                                                         |
| BASE                    CHECKS                 WORK HERE              LIVE              |
| Passes on GitHub        Usually 5m 15s         1 goal                 14 minutes ago    |
| Not run in the sandbox  No check rerun         3 tasks running        Plan page: an     |
| at 49dcecd              this week              2 open pull requests   answer field...   |
|                                                                                         |
| Standing checks: uv run ruff check .     Deploy check: deploy to Google Cloud           |
+-----------------------------------------------------------------------------------------+
```

- **Base.** Whether the base's newest checked commit passes on the code host and in the sandbox. For a failing
  one: the failing checks, the commit, and the goal whose task found it. The box's button opens the fix task
  when one is open, and is Fix it when Muxxy asked the goal's operator.
- **Checks.** How long the repo's checks usually take together, and each check rerun for failing by chance in
  the last seven days, with how many of its runs.
- **Work here.** Its open goals, running tasks and open pull requests, as counts that link to them.
- **Live.** For a repo with a deploy check named: the last change that went live and when. For one with none: a
  link to name one.
- **Settings.** Its standing checks and deploy check, read only, with a link to Configuration, Repositories,
  which edits and versions them.
- A repo no goal is working in says so, and that a task's next start checks its base.
- The sidebar's entry counts the repos whose base fails.

**One health marker, wherever an operator picks or watches a repo.** "Base passes" or "Base fails 3 checks", with a
mark that differs in shape, and a link to the Repos page:

- New goal: beside each repo offered. A goal can start on a failing base; the marker says so before it does.
- A goal's page: beside each of its repos.
- A task's page: the last step of its path reads "Live" for a repo with a deploy check named.

**What this page is not.** It shows no code and lists no pull requests: the code host does both, and every row
links there. A repo has no page of its own, no history and no action but the fix for its base.

**Served by** `GET /v1/repos/health`, from what the run service and the planner already record: the kept base
checks, `base_check_ran`, `remedy_tried`, `merge_checks` and the goals' links. Reading the page asks the code
host nothing.

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
5. **A task says when its change is live**, with `deploy_check`.
6. **Repos in the console**: `GET /v1/repos/health`, the Repos page and the health marker. The
   endpoint and the page go in first, then the marker on New goal and a goal's page.
7. **This repo's CI**: the required check and admin rule, the pinned Temporal CLI, `pytest` across cores and
   shared images. The rules and the pin go in first, then the speed.
8. **Audits and scans in this repo's CI**, then **a security alert becomes an issue**, which reads them.
9. **A type check and coverage of changed lines**, with the reviewer told which changed lines no test ran.
