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
    assert reasons == ["the drafts depend on each other in a cycle: a -> b -> c -> a"]


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
