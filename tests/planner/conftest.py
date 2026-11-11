import pytest


@pytest.fixture(autouse=True)
def no_gateway(monkeypatch):
    """No planner test reaches a real HTTP Gateway: a service built without a sizer sizes no issue, as its
    sizing call fails and the issue runs unsized. A test that wants the gateway sets it to a mock."""
    monkeypatch.delenv("GATEWAY_URL", raising=False)
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)
