import os

import pytest

from meta_harness.planner import sizing


@pytest.fixture(autouse=True)
def no_gateway(monkeypatch):
    """No planner test reaches a real HTTP Gateway. A service built without a sizer sizes no issue: with no
    gateway set, its sizer answers as sizing turned off does, and the issue runs unsized. (A sizing call that
    fails would leave the issue waiting for the next sweep.) A test that wants the gateway sets it to a mock."""
    monkeypatch.delenv("GATEWAY_URL", raising=False)
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)
    real = sizing.size_issue

    async def size_issue(issue, transport=None):
        if transport is None and not os.environ.get("GATEWAY_URL"):
            return None
        return await real(issue, transport)

    monkeypatch.setattr(sizing, "size_issue", size_issue)
