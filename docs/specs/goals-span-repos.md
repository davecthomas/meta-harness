# Goals that span repos

Status: approved in outline by the owner on 2026-11-24. Muxxy builds it as a goal from this file.

## Why

A problem often crosses repos: an API in one, its client in another, the infrastructure in a third. Today a goal
can only touch repos an admin registered one by one, in orgs where an owner installed Muxxy's GitHub App, and
the operator must name the goal's repos before anyone has looked at the problem. Other AI coding tools work with
whatever the signed-in person can already reach on GitHub. Muxxy should too.

## The flow

1. The operator connects their GitHub account, once.
2. The operator creates a goal. They do not choose its repos.
3. The planner works out which repos the goal needs, reads them, and names them in the plan.
4. The operator approves the plan. Those repos become the goal's repos.
5. Each task changes one repo and opens one pull request there.

No org owner installs anything. No admin registers a repo. Nothing limits how many repos a goal reads or changes;
the goal's budget is the bound.

## What happens today

- `goal.repos` is already a list and each task already has one repo (`task.repo`). Neither changes.
- A repo is usable only when it is a key in the `repos` config (`planner/repos.py`), which only an admin can save,
  and only where an org owner installed the GitHub App named for that owner in `MH_GITHUB_APPS` (`github_app.py`).
- New goal asks for the goal's repositories first, and every other choice on the page follows from them.
- The planning run clones only the goal's first repo (`planner/planning.py`), so the planner writes a plan for a
  goal with several repos without reading the others.
- A draft that names a repo outside the goal's list is invalid (`planner/plans.py`).
- An operator signs in with Google. The services know them by their email (`X-MH-Actor`, set by the edge), which is
  also `goal.created_by`. Nothing links it to a GitHub account.
- Each operator can save default repos for New goal in their settings (`planner.repos`), checked against the
  registry.

## What we want

### An operator connects GitHub once

- Anywhere the console needs GitHub and the operator has no connection, it shows one button, "Connect GitHub". It
  opens GitHub's own sign-in and consent screen and returns to the page the operator was on.
- The connection belongs to the signed-in operator. It gives Muxxy what that operator can do on GitHub: read and
  push to repos, open and merge pull requests, read and write issues.
- The control plane stores the operator's token encrypted, never logs it, never puts it in an event, and never
  passes it into a sandbox. Only the control plane writes to GitHub, as now.
- The control plane notices a connection GitHub has revoked or expired the next time it uses it. Everything that
  needs the connection then says so with a "Reconnect GitHub" button.
- The operator can disconnect on the Settings page. Their running goals then stop where they next need GitHub.

### A goal acts as its operator

- A goal created in the console acts as the operator who created it. Its clones, pushes, pull requests, merges,
  checks and issue writes on GitHub use that operator's connection.
- The operator's GitHub account therefore authors its pull requests. Their text still goes through
  `outbound.prepare` and keeps Muxxy's provenance line.
- Another operator can take a goal over on the goal page ("Act as me"). From then on the goal uses their
  connection, and Muxxy checks its repos against their access.
- A goal with no operator (one created before this change, or by a service with an API key) keeps using the GitHub
  App on the repos it reaches, exactly as today.

### The operator does not choose repos

- New goal has no Repositories control. No control on the page waits for a repo.
- Where a choice on the page needs to look inside one repo (picking a spec file from a repo, browsing a repo's
  issues), that control has its own repo picker, fed by the repos the operator can reach. A repo picked this way
  tells the planner where to start. It does not limit the plan.
- The operator may save a goal with no repos. It gets them when the operator approves its plan.

### A spec is still read from a repo

- Creating a goal from a spec file in a repo stays. The operator picks any repo they can reach, then any Markdown
  or text file in it, from a list or by typing its path, and the console reads the file's text as the goal's spec.
- This needs only read access to that repo. The goal does not have to change it.
- The console reads the file with the operator's own connection, so it needs no registry entry and no App
  installation.
