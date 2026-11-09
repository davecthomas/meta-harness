"""A registered event type's schema never changes: the event service refuses to start when one does.

The deployment's event service keeps every (name, version) schema it has
registered, and stops at startup when the code's schema for one differs
("changed its schema; give it a new version"). CI starts from an empty
database, so only this snapshot catches it before a deploy.
"""

import json
from pathlib import Path

from meta_harness.events import types

SNAPSHOT = Path(__file__).with_name("registered_schemas.json")


def _registry() -> dict[str, dict]:
    return {f"{t.name} v{t.schema_version}": t.json_schema for t in types.REGISTRY}


def test_no_registered_schema_changes():
    shipped = json.loads(SNAPSHOT.read_text())
    changed = [key for key, schema in shipped.items() if key in _registry() and _registry()[key] != schema]
    assert not changed, (
        f"{changed}: a deployed event type's schema changed, and the event service would refuse to start. "
        "Keep the schema, or register a new version beside it"
    )
    removed = [key for key in shipped if key not in _registry()]
    assert not removed, f"{removed}: a deployed event type was removed; remove it in a later pull request"


def test_every_new_event_type_is_in_the_snapshot():
    missing = [key for key in _registry() if key not in json.loads(SNAPSHOT.read_text())]
    assert not missing, f"add {missing} to {SNAPSHOT.name}, so a later change to it is caught"
