# Goals that span repos

Status: approved in outline by the owner on 2026-11-24. To be built as a harness goal from this file.

## Why

A problem often crosses repos: an API in one, its client in another, the infrastructure in a third. Today a goal
can only touch repos an admin registered one by one, in orgs where an owner installed the harness's GitHub App,
and the operator must name the goal's repos before anyone has looked at the problem. Other AI coding tools work
with whatever the signed-in person can already reach on GitHub. The harness should too.

## The flow

1. The operator connects their GitHub account, once.
2. The operator creates a goal. They do not choose its repos.
3. The planner asks which repos are needed to solve the goal, reads them, and names them in the plan.
4. The operator approves the plan. Those repos become the goal's repos.
5. Each task changes one repo and opens one pull request there.

No org owner installs anything. No admin registers a repo. Nothing limits how many repos a goal reads or changes;
the goal's budget is the bound.

## What happens today

- `goal.repos` is already a list and each task already has one repo (`task.repo`). Neither changes.
- A repo is usable only when it is a key in the `repos` config (`planner/repos.py`), which only an admin can save,
  and only where an org owner installed the GitHub App named for that owner in `MH_GITHUB_APPS` (`github_app.py`).
- New goal asks for the goal's repositories first, and every other choice on the page follows from them.
- The planning run clones only the goal's first repo (`planner/planning.py`), so a plan for a goal with several
  repos is written without reading the others.
- A draft that names a repo outside the goal's list is invalid (`planner/plans.py`).
- An operator signs in with Google. The services know them by that identity (`X-MH-Actor`), and nothing links it to
  a GitHub account.

## What we want

### An operator connects GitHub once

- Anywhere the console needs GitHub and the operator has no connection, it shows one button, "Connect GitHub". It
  opens GitHub's own sign-in and consent screen and returns to the page the operator was on.
- The connection belongs to the signed-in operator. It gives the harness what that operator can do on GitHub: read
  and push to repos, open and merge pull requests, read and write issues.
- The control plane stores the operator's token encrypted, never logs it, never puts it in an event, and never
  passes it into a sandbox. Only the control plane writes to GitHub, as now.
- A connection that GitHub has revoked or expired is noticed the next time it is used. Everything that needs it
  says so with a "Reconnect GitHub" button.
- The operator can disconnect on the Settings page. Their running goals then stop where they next need GitHub.

### A goal acts as its operator

- A goal created in the console acts as the operator who created it. Its clones, pushes, pull requests, merges,
  checks and issue writes on GitHub use that operator's connection.
- Its pull requests are therefore authored by the operator's GitHub account. Their text still goes through
  `outbound.prepare` and carries the harness's provenance.
- Another operator can take a goal over on the goal page ("Act as me"). From then on it uses their connection, and
  its repos are checked against their access.
- A goal with no operator (one created before this change, or by a service with an API key) keeps using the GitHub
  App on the repos the App is installed on, exactly as today.

### The operator does not choose repos

- New goal has no Repositories control. Nothing on the page is blocked for want of a repo.
- Where a choice on the page needs to look inside one repo (picking a spec file from a repo, browsing a repo's
  issues), that control has its own repo picker, fed by the repos the operator can reach. A repo picked this way
  tells the planner where to start. It does not limit the plan.
- A goal may be saved with no repos. It gets them when its plan is approved.

### The planner works out the repos

- The planning run is given the goal and the list of repos its operator can reach: each one's name, description,
  main language and when it was last pushed to.
- It names the repos it needs to read. The control plane clones each one read-only into the planning sandbox, on
  its default branch, side by side. The planner may ask for more as it learns more.
- A repo the operator pointed at on New goal, a repo named in the goal's text or spec, and the repo of any issue
  already on the goal are always cloned.
- Each draft in the plan names the one repo it changes. The plan says, in a sentence per repo, why that repo is
  involved.
- A draft is valid only when the goal's operator can push to its repo. One that names a repo they can only read,
  or cannot reach, is invalid, and the plan says which and what to do.
- For each repo it plans work in, the planner records the checks it found in the repo's `AGENTS.md` or CI
  configuration. They become the acceptance checks of the drafts in that repo that name none.

### Approving the plan approves the repos

- The plan panel lists the repos above the drafts, each with the planner's reason and its draft count, and groups
  the drafts by repo.
- The operator can change a draft's repo before approving, picked from the repos they can reach.
- Approving the plan sets the goal's repos to the repos of its drafts. No task touches a repo before that.
- A replan that adds a repo shows it marked as new, and is approved the same way.

### Goals that run issues as they are