- The goal keeps the text the planner worked from, as now, and the file's repo is one of the planner's starting
  points.

### The planner works out the repos

- The planning run gets the goal and the list of repos its operator can reach: each one's name, description,
  main language and when someone last pushed to it.
- It names the repos it needs to read. The control plane clones each one read-only into the planning sandbox, on
  its default branch, side by side. The planner may ask for more as it learns more.
- The control plane always clones a repo the operator pointed at on New goal, a repo named in the goal's text or
  spec, and the repo of any issue already on the goal.
- A planning task changes no repo. Its own `repo` is empty, its workspace holds only the read-only clones at
  `/workspace/<owner>/<name>`, and it publishes nothing.
- Each draft in the plan names the one repo it changes. The plan says, in a sentence per repo, why the goal needs
  that repo.
- A draft is valid only when the goal's operator can push to its repo. One that names a repo they can only read,
  or cannot reach, is invalid, and the plan says which and what to do.
- For each repo it plans work in, the planner records the checks it found in the repo's `AGENTS.md` or CI
  configuration. They become the acceptance checks of the drafts in that repo that name none.

### Approving the plan approves the repos

- The plan panel lists the repos above the drafts, each with the planner's reason and its draft count, and groups
  the drafts by repo.
- The operator can change a draft's repo before approving, picked from the repos they can reach.
- Approving the plan sets the goal's repos to the repos of its drafts. No task touches a repo before that.
- A replan that adds a repo marks it as new, and the operator approves it the same way.

### Goals that run issues as they are

- A GitHub issue names its repo by where it lives. A Jira issue names it with a `Repo:` line or a `repo:` label.
  An issue the operator picks on New goal, or one with the goal's label when the operator creates the goal,
  brings its repo into the goal: the operator chose it.
- A Jira issue that names no repo cannot run as it is. The planner plans a goal with such an issue first, and New
  goal says so; the planner assigns the repo and writes the `Repo:` line onto the issue through
  `outbound.prepare`.
- After a goal has started, the sweep holds an issue that gains the goal's label in a repo the goal does not have.
  It appears in what needs the operator, with a button that adds the repo to the goal.

### A task works in one repo and can read the others

- A task clones the repo it changes at `/workspace`, changes it and opens one pull request, as now.
- The control plane clones the goal's other repos read-only beside it, at `/workspace-reads/<owner>/<name>`, and
  the task's prompt lists them. The control plane only ever publishes `/workspace`, so it never pushes anything
  written to a read repo.
- `depends_on` orders tasks across repos as it does within one repo: a task starts when the pull request it
  depends on has merged.
- No pull request spans repos and no merge is atomic across repos. The planner plans a change that must land in
  two repos as an ordered pair, the side that keeps working alone first.
- Before a task starts, the run confirms the operator's connection works and can still push to the task's repo. If
  not, the task stops there and says what to do.

### Review and merge

- The model reviewer already posts its verdict as a comment, never as an approval, because GitHub refuses an
  approval from a pull request's own author. That stays true with the operator as author.
- A merge uses the goal operator's connection: when the goal merges by itself, and when an operator asks. Muxxy
  still holds the paths it holds for an operator's own merge (`.github/`, `deploy/`, `versioning.py`).
- A repo whose branch rules require an approving review needs it from a person other than the goal's operator. The
  task waits and says so, with a link to the pull request.
- GitHub still refuses a push that changes workflow files: the connection does not ask for that permission.
- Every pull request Muxxy opens still has the `ai:meta-harness` label.

### The repo registry becomes optional

- A repo needs no entry in the `repos` config. An entry only overrides a default for that repo: its tracker and
  its default checks.
- Existing entries keep working unchanged.
- Saving the config no longer refuses to drop a repo an open goal names: dropping an entry only drops its
  overrides.

## Screens

Every screen that shows, asks for or links to repos, and what it does after this change.

