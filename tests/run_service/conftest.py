"""What every run service test starts from."""

import pytest

from meta_harness.run_service import code_host

# The code host's own read, kept for the test of it: the fixture below replaces it everywhere else.
_PENDING_SUITES = code_host.pending_suites


@pytest.fixture
def pending_suites():
    return _PENDING_SUITES


@pytest.fixture(autouse=True)
def no_queued_workflows(monkeypatch):
    """No workflow is queued on any commit, unless a test says one is: the checks wait reads the head commit's
    check suites from the code host, and no test calls GitHub."""

    async def none(repo: str, sha: str) -> int:
        return 0

    monkeypatch.setattr(code_host, "pending_suites", none)
