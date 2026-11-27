"""No test reaches a host outside its own machine and database (tests/conftest.py, no_outside_network)."""

import socket
import time

import httpx
import pytest

from meta_harness.run_service import activities


def test_an_outside_host_does_not_resolve_and_says_so_at_once():
    began = time.monotonic()
    with pytest.raises(socket.gaierror, match="tests reach no outside host"):
        socket.getaddrinfo("gateway.test", 80)
    with pytest.raises(httpx.ConnectError):
        httpx.get("http://gateway.test/v1/structured", timeout=30)
    # An address that never answers is refused too, where it used to hang until a wait in the code ran out.
    with pytest.raises(OSError, match="tests reach no outside host"):
        socket.create_connection(("10.255.255.1", 80), timeout=30)
    assert time.monotonic() - began < 2


def test_this_machine_still_answers():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        with socket.create_connection(listener.getsockname(), timeout=2) as client:
            assert client.getpeername()[0] == "127.0.0.1"


def test_the_review_prompt_follows_refusals_and_runs_a_failing_check_on_the_base():
    template = (activities.PROMPTS_DIR / "review-v6.txt").read_text(encoding="utf-8")

    assert "Something lost to a request that was then refused is a finding" in template
    assert "check the two cannot disagree" in template
    assert "Run it on the base, in a worktree of origin/$base" in template
    assert "If it passes there, the change broke it: that finding blocks" in template