### New goal (`console/src/features/goals/NewGoal.tsx`)

| Panel | Today | After |
|---|---|---|
| 1. Where does the work come from? | Three choices | Unchanged |
| 2. Where is the work? | Repositories (required for every source), then the tracker and its settings | The tracker and its settings only. With one tracker in use the panel answers itself and folds at once |
| 3. Issues that already exist | Label suggestions and the issue picker read the chosen repos | They read every repo the operator can reach. The picker's repo filter starts at "All". Each picked issue shows its repo |
| 3. A spec, as a file in a repo | The operator picks the file from one of the goal's repos; with none chosen it says "Choose the goal's repository above first" | The control has its own repo picker over the operator's reachable repos, then the file list. That message goes away |
| 3. A spec as an epic, issue or upload; I'll describe it | Unchanged by repos | Unchanged |
| 4. How it runs | Unchanged by repos | Unchanged |
| Save buttons | Refuse a goal with no repo | Take a goal with no repo. A goal that runs issues as they are and has a Jira issue naming no repo offers only "Save and plan", with the reason |

With no GitHub connection, panel 3 shows "Connect GitHub" in place of any control that reads GitHub. The operator
can still write a goal that needs nothing from GitHub until planning (an upload, a description); "Save and plan"
then asks for the connection.

`docs/specs/new-goal-progressive.md` said "Repositories come before the tracker: every source needs them". This
spec replaces that decision.

### Other screens

| Screen | File | Today | After |
|---|---|---|---|
| Plan panel | `goals/GoalPlan.tsx` | Each draft shows its repo as a badge. The draft form's Repo is a `Select` of the goal's repos; a new draft defaults to the goal's first repo | Repos listed above the drafts with the planner's reason and draft count; drafts grouped by repo; a repo new to the goal marked "new". The draft form's Repo is a `PickedName` over the operator's reachable repos, defaulting to the repo of the group the operator added the draft in |
| Goal page, Repositories field | `goals/GoalPage.tsx`, `goals/GoalFields.tsx` | Editable checkboxes of registered repos; saving none fails with "Pick at least one repo." | Before the operator approves a plan: "Chosen by the plan". After: the repos, each linking to GitHub. Add with a `PickedName` over reachable repos; remove one that has no open task |
| Goal page, who it acts as | `goals/GoalPage.tsx` | Not shown | A line "Acts as <GitHub login>" with "Act as me" for another operator, and "Reconnect GitHub" when the connection is broken |
| Goal page, waiting issues and empty states | `goals/GoalPage.tsx` | Reads the goal's label in each of `goal.repos`; says "in the goal's repos" | With no repos yet, reads the label across the operator's reachable repos; the sentences drop "in the goal's repos" until there are some |
| Goal page, Add issues | `goals/IssuesPicker.tsx` | Browses the goal's repos, or every registered repo when the goal has none | Browses the goal's repos first, with "All my repos" as a choice; an issue from a repo the goal lacks adds that repo |
| Label field | `goals/LabelField.tsx` | Suggests labels from the picked repos, or every registered repo | Suggests labels from the goal's repos, or the operator's reachable repos when it has none |
| Follow-on goal | `goals/NextSteps.tsx` | "takes this goal's repos and settings" | Takes this goal's settings and starts with its repos as the planner's starting points; the sentence says so |
| Sizing a split | `goals/sizing.tsx` | "Each issue is created in <repo>" | Unchanged |
| Goals list | `goals/GoalsPage.tsx` | A Repos column | A goal with none yet shows "Chosen by the plan" |
| Tracker settings on a goal | `trackers/GoalTrackerFields.tsx` | The goal's repos scope the lists that depend on repos (GitHub's assignable people) | With no repos yet, such a list says it is available once the operator approves the plan, and takes a typed value |
| Tasks list, task page, tiles, pull request rows | `tasks/TasksPage.tsx`, `tasks/TaskPage.tsx`, `components/TileGrid.tsx`, `overview/PullRequestsRow.tsx` | Show the task's repo | Unchanged. The task page adds the repos the task could read |
| Task remedies and diagnosis | `tasks/Remedies.tsx`, `tasks/diagnosis.ts` | "No checks" links to Configuration, Repositories; the diagnosis speaks of "its repo's registry entry" | The link stays, to Repo defaults. The diagnosis says "add checks to the issue, or default checks for the repo" |
| Skill import | `config/SkillsView.tsx` | The Repository picker lists only registered repos | Lists the admin's reachable repos |
| Settings | `settings/SettingsPage.tsx` | "Repositories": the operator's default repos for New goal, with "an admin adds one under Configuration, Repositories" when the registry is empty | That field and its text go. A new GitHub section shows the connected login, the orgs it covers, Connect, Reconnect and Disconnect |
| Getting started | `overview/GettingStarted.tsx` | Step "a repository": done when the registry has one, links to Configuration, Repositories, speaks of the GitHub App | Step "Connect GitHub": done when the operator has a working connection; its button starts the connection |
| Configuration, Repositories | `config/ReposView.tsx`, `config/ConfigPage.tsx` | "The repo registry": Add repo from the repos the App reaches | Titled "Repo defaults". Says no repo needs an entry. Add picks from the admin's reachable repos. Still admin-only, like the rest of Configuration |
| Command palette | `components/CommandPalette.tsx` | "Repositories" opens Configuration, Repositories | Renamed "Repo defaults" |
| Help | `help/topics.ts` | "A goal is what you want done, in your words, with the repos it touches…" | "…in your words. The planner works out which repos it touches, and you approve them with the plan." A new topic covers connecting GitHub |
| Performance | `features/performance` | Costs can be broken down and filtered by repository | Unchanged. A goal's costs now spread over several repos |

