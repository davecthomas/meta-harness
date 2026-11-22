# Created by the meta-harness in run t-c5871d1caed3:run-1 on harness:pi-careful/claude:claude-opus-5-5.

"""Each adapter adds a section to an issue's description, keeping what it said, against a fake HTTP service."""

import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from meta_harness import outbound
from meta_harness.github_app import AppConfig, GitHubApps
from meta_harness.trackers import GitHubTracker, OwnTracker, TrackerError
from meta_harness.trackers.base import with_section
from meta_harness.trackers.jira import JiraTracker, Site

SITE = "https://example.atlassian.net"
EARLIER = "Build the store.\n\n## Acceptance checks\n\n- make test\n\nPaths: src/store\n"


async def prepared(destination: str, heading: str, text: str) -> tuple[outbound.Checked, outbound.Checked]:
    """A heading and a text as a caller passes them: through the outbound pipeline, for the destination."""
    return (
        await outbound.prepare(heading, outbound.Context(destination, outbound.TITLE, outbound.FIXED)),
        await outbound.prepare(text, outbound.Context(destination, outbound.BODY, outbound.FIXED)),
    )


class Service:
    """A fake HTTP service: what it was asked, and what it answers by method and path. An answer may be a
    function of the request's body."""

    def __init__(self, answers: dict) -> None:
        self.asked: list[tuple[str, str, dict | None]] = []
        self.answers = answers

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.asked.append((request.method, request.url.path, body))
        answer = self.answers.get((request.method, request.url.path))
        if callable(answer):
            answer = answer(body)
        if answer is None:
            return httpx.Response(404, json={"message": "Not Found"})
        return answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)

    def sent(self, method: str, path: str) -> list[dict | None]:
        return [body for m, p, body in self.asked if (m, p) == (method, path)]


