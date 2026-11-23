"""Every HTTP call the meta-harness makes retries transient failures, with backoff, and logs what happened."""

import logging
import re
from pathlib import Path

import httpx
import pytest

from meta_harness import retrying

SRC = Path(__file__).parent.parent / "src" / "meta_harness"
# Each repeats by itself: the edge waits out a service's restart in its forwarding loop, and a new sandbox's
# health is polled until its deadline.
OWN_LOOP = {"edge/app.py", "run_service/sandbox.py"}


def _client(handler, **kwargs):
    return httpx.Client(transport=retrying.transport(httpx.MockTransport(handler), **kwargs))


def _flaky(*steps):
    """A service answering each step in turn, the last one for good: a status, or an exception to raise."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        step = steps[min(len(seen), len(steps) - 1)]
        seen.append(request)
        if isinstance(step, type):
            raise step("no", request=request)
        return httpx.Response(step, headers={} if step != 429 else {"Retry-After": "1"}, json={})

    return handler, seen


def test_every_client_is_built_with_the_retrying_transport():
    missing = []
    for file in SRC.rglob("*.py"):
        name = file.relative_to(SRC).as_posix()
        text = file.read_text()
        for match in re.finditer(r"httpx\.(?:Async)?Client\(", text):
            depth, end = 1, match.end()
            while depth:
                depth += {"(": 1, ")": -1}.get(text[end], 0)
                end += 1
            if "retrying." not in text[match.start() : end] and name not in OWN_LOOP:
                missing.append(f"{name}: {text[match.start() : end][:60]}")
    assert missing == [], "build each with transport=retrying.transport() or retrying.async_transport()"


@pytest.mark.parametrize("failure", [httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout])
def test_a_request_that_never_arrived_is_repeated_whatever_its_method(failure):
    handler, seen = _flaky(failure, failure, 201)
    assert _client(handler).post("https://x.test/make").status_code == 201
    assert len(seen) == 3


@pytest.mark.parametrize("status", [429, 503])
def test_a_request_turned_away_is_repeated_even_when_it_changes_something(status):
    handler, seen = _flaky(status, 200)
    assert _client(handler).post("https://x.test/make").status_code == 200 and len(seen) == 2


@pytest.mark.parametrize("step", [500, 502, 504, httpx.ReadTimeout, httpx.RemoteProtocolError])
def test_a_change_that_may_have_been_made_is_not_sent_twice(step):
    handler, seen = _flaky(step, 200)
    client = _client(handler)
    if isinstance(step, int):
        assert client.post("https://x.test/make").status_code == step
    else:
        with pytest.raises(step):
            client.post("https://x.test/make")
    assert len(seen) == 1


@pytest.mark.parametrize("step", [500, 502, 504, httpx.ReadTimeout, httpx.RemoteProtocolError])
def test_a_read_and_a_post_marked_safe_are_repeated_after_any_transient_failure(step):
    handler, seen = _flaky(step, 200)
    assert _client(handler).get("https://x.test/read").status_code == 200 and len(seen) == 2
    handler, seen = _flaky(step, 200)
    assert _client(handler, safe=retrying.always).post("https://x.test/find").status_code == 200 and len(seen) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
def test_a_refusal_that_would_repeat_is_returned_at_once(status):
    handler, seen = _flaky(status)
    assert _client(handler).get("https://x.test/read").status_code == status and len(seen) == 1


def test_a_service_that_stays_down_is_given_up_on_and_every_step_is_logged(caplog):
    handler, seen = _flaky(httpx.ConnectTimeout)
    with caplog.at_level(logging.INFO, "meta_harness.retrying"), pytest.raises(httpx.ConnectTimeout):
        _client(handler).get("https://x.test/read?secret=1")
    assert len(seen) == retrying.DEFAULT.attempts
    levels = [r.levelname for r in caplog.records]
    assert levels == ["WARNING"] * (retrying.DEFAULT.attempts - 1) + ["ERROR"]
    assert "GET x.test/read failed after 5 tries" in caplog.text and "secret" not in caplog.text


def test_a_call_that_recovers_says_so(caplog):
    handler, _ = _flaky(503, 200)
    with caplog.at_level(logging.INFO, "meta_harness.retrying"):
        _client(handler).get("https://x.test/read")
    assert "GET x.test/read succeeded on try 2" in caplog.text


def test_the_wait_a_service_asks_for_is_honored_and_backoff_grows_within_its_cap(monkeypatch):
    waits = []
    monkeypatch.setattr(retrying, "_sleep", waits.append)
    handler, _ = _flaky(429, 200)
    _client(handler).get("https://x.test/read")
    assert waits == [1.0]
    backoffs = [retrying.backoff(retrying.Policy(base_seconds=1.0, cap_seconds=4.0), n) for n in range(1, 6)]
    assert 0.5 <= backoffs[0] <= 1.0 and 1.0 <= backoffs[1] <= 2.0 and all(2.0 <= b <= 4.0 for b in backoffs[2:])


def test_a_wait_longer_than_the_deadline_gives_up_instead_of_hanging(caplog):
    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "3600"})

    assert _client(handler).get("https://x.test/read").status_code == 429
    assert "failed after 1 tries" in caplog.text


def test_github_saying_when_its_rate_limit_resets_counts_as_a_wait():
    limited = httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"})
    assert retrying.asked_wait(limited) > 0 and retrying.asked_wait(httpx.Response(403)) is None


async def test_the_async_transport_retries_too():
    handler, seen = _flaky(httpx.ConnectTimeout, 502, 200)
    transport = retrying.async_transport(httpx.MockTransport(handler))
    async with httpx.AsyncClient(transport=transport) as client:
        assert (await client.get("https://x.test/read")).status_code == 200
    assert len(seen) == 3


def test_a_call_no_person_waits_on_outlasts_a_rate_limits_whole_window(monkeypatch):
    """The HTTP Gateway's 429 asks for up to its 60-second window. The default deadline gives up on that."""
    waits = []
    monkeypatch.setattr(retrying, "_sleep", waits.append)

    def handler(request):
        if not waits:
            return httpx.Response(429, headers={"Retry-After": "60"})
        return httpx.Response(200, json={})

    assert _client(handler).post("https://x.test/v1/structured").status_code == 429
    assert _client(handler, policy=retrying.PATIENT).post("https://x.test/v1/structured").status_code == 200
    assert waits == [60.0]