Every new repo picker follows the pickers rule: it offers the operator's reachable repos as they type, refuses a
name the list lacks, marks a saved one that no longer matches, and, when it cannot read the list, says why and
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
| Choosing the credential | `run_service/code_host.py` | Each call that takes a token (clone, push, open pull request, checks, merge, comments) uses the goal operator's connection. A goal with none uses the App. Local bare repos keep working as they do |
| Goal create and edit | `planner/service.py`, `planner/store.py` | `repos` optional. The service checks a named repo against the operator's reach in place of the registry. `acts_as` set from the caller |
| Task create | `tasks/service.py` | The registry check becomes: the goal's operator can push to the repo. A task with no goal keeps today's check |
| Sign-in return | `edge/app.py` | A route GitHub returns the operator to after consent. It ties the connection to the signed-in session and refuses a return that does not match the request it started |
| Repo lists | `planner/service.py` | New `GET /v1/repos/reachable`. `GET /v1/repos` unchanged. `GET /v1/repos/available` lists the caller's reachable repos with no entry |
| Labels, repo issues, goal drafts | `planner/service.py` (`/v1/labels`, `/v1/repos/{owner}/{name}/issues`, `/v1/goals/draft`) | Read the caller's reachable repos in place of the registry; with no repo named, the goal's repos, else all reachable |
| User settings | `planner/settings.py`, `planner/service.py` | The service no longer checks `planner.repos` against the registry, and New goal no longer reads it |
| Config save | `planner/service.py` (`in_use`), `config/kinds.py` | The "repo an open goal names cannot leave the registry" refusal goes. The kind's description becomes "Per-repo defaults" |
| GitHub issues | `trackers/github.py` | Issue reads and writes for a goal use its operator's connection, the App otherwise |
| Operator comments on a pull request | `planner/service.py` | Posted with the goal's connection; the text still names the operator who wrote it |
| Costs | `costs/model_source.py` | A run's cost goes to its task's repo. A planning run, which has none, records no repo, in place of the goal's first repo |
| Follow-on goals | `planner/next_steps.py` | A goal started from another takes that goal's repos as starting points; they do not become its repos |
| Spec files and repo issues | `planner/spec_files.py`, `planner/service.py` | Read with the caller's connection, for any repo they can reach, in place of "only a repo in the registry" |
| Planning | `planner/planning.py`, `prompts/` | The catalog in the prompt, several read-only clones, a tool to ask for more repos, a reason per repo, checks per repo. A new prompt file beside `plan-v1.txt`; plans in flight finish on the old one |
| Plan validation and approval | `planner/plans.py`, `planner/planning.py` | A draft's repo must be pushable by the goal's operator. Approval writes the drafts' repos to `goal.repos`. The epic goes in the repo with the most drafts |
| Sweep | `planner/sweep.py`, `trackers/github.py`, `trackers/jira.py`, `trackers/router.py` | A goal with no repos sweeps nothing until it has some. The hold for a labeled issue in a new repo. The Jira hold for a missing `Repo:` line stays for goals that are not planned |
| Runs | `run_service/shared.py`, `run_service/workflows.py`, `run_service/activities.py`, `images/harness-pi` | `reads` on the run input; the read-only clones; the start check. Behind `workflow.patched("read-repos")`, with a recorded history of the new path |
| Design doc | `docs/technical-design.md`, `docs/requirements.md` | Every statement that a repo must be in the registry or that the App must be on each repo. In the design: the Components table (Code host, configuration), the API list (`/v1/repos`, labels, issues, spec files), Goals (`repos`), the repo registry and tracker sections, the console's Repositories, New goal and goal-fields text, Security, and the secrets list. In the requirements: "A goal names its repos" and SEC-1's repo-scoped tokens |