@pytest.fixture
def key_file(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "app.pem"
    path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return path


def github(key_file, answers: dict) -> tuple[GitHubTracker, Service]:
    token = {"token": "tok", "expires_at": "2099-01-01T00:00:00Z"}
    service = Service(
        {
            ("GET", "/repos/o/r/installation"): {"id": 42},
            ("POST", "/app/installations/42/access_tokens"): httpx.Response(201, json=token),
            **answers,
        }
    )
    apps = GitHubApps({"o": AppConfig("o", "1", key_file)}, transport=httpx.MockTransport(service))
    return GitHubTracker(apps, label=lambda: "bot"), service


def jira(answers: dict) -> tuple[JiraTracker, Service]:
    service = Service(answers)
    site = Site(site=SITE)
    return JiraTracker("bot@example.com", "t", lambda: site, httpx.MockTransport(service), label=lambda: "bot"), service


async def test_github_appends_the_section_to_the_issue_body_and_keeps_the_earlier_text(key_file):
    issue = {"number": 7, "title": "Fix it", "body": EARLIER, "state": "open", "labels": []}
    tracker, service = github(
        key_file,
        {
            ("GET", "/repos/o/r/issues/7"): issue,
            ("POST", "/repos/o/r/issues/7/labels"): [],
            ("PATCH", "/repos/o/r/issues/7"): issue,
        },
    )
    heading, text = await prepared(outbound.GITHUB, "Accepted draft", "Store the rows in Postgres.")

    await tracker.add_section("github:o/r#7", heading, text)

    [patch] = service.sent("PATCH", "/repos/o/r/issues/7")
    assert patch == {"body": f"{EARLIER}\n## Accepted draft\n\nStore the rows in Postgres.\n"}
    assert patch["body"].startswith(EARLIER), "the earlier text is kept as it was"
    assert service.sent("POST", "/repos/o/r/issues/7/labels") == [{"labels": ["bot"]}], "labeled before the write"


async def test_github_gives_its_refusal_as_a_tracker_error_with_its_reason(key_file):
    issue = {"number": 7, "title": "Fix it", "body": EARLIER, "state": "open", "labels": []}
    refused = httpx.Response(422, json={"message": "body is too long (maximum is 65536 characters)"})
    tracker, _ = github(
        key_file,
        {
            ("GET", "/repos/o/r/issues/7"): issue,
            ("POST", "/repos/o/r/issues/7/labels"): [],
            ("PATCH", "/repos/o/r/issues/7"): refused,
        },
    )
    heading, text = await prepared(outbound.GITHUB, "Accepted draft", "More.")

    with pytest.raises(TrackerError, match="body is too long") as failed:
        await tracker.add_section("github:o/r#7", heading, text)
    assert (failed.value.status, failed.value.retryable) == (422, False)


async def test_jira_appends_the_section_to_the_description_in_wiki_markup_and_keeps_the_earlier_text():
    earlier = "Make it so.\n\nh2. Acceptance checks\n* {{uv run pytest -q}}\n"
    tracker, service = jira(
        {
            ("GET", "/rest/api/2/issue/VL-7"): {"key": "VL-7", "fields": {"description": earlier}},
            ("PUT", "/rest/api/2/issue/VL-7"): httpx.Response(204),
        }
    )
    heading, text = await prepared(outbound.JIRA, "Accepted draft", "Store the rows in Postgres.")

    await tracker.add_section("jira:VL-7", heading, text)

    puts = service.sent("PUT", "/rest/api/2/issue/VL-7")
    assert puts[0] == {"update": {"labels": [{"add": "bot"}]}}, "labeled before the write"
    description = puts[1]["fields"]["description"]
    assert description == f"{earlier}\nh2. Accepted draft\n\nStore the rows in Postgres.\n"
    assert description.startswith(earlier), "the earlier text is written back as Jira held it"


async def test_jira_gives_its_refusal_as_a_tracker_error_with_its_reason():
    refused = httpx.Response(400, json={"errors": {"description": "The entered text is too long."}})
    tracker, _ = jira(
        {
            ("GET", "/rest/api/2/issue/VL-7"): {"key": "VL-7", "fields": {"description": None}},
            # The label is added; the description is refused.
            ("PUT", "/rest/api/2/issue/VL-7"): lambda body: refused if "fields" in body else httpx.Response(204),
        }
    )
    heading, text = await prepared(outbound.JIRA, "Accepted draft", "More.")

    with pytest.raises(TrackerError, match="description: The entered text is too long") as failed:
        await tracker.add_section("jira:VL-7", heading, text)
    assert (failed.value.status, failed.value.retryable) == (400, False)


def test_a_section_goes_after_the_earlier_text_with_one_blank_line_and_an_empty_body_takes_it_alone():
    assert with_section("Earlier.\n\n\n", "Draft\n  one", "Text.") == "Earlier.\n\n## Draft one\n\nText.\n"
    assert with_section("", "Draft", "Text.", mark="h2.") == "h2. Draft\n\nText.\n"
    with pytest.raises(ValueError, match="heading is empty"):
        with_section("Earlier.", " \n", "Text.")


def refusing_service(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"a refused text reached the tracker: {request.method} {request.url}")


def no_database():
    raise AssertionError("a refused text reached the database")


@pytest.mark.parametrize(
    ("make", "ref", "destination"),
    [
        (lambda key: GitHubTracker(GitHubApps({"o": AppConfig("o", "1", key)}, httpx.MockTransport(refusing_service))),
         "github:o/r#7", outbound.GITHUB),
        (lambda key: JiraTracker("b@example.com", "t", lambda: Site(site=SITE), httpx.MockTransport(refusing_service)),
         "jira:VL-7", outbound.JIRA),
        (lambda key: OwnTracker(no_database), "mh:i-0123456789ab", outbound.OWN),
    ],
    ids=["github", "jira", "own"],
)  # fmt: skip
async def test_a_plain_string_or_a_text_prepared_for_another_place_is_refused(key_file, make, ref, destination):
    tracker = make(key_file)
    heading, text = await prepared(destination, "Accepted draft", "Text.")
    other = outbound.JIRA if destination != outbound.JIRA else outbound.GITHUB
    _, elsewhere = await prepared(other, "Accepted draft", "Text.")

    with pytest.raises(TypeError, match="must come through the outbound pipeline"):
        await tracker.add_section(ref, heading, "Text.")
    with pytest.raises(TypeError, match="must come through the outbound pipeline"):
        await tracker.add_section(ref, "Accepted draft", text)
    with pytest.raises(TypeError, match=f"is being posted to {destination}"):
        await tracker.add_section(ref, heading, elsewhere)
    with pytest.raises(TypeError, match="Prepare it with the kind outbound.TITLE"):
        await tracker.add_section(ref, text, text)
