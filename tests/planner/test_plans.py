"""A plan's drafts: which hold together, in what order their issues are created, and how a body names them."""

from meta_harness.planner import planning, plans

REPOS = ["o/a", "o/b"]


def draft(draft_id: str, depends_on: tuple = (), repo: str = "o/a", **fields) -> dict:
    body = f"Build {draft_id}.\n\n## Acceptance checks\n\n- make test"
    return {
        "draft_id": draft_id,
        "repo": repo,
        "title": f"Build {draft_id}",
        "body": body,
        "labels": [],
        "depends_on": list(depends_on),
        **fields,
    }


def test_a_plan_whose_drafts_hold_together_is_valid():
    drafts = [draft("store"), draft("api", ("store", "o/b#12")), draft("ui", ("api",), repo="o/b")]
    assert plans.validate(drafts, REPOS) == []


def test_each_reason_a_plan_is_invalid_names_its_draft():
    assert plans.validate([], REPOS) == ["the plan has no drafts"]
    reasons = plans.validate(
        [
            draft("store", ("nothing-here",)),
            draft("store"),
            draft("api", repo="o/elsewhere"),
            draft("ui", body="No checks named."),
            draft("Bad Id"),
            draft("blank", title=""),
        ],
        REPOS,
    )
    assert "draft id 'store' is used by 2 drafts" in reasons
    assert "store: depends on nothing-here, which is no draft and no issue (owner/repo#number)" in reasons
    assert "api: repo o/elsewhere is not one of the goal's (o/a, o/b)" in reasons
    assert "ui: its body needs a '## Acceptance checks' section" in reasons
    assert "Bad Id: a draft id is lowercase letters, digits and hyphens, at most 40" in reasons
    assert "blank: needs a title" in reasons


def test_drafts_that_depend_on_each_other_in_a_cycle_are_refused_with_the_cycle():
    reasons = plans.validate([draft("a", ("b",)), draft("b", ("c",)), draft("c", ("a",)), draft("d")], REPOS)
    assert reasons == ["the plan's tasks depend on each other in a cycle: a -> b -> c -> a"]


def test_issues_are_created_with_each_draft_after_what_it_depends_on():
    drafts = [draft("ui", ("api",)), draft("api", ("store",)), draft("docs"), draft("store")]
    assert [d["draft_id"] for d in plans.ordered(drafts)] == ["store", "api", "ui", "docs"]


def test_the_last_call_for_a_draft_id_is_the_draft_in_first_seen_order():
    events = [
        {"position": 3, "payload": draft("api", title="Second thoughts")},
        {"position": 1, "payload": draft("api")},
        {"position": 2, "payload": {**draft("store"), "labels": ["x", ""], "extra": "dropped"}},
    ]
    drafts = plans.latest_drafts(events)
    assert [(d["draft_id"], d["title"]) for d in drafts] == [("api", "Second thoughts"), ("store", "Build store")]
    assert drafts[1]["labels"] == ["x"] and "extra" not in drafts[1]


def test_an_issues_body_names_the_issues_its_dependencies_became():
    created = {"store": "github:o/a#41", "shared": "github:o/b#7"}
    body = planning.issue_body(draft("api", ("store", "shared", "o/b#12")), created)
    assert body.endswith("- make test\n\nDepends on: #41, o/b#7, o/b#12\n")
    assert planning.issue_body(draft("store"), {}) == draft("store")["body"] + "\n"


def test_a_goal_with_no_label_of_its_own_gets_one_from_its_id():
    assert planning.goal_label({"id": "g-7bd41fcd41c3"}) == "meta-harness:goal-g-7bd41f"


def test_the_planning_settings_name_a_candidate_and_a_spend_limit():
    planning.check_settings()
    config = planning.settings()
    assert config["candidate"].startswith("harness:pi-plan/")


# --- A plan over the issues a goal already has ---------------------------------------------------------

ISSUES = [{"ref": f"jira:VL-{n}", "repo": "o/r", "title": f"Issue {n}"} for n in (1, 2, 3)]


def item(ref: str, decision: str = "keep", **fields) -> dict:
    return plans.clean_item({"ref": ref, "decision": decision, "reason": "because", **fields})


def test_every_issue_gets_one_decision_and_one_the_planner_said_nothing_of_is_kept():
    decided = plans.complete([item("jira:VL-2", "leave_out"), item("jira:VL-9")], ISSUES)

    assert [(i["ref"], i["decision"]) for i in decided] == [
        ("jira:VL-1", "keep"),
        ("jira:VL-2", "leave_out"),
        ("jira:VL-3", "keep"),
        ("jira:VL-9", "keep"),
    ]
    [unknown] = plans.validate([], ["o/r"], decided, ISSUES)
    assert unknown == "jira:VL-9: is not one of the goal's issues, so the plan cannot decide on it"


def test_a_plan_of_kept_issues_needs_no_new_task_but_must_run_something():
    kept = plans.complete([], ISSUES)
    assert plans.validate([], ["o/r"], kept, ISSUES) == []
    none = [item(i["ref"], "leave_out") for i in ISSUES]
    assert plans.validate([], ["o/r"], none, ISSUES) == ["the plan runs nothing: it has no new task and keeps no issue"]