## Code that assumes a goal has a repo

Each of these reads the goal's first repo or refuses an empty list today. After this change a goal may have none
until the operator approves its plan.

| Where | Today | After |
|---|---|---|
| `planner/service.py` `GoalIn.repos`, `GoalPatch.repos` | At least one | May be empty |
| `planner/planning.py` planning task | `repo=goal["repos"][0]`; the prompt says `/workspace` holds it | Empty repo; the prompt lists the clones |
| `planner/plans.py` `validate` | A draft's repo must be one of the goal's | Must be pushable by the goal's operator |
| `planner/planning.py` settle, `planner/draft_sizes.py` | An item with no repo falls back to the goal's first | A draft with no repo is invalid; no fallback |
| `planner/store.py` issue links | Saved only when the repo is one of the goal's | Saved when the goal's operator can push to it; the goal gains the repo where this spec says an issue brings its repo |
| `planner/service.py` add issues (`checked_refs`) | A ref's repo must be one of the goal's | Adds the repo, as above |
| `planner/sweep.py` issue with no repo | Saved against the goal's first repo, held | Saved with no repo, held, or planned as this spec says |
| `trackers/jira.py` `repo_of` | The goal's only repo when it has one; else the `Repo:` line or label | The `Repo:` line or label; with neither, no repo. A single-repo goal keeps today's shortcut |
| `planner/enrich.py`, `planner/assignees.py` | Scope lists to the goal's repos | With none, the operator's reachable repos |
| `costs/model_source.py` | Cost repo is the goal's first | The task's repo |
| `goals/GoalPlan.tsx` new draft | `goal.repos[0]` | The group the operator added it in; none chosen when the plan has no repos |
| `goals/GoalFields.tsx` | "Pick at least one repo." | Removed |

## New fields and their defaults