- A GitHub issue names its repo by where it lives. A Jira issue names it with a `Repo:` line or a `repo:` label.
  An issue the operator picks on New goal, or that carries the goal's label when the goal is created, brings its
  repo into the goal: the operator chose it.
- A Jira issue that names no repo cannot run as it is. A goal with such an issue is planned first, and New goal
  says so; the planner assigns the repo and writes the `Repo:` line onto the issue through `outbound.prepare`.
- After a goal has started, an issue that gains the goal's label in a repo the goal does not have is held. It
  appears in what needs the operator, with a button that adds the repo to the goal.

### A task works in one repo and can read the others

- A task clones the repo it changes at `/workspace`, changes it and opens one pull request, as now.
- The goal's other repos are cloned read-only beside it, at `/workspace-reads/<owner>/<name>`, and the task's
  prompt lists them. Only `/workspace` is ever published, so nothing written to a read repo is pushed.
- Tasks are ordered across repos with `depends_on`, as they are within one repo: a task starts when the pull
  request it depends on has merged.
- No pull request spans repos and no merge is atomic across repos. A change that must land in two repos is planned
  as an ordered pair, the side that keeps working alone first.
- Before a task starts, the run confirms the operator's connection works and can still push to the task's repo. If
  not, the task stops there and says what to do.

### The repo registry becomes optional

- A repo needs no entry in the `repos` config. An entry only overrides a default for that repo: its tracker and
  its default checks.
- Existing entries keep working unchanged.

## Screens

Every screen that shows, asks for or links to repos, and what it does after this change.

### New goal (`console/src/features/goals/NewGoal.tsx`)

| Panel | Today | After |
|---|---|---|
| 1. Where does the work come from? | Three choices | Unchanged |
| 2. Where is the work? | Repositories (required for every source), then the tracker and its settings | The tracker and its settings only. With one tracker in use the panel is answered and folds at once |
| 3. Issues that already exist | Label suggestions and the issue picker read the chosen repos | They read every repo the operator can reach. The picker's repo filter starts at "All". Each picked issue shows its repo |
| 3. A spec, as a file in a repo | The file is picked from one of the goal's repos; with none chosen it says "Choose the goal's repository above first" | The control has its own repo picker over the operator's reachable repos, then the file list. That message goes away |
| 3. A spec as an epic, issue or upload; I'll describe it | Unchanged by repos | Unchanged |
| 4. How it runs | Unchanged by repos | Unchanged |
| Save buttons | Refuse a goal with no repo | Take a goal with no repo. A goal that runs issues as they are and has a Jira issue naming no repo offers only "Save and plan", with the reason |

With no GitHub connection, panel 3 shows "Connect GitHub" in place of any control that reads GitHub. A goal that
needs nothing from GitHub until planning (an upload, a description) can still be written first; "Save and plan"
then asks for the connection.

`docs/specs/new-goal-progressive.md` said "Repositories come before the tracker: every source needs them". This
spec replaces that decision.

### Other screens

