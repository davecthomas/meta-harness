"""The events the planner records when it sizes an issue and an operator answers one it held."""

import jsonschema
import pytest

from meta_harness.events.types import REGISTRY

SCHEMAS = {t.name: t.json_schema for t in REGISTRY}

SIZED = {
    "ref": "github:o/r#1",
    "goal_id": "g-1",
    "size": "large",
    "reasons": ["22 files"],
    "missing": ["paths"],
    "split": ["Edit titles", "Edit labels"],
    "model": "openai-decisions:gpt-6-luna",
    "usd_cost": "0.0003",
}


def test_issue_sized_keeps_the_size_the_model_and_its_cost():
    jsonschema.validate(SIZED, SCHEMAS["issue_sized"])
    for key in ("size", "model", "usd_cost"):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({k: v for k, v in SIZED.items() if k != key}, SCHEMAS["issue_sized"])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**SIZED, "size": "huge"}, SCHEMAS["issue_sized"])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**SIZED, "missing": ["vibes"]}, SCHEMAS["issue_sized"])


def test_an_operators_answer_to_a_held_issue_is_recorded():
    jsonschema.validate(
        {"ref": "github:o/r#1", "goal_id": "g-1", "held_reason": "sized large"}, SCHEMAS["issue_released"]
    )
    jsonschema.validate({"ref": "github:o/r#1", "goal_id": "g-1", "issues": ["github:o/r#2"]}, SCHEMAS["issue_split"])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"ref": "github:o/r#1", "goal_id": "g-1"}, SCHEMAS["issue_split"])
