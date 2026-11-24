"""A setting with a safe default that is set badly falls back to it and says so (fallback.py, VL-14520)."""

import pytest

from meta_harness import fallback
from meta_harness.planner import actions, needs


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(fallback, "DEFAULTED", {})


def test_a_good_or_missing_setting_is_used_and_a_bad_one_falls_back_named_with_its_fix(monkeypatch, caplog):
    monkeypatch.delenv("MH_TEST_SECONDS", raising=False)
    assert fallback.seconds("MH_TEST_SECONDS", 60.0) == 60.0
    monkeypatch.setenv("MH_TEST_SECONDS", "0")
    assert fallback.seconds("MH_TEST_SECONDS", 60.0) == 0.0
    assert fallback.DEFAULTED == {}

    for bad in ("often", "-5", "nan", "inf"):
        monkeypatch.setenv("MH_TEST_SECONDS", bad)
        assert fallback.seconds("MH_TEST_SECONDS", 60.0) == 60.0, bad
    [found] = fallback.checked("planner")["defaulted"]
    assert found == {
        "setting": "MH_TEST_SECONDS",
        "value": "inf",
        "default": "60.0",
        "fix": "Set MH_TEST_SECONDS to a number of seconds, 0 or more, or remove it to keep 60.0, then restart the service",
    }
    assert "MH_TEST_SECONDS is 'inf'" in caplog.text


def test_each_services_newest_report_decides_what_needs_the_operator():
    class Events:
        def read(self, from_position=0, limit=500, before_position=None, **filters):
            assert filters == {"event_type": "settings_checked"}
            bad = {"setting": "MH_ADMISSION_SECONDS", "value": "x", "default": "3.0", "fix": "Set it"}
            return {
                "events": [
                    {"position": 1, "payload": {"service": "planner", "defaulted": [{**bad, "setting": "MH_A"}]}},
                    {"position": 2, "payload": {"service": "scheduler", "defaulted": [bad]}},
                    # The planner restarted with the setting fixed: its notice is gone.
                    {"position": 3, "payload": {"service": "planner", "defaulted": []}},
                ]
            }

    found = actions.for_settings(needs.settings_reports(Events()))

    assert [(n["action"], n["asks"], n["title"]) for n in found] == [
        ("fix_setting", "Fix a setting", "MH_ADMISSION_SECONDS")
    ]
    assert found[0]["what"] == "The scheduler runs on 3.0 for MH_ADMISSION_SECONDS, since it is set to 'x'. Set it."