def test_a_split_names_drafts_that_replace_the_issue_and_nothing_waits_on_what_the_plan_takes_out():
    drafts = [draft("store", repo="o/r"), draft("api", ("store", "jira:VL-1"), repo="o/r")]
    good = [
        item("jira:VL-1"),
        item("jira:VL-2", "split", replaced_by=["store", "api"]),
        item("jira:VL-3", depends_on=["api", "jira:VL-1"]),
    ]
    assert plans.validate(drafts, ["o/r"], good, ISSUES) == []

    bad = [
        item("jira:VL-1", "split"),
        item("jira:VL-2", "split", replaced_by=["nope"]),
        item("jira:VL-3", "merge", depends_on=["jira:VL-3"]),
    ]
    reasons = plans.validate([draft("store", ("jira:VL-1",), repo="o/r")], ["o/r"], bad, ISSUES)
    assert "store: waits on jira:VL-1, which the plan splits: wait on the tasks that replace it" in reasons
    assert "jira:VL-1: is split, so it names the new tasks that replace it" in reasons
    assert "jira:VL-2: is replaced by nope, which is no draft of the plan" in reasons
    assert "jira:VL-3: its decision is 'merge', not one of keep, split, leave_out" in reasons
    left = [item("jira:VL-1", "leave_out"), item("jira:VL-2", depends_on=["jira:VL-1"]), item("jira:VL-3")]
    assert plans.validate([], ["o/r"], left, ISSUES) == ["jira:VL-2: waits on jira:VL-1, which the plan leaves out"]


def test_a_cycle_through_an_existing_issue_and_a_new_task_is_refused():
    drafts = [draft("store", ("jira:VL-1",), repo="o/r")]
    looped = [item("jira:VL-1", depends_on=["store"]), item("jira:VL-2"), item("jira:VL-3")]

    [reason] = plans.validate(drafts, ["o/r"], looped, ISSUES)

    assert reason.startswith("the plan's tasks depend on each other in a cycle: ") and "jira:VL-1" in reason


def test_a_planning_runs_last_word_on_an_issue_and_its_last_summary_are_the_plans():
    events = [
        {"position": 1, "payload": {"ref": "jira:VL-1", "decision": "split", "reason": "Big", "replaced_by": ["a"]}},
        {
            "position": 2,
            "payload": {"ref": "jira:VL-1", "decision": "keep", "reason": " Fine after all ", "depends_on": ["a", "a"]},
        },
    ]
    assert plans.latest_items(events) == [
        {"ref": "jira:VL-1", "decision": "keep", "reason": "Fine after all", "depends_on": ["a"], "replaced_by": []}
    ]
    summaries = [
        {"position": 4, "payload": {"summary": " Second. ", "gaps": ["None covers export", ""]}},
        {"position": 3, "payload": {"summary": "First."}},
    ]
    assert plans.latest_summary(summaries) == ("Second.", ["None covers export"])
    assert plans.latest_summary([]) == ("", [])


def test_the_shipped_configuration_plans_every_goal_and_its_settings_are_checked():
    from meta_harness.config import kinds

    shipped = planning.settings()
    assert shipped["goals"] == "all", "every goal is planned before its tasks run, as shipped"
    assert planning.tracks_new_tasks() is True
    planning.check_settings()
    assert kinds.check_planning(shipped, None) == []

    def reasons(**changed) -> list[str]:
        return kinds.check_planning({**shipped, **changed}, None)

    assert reasons(goals="some") == [
        "goals must be one of all, from_description: which goals are planned before their tasks run"
    ]
    assert reasons(max_issues=0)[0].startswith("max_issues must be a whole number above 0")
    assert reasons(max_usd="0")[0].startswith("max_usd must be an amount above 0")
    assert reasons(candidate="claude:x")[0].startswith("candidate must be harness:<profile>/<engine>:<model>")
    assert reasons(track_new_tasks="yes")[0].startswith("track_new_tasks must be true or false")
    assert reasons(goals="from_description", track_new_tasks=False) == []


def test_the_planning_candidates_profile_must_exist_and_be_able_to_record_a_plan():
    from meta_harness.config import kinds

    shipped = planning.settings()
    profiles = {
        "profiles": {
            "profiles": {"pi-plan": {"tools": ["read", "create_issue"]}, "pi-lean": {"tools": ["read", "write"]}}
        }
    }

    assert kinds.check_planning(shipped, profiles) == []
    [gone] = kinds.check_planning({**shipped, "candidate": "harness:pi-gone/claude:m"}, profiles)
    assert gone == "candidate names the harness profile pi-gone, which the profiles lack: pick one of pi-lean, pi-plan"
    [toolless] = kinds.check_planning({**shipped, "candidate": "harness:pi-lean/claude:m"}, profiles)
    assert "has no create_issue tool" in toolless and "Pick a planning profile" in toolless


def test_a_draft_that_names_a_whole_top_level_directory_gets_a_gap_saying_what_it_costs():
    from meta_harness.planner import plans

    drafts = [
        {"draft_id": "a", "title": "Capture summaries", "body": "Do it.\n\nPaths: src/meta_harness/run_service/review.py, tests"},
        {"draft_id": "b", "title": "Narrow", "body": "Do it.\n\nPaths: console/src/features/goals/GoalPage.tsx, Makefile.am"},
    ]
    [gap] = plans.broad_path_gaps(drafts)
    assert gap.startswith("Capture summaries names the whole tests directory as its paths")
    assert gap.endswith("Name the files it changes instead")
    assert plans.broad_paths("Paths: tests, src, docs/x.md") == ["tests", "src"]