| Screen | File | Today | After |
|---|---|---|---|
| Plan panel | `goals/GoalPlan.tsx` | Each draft shows its repo as a badge. The draft form's Repo is a `Select` of the goal's repos; a new draft defaults to the goal's first repo | Repos listed above the drafts with the planner's reason and draft count; drafts grouped by repo; a repo new to the goal marked "new". The draft form's Repo is a `PickedName` over the operator's reachable repos, defaulting to the repo of the group the draft was added in |
| Goal page, Repositories field | `goals/GoalPage.tsx`, `goals/GoalFields.tsx` | Editable checkboxes of registered repos | Before a plan is approved: "Chosen by the plan". After: the repos, each linking to GitHub. Add with a `PickedName` over reachable repos; remove one that has no open task |
| Goal page, who it acts as | `goals/GoalPage.tsx` | Not shown | A line "Acts as <GitHub login>" with "Act as me" for another operator, and "Reconnect GitHub" when the connection is broken |
| Goal page, waiting issues and empty states | `goals/GoalPage.tsx` | Reads the goal's label in each of `goal.repos`; says "in the goal's repos" | With no repos yet, reads the label across the operator's reachable repos; the sentences drop "in the goal's repos" until there are some |
| Goal page, Add issues | `goals/IssuesPicker.tsx` | Browses the goal's repos, or every registered repo when the goal has none | Browses the goal's repos first, with "All my repos" as a choice; an issue from a repo the goal lacks adds that repo |
| Label field | `goals/LabelField.tsx` | Suggests labels from the picked repos, or every registered repo | Suggests labels from the goal's repos, or the operator's reachable repos when it has none |
| Follow-on goal | `goals/NextSteps.tsx` | "takes this goal's repos and settings" | Takes this goal's settings and starts with its repos as the planner's starting points; the sentence says so |
| Sizing a split | `goals/sizing.tsx` | "Each issue is created in <repo>" | Unchanged |
| Goals list | `goals/GoalsPage.tsx` | A Repos column | A goal with none yet shows "Chosen by the plan" |
| Tracker settings on a goal | `trackers/GoalTrackerFields.tsx` | Lists that depend on repos (GitHub's assignable people) are scoped to the goal's repos | With no repos yet, such a list says it is available once the plan is approved, and takes a typed value |
| Tasks list, task page, tiles, pull request rows | `tasks/TasksPage.tsx`, `tasks/TaskPage.tsx`, `components/TileGrid.tsx`, `overview/PullRequestsRow.tsx` | Show the task's repo | Unchanged. The task page adds the repos the task could read |
| Task remedies | `tasks/Remedies.tsx` | "No checks" links to Configuration, Repositories | Unchanged: default checks are still set there |
| Settings | `settings/SettingsPage.tsx` | "Repositories": the operator's default repos for New goal | That field is removed. A GitHub section is added: the connected login, the orgs it covers, Connect, Reconnect and Disconnect |
| Getting started | `overview/GettingStarted.tsx` | Step "a repository": done when one is registered, links to Configuration, Repositories, speaks of the GitHub App | Step "Connect GitHub": done when the operator has a working connection; its button starts the connection |
| Configuration, Repositories | `config/ReposView.tsx`, `config/ConfigPage.tsx` | "The repo registry": Add repo from the repos the App is installed on | Titled "Repo defaults". Says no repo needs an entry. Add picks from the admin's reachable repos. Still admin-only, like the rest of Configuration |
| Command palette | `components/CommandPalette.tsx` | "Repositories" opens Configuration, Repositories | Renamed "Repo defaults" |
| Help | `help/topics.ts` | "A goal is what you want done, in your words, with the repos it touches…" | "…in your words. The planner works out which repos it touches, and you approve them with the plan." A topic on connecting GitHub is added |
| Performance | `features/performance` | Costs can be broken down and filtered by repository | Unchanged. A goal's costs now spread over several repos |

Every new repo picker follows the pickers rule: it offers the operator's reachable repos as they type, refuses a
name the list lacks, marks a saved one that no longer matches, and, when the list cannot be read, says why and
takes a typed `owner/name`.

### What needs the operator

New actions in `planner/actions.py`, each with its sentence and its button, served through `GET /v1/needs`:

| Need | Button |
|---|---|
| A goal's operator has no working GitHub connection | Reconnect GitHub |
| A goal's operator can no longer push to one of its repos | Act as me, or remove the repo |
| A labeled issue is in a repo the goal does not have | Add the repo to the goal |
| An org hides its repos from the connection until the operator authorizes it on GitHub | Open GitHub's authorization page |

## Services

| Area | File | Change |
|---|---|---|
| GitHub as an operator | new `github_user.py` | The sign-in exchange, the stored connection, and REST and git credentials for an operator. Built on `retrying.async_transport()` |
| Reachable repos | new `planner/reach.py` | The operator's repos with push or pull access, held for a short time and reread on request |
| Choosing the credential | `run_service/code_host.py` | Every call that takes a token (clone, push, open pull request, checks, merge, comments) uses the goal operator's connection when the goal has one, and the App otherwise. Local bare repos are unchanged |
| Goal create and edit | `planner/service.py`, `planner/store.py` | `repos` optional. A named repo is checked against the operator's reach, not the registry. `acts_as` set from the caller |
| Task create | `tasks/service.py` | The registry check becomes: the goal's operator can push to the repo. A task with no goal keeps today's check |
| Repo lists | `planner/service.py` | `GET /v1/repos/reachable` added. `GET /v1/repos` and `GET /v1/repos/available` unchanged for the Repo defaults page |
| Spec files and repo issues | `planner/spec_files.py`, `planner/service.py` | Read with the caller's connection, for any repo they can reach, in place of "only a repo in the registry" |
| Planning | `planner/planning.py`, `prompts/` | The catalog in the prompt, several read-only clones, a tool to ask for more repos, a reason per repo, checks per repo. A new prompt file beside `plan-v1.txt`; plans in flight finish on the old one |
| Plan validation and approval | `planner/plans.py`, `planner/planning.py` | A draft's repo must be pushable by the goal's operator. Approval writes the drafts' repos to `goal.repos`. The epic goes in the repo with the most drafts |
| Sweep | `planner/sweep.py`, `trackers/github.py`, `trackers/jira.py` | A goal with no repos sweeps nothing until it has some. The hold for a labeled issue in a new repo. The Jira hold for a missing `Repo:` line stays for goals that are not planned |
| Runs | `run_service/shared.py`, `run_service/workflows.py`, `run_service/activities.py`, `images/harness-pi` | `reads` on the run input; the read-only clones; the start check. Behind `workflow.patched("read-repos")`, with a recorded history of the new path |
| Design doc | `docs/technical-design.md` | The Goals table's `repos` row, the Planner section, the Security section's "installs the meta-harness GitHub App on each registered repo", the console's default-repos sentence, and the Code host component |

## New fields and their defaults

| Field | Where | Default | Existing rows |
|---|---|---|---|
| `github_connection` | new table: operator identity (key), GitHub login, encrypted token, connected at, last checked at, state | No row until the operator connects | None |
| `goal.acts_as` | nullable text | The creating operator, for a goal created in the console. Null for a goal created by a service key | Null: they keep using the App |
| `goal.repos` | existing `text[]` | May now be `{}` | Unchanged |
| `goal.start_repos` | `text[] NOT NULL DEFAULT '{}'` | The repos the operator pointed at on New goal (a spec file's repo). Always read by the planner | `{}` |
| `task.reads` | `text[] NOT NULL DEFAULT '{}'` | The goal's other repos when the task is created | `{}` |
| Draft `repo` | `plan.drafts` entries | Required, as today | Unchanged |
| Draft `checks` | `plan.drafts` entries, optional | The checks the planner found for the draft's repo | Absent |
| Plan `repo_reasons` | `plan` column, `jsonb NOT NULL DEFAULT '{}'` | Repo to the planner's sentence | `{}` |
| `RunTaskInput.reads`, `HarnessRequest.reads` | run input | `[]`, so recorded histories replay | n/a |
| Events | new types, version 1 | `github.connected`, `github.disconnected`, `goal.repos_set` (with what set them: a plan approval, an issue, an operator), `goal.acts_as_changed` | n/a |
| The operator's default repos | planner user setting | No longer read or shown. The stored value is left in place | Ignored |

Every migration only adds. No existing event type changes.

## Settings

| Setting | Default | A bad or missing value |
|---|---|---|
| The GitHub OAuth client id and secret | None | Both unset: connecting is off, the console says so, and goals use the App as today. One set without the other stops the service, naming it |
| The key that encrypts stored tokens | None | Missing while the OAuth client is set stops the service, naming it |
| How long a reachable list is held, seconds | 600 | Falls back to the default through `fallback.py` |
| `MH_GITHUB_APPS` | Unchanged | Unchanged |

## Safety

- A sandbox never holds a GitHub credential. The control plane makes every clone and every push.
- An operator's token reaches everything that operator can, where an App token named one repo. Only control-plane
  code uses it, and it writes only to the repos of an approved plan or of issues the operator chose.
- A goal uses one operator's access. Another operator cannot add a repo to it that they could not push to
  themselves, and taking a goal over rechecks its repos against their access.
- Budgets are unchanged, per goal and per task, and they bound the cost of planning over many repos.
- A read repo's contents go to the model provider, as a changed repo's do today.

## Tests

- No test calls GitHub. The operator connection and the reachable list are tested against a fake transport, as
  `github_app` is.
- The zero-cost end-to-end check gains a goal planned over two local bare repos, and a task that reads one repo
  while changing another.
- `tests/run_service/test_replay.py` gains a recorded history of a run with read repos; the old ones stay.
- Tests that pin "not in the repo registry" on goal and task create change to the new check.
- Console tests cover New goal with no Repositories control, the plan grouped by repo, and each new picker when
  the list cannot be read.

## To settle while building

- Which GitHub sign-in gives the operator's own repo access with no step by an org owner. An OAuth app with the
  `repo` scope is the working assumption; a GitHub App's user tokens reach only repos the App is installed on,
  which would bring the org owner back.
- An org can restrict third-party applications or require single sign-on for tokens. GitHub then hides that
  org's repos until the operator authorizes the connection on GitHub's page. The console must detect this and
  link there.
- Who reviews and who merges when the pull request's author is the operator. GitHub does not let an account
  approve its own pull request, so a repo that requires an approval needs it from someone else.

## Decided (2026-11-24)

1. Access is the operator's own GitHub access. No org owner and no admin is needed to use a repo.
2. The operator does not choose a goal's repos. The planner works them out, and approving the plan approves them.
3. There is no cap on repos.
4. One task changes one repo and opens one pull request.
