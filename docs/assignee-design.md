<!-- Created by the meta-harness in run t-ee10c5915e00:run-1 on harness:pi-lean/claude:claude-sonnet-5-5. -->

# Assignee design

Status: proposed. The goal's owner approves this note before the assign tasks
start. Those tasks fold the decision into `docs/technical-design.md` when they
change the code. Part of VL-14260.

## Problem

The meta-harness never sets an Assignee on the issues it creates or takes in.
Nobody can tell from the tracker who is responsible for an issue.

## Choices for the assignee

| Choice | For | Against |
|---|---|---|
| The goal's owner | Always known. The owner already answers for the goal. | The owner may not be a tracker user. One person gets every issue. |
| The operator who approved the plan | The person who said yes to the work. Spreads issues over operators. | Approval is a console action, so the tracker may not know that identity. Automatic goals have no approving operator. |
| A person named in the goal's tracker settings | Written in the tracker's own terms, so it always maps to a tracker user. | Needs setup. A goal without the setting has no value. |

## Decision

Resolve one assignee per goal, first match wins:

1. The person named in the goal's tracker settings, if set.
2. The operator who approved the plan, if there is one.
3. The goal's owner.

The tracker setting comes first because it holds a tracker identity and needs
no mapping. The approving operator comes next because that person chose the
work. The owner is the fallback because every goal has one.

## Cases

| Case | Assignee |
|---|---|
| An issue created from a plan | The resolved assignee. |
| A part created by a split | The resolved assignee, the same as the parent issue's. |
| An issue a goal takes in with no assignee, when its task starts | The resolved assignee, set once when the task starts. |
| An issue that already has an assignee | Unchanged. The meta-harness never overwrites an assignee. |

## Trackers

| Tracker | Assignee field | Identity |
|---|---|---|
| GitHub | `assignees` on the issue | A GitHub login. The login must be able to take assignments on the repo. |
| Jira | `assignee` | An account id. An email or name is looked up to an account id first. |
| Own tracker | The issue's assignee column | The user id the meta-harness already holds. No lookup. |

A value in the tracker settings is stored in the tracker's own form. An
operator or owner identity is mapped to the tracker's form by email where the
tracker needs it.

## When the tracker does not know the person

The issue is still created. The assign step never blocks creation, a split or
a task start.

The meta-harness then:

1. Leaves the issue with no assignee.
2. Reports the problem on the goal and in the audit trail. The report names
   the issue, the person it tried, the tracker and the error.
3. Says what to do: set a valid person in the goal's tracker settings, or
   assign the issue by hand in the tracker.

Later issues of the goal try the same person again. A fixed setting takes
effect without further steps.

## Out of scope

- Reassigning an issue when ownership changes.
- Assigning more than one person.
- Round-robin or load-based choice.