| Field | Where | Default | Existing rows |
|---|---|---|---|
| `github_connection` | new table: operator identity (key), GitHub login, encrypted token, connected at, last checked at, state | No row until the operator connects | None |
| `goal.acts_as` | nullable text | The creating operator, for a goal created in the console. Null for a goal created by a service key | Null: they keep using the App |
| `goal.repos` | existing `text[]` | May now be `{}` | Unchanged |
| `task.repo` | existing `text NOT NULL` | Empty for a planning task of a goal with no repo yet | Unchanged |
| `goal.start_repos` | `text[] NOT NULL DEFAULT '{}'` | The repos the operator pointed at on New goal (a spec file's repo). The planner always reads them | `{}` |
| `task.reads` | `text[] NOT NULL DEFAULT '{}'` | The goal's other repos when Muxxy creates the task | `{}` |
| Draft `repo` | `plan.drafts` entries | Required, as today | Unchanged |
| Draft `checks` | `plan.drafts` entries, optional | The checks the planner found for the draft's repo | Absent |
| Plan `repo_reasons` | `plan` column, `jsonb NOT NULL DEFAULT '{}'` | Repo to the planner's sentence | `{}` |
| `RunTaskInput.reads`, `HarnessRequest.reads` | run input | `[]`, so recorded histories replay | n/a |
| Events | new types, version 1 | `github.connected`, `github.disconnected`, `goal.repos_set` (with what set them: a plan approval, an issue, an operator), `goal.acts_as_changed` | n/a |
| The operator's default repos | planner user setting | No longer read or shown. The stored value stays in place | Ignored |

Every migration only adds. No existing event type changes: `goal_created` and `task_created` already validate with
an empty `repos` list and an empty `repo`.

## Settings

| Setting | Default | A bad or missing value |
|---|---|---|
| The GitHub OAuth client id and secret | None | Both unset: connecting is off, the console says so, and goals use the App as today. One set without the other stops the service, naming it |
| The key that encrypts stored tokens | None | Missing alongside a configured OAuth client: stops the service, naming it |

All three reach the services as environment values from Secret Manager at start, like every other secret. The
platform stack adds the secrets, in a pull request before the one that reads them.

| Setting | Default | A bad or missing value |
|---|---|---|
| How long a reachable list stays fresh, seconds | 600 | Falls back to the default through `fallback.py` |
| `MH_GITHUB_APPS` | Unchanged | Unchanged |

## Safety

- A sandbox never holds a GitHub credential. The control plane makes every clone and every push.
- An operator's token reaches everything that operator can, where an App token named one repo. Only control-plane
  code uses it, and it writes only to the repos of an approved plan or of issues the operator chose.
- A goal uses one operator's access. Another operator cannot add a repo to it that they could not push to
  themselves, and taking a goal over rechecks its repos against their access.
- Budgets stay as they are, per goal and per task, and they bound the cost of planning over many repos.
- A read repo's contents go to the model provider, as a changed repo's do today.

## Tests

- No test calls GitHub. Tests run the operator connection and the reachable list against a fake transport, as
  `github_app`'s tests do.
- The zero-cost end-to-end check gains a goal planned over two local bare repos, and a task that reads one repo
  while changing another.
- `tests/run_service/test_replay.py` gains a recorded history of a run with read repos; the old ones stay.
- Tests that pin "not in the repo registry" on goal create, goal edit, task create, user settings and spec files
  change to the new check, as do "a goal's repos must be registered and not empty", "a repo an open goal names
  cannot leave the registry" and "available repos are the installed ones the registry lacks".
- Console tests change for New goal, the spec file picker, the goal's fields, the issue picker, the label field,
  Settings, Getting started, Repo defaults and skill import, and cover each new picker when it cannot read the
  list.

## To settle while building

- Which GitHub sign-in gives the operator's own repo access with no step by an org owner. An OAuth app with the
  `repo` scope is the working assumption; a GitHub App's user tokens reach only repos with the App installed,
  which would bring the org owner back.
- An org can restrict third-party applications or require single sign-on for tokens. GitHub then hides that
  org's repos until the operator authorizes the connection on GitHub's page. The console must detect this and
  link there.

## Decided (2026-11-24)

1. Access is the operator's own GitHub access. Using a repo needs no org owner and no admin.
2. The operator does not choose a goal's repos. The planner works them out, and approving the plan approves them.
3. There is no cap on repos.
4. One task changes one repo and opens one pull request.
