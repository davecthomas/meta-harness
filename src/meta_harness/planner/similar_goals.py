"""The open goals a new goal's description is close to, so an operator does not start the same work twice.

New goal sends the description as it is written. It is embedded and compared, by cosine similarity, with each
open goal's description; the closest few at or above the cut-off are shown, each with what the operator can do
about it. Nothing here blocks a goal from being saved. Every goal shown and every answer is recorded
(`goal_similarity_judged`), so the cut-off can be set from what operators said (docs/specs/similar-goals.md).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from meta_harness import db
from meta_harness.config import kinds
from meta_harness.planner import embeddings, planning

log = logging.getLogger("meta_harness.planner")

# The goals a new one is compared with: every goal that is not over.
OPEN = ("planning", "active", "paused", "stalled", "budget_exhausted")
# A description shorter than this says too little to compare.
MIN_CHARS = 40
MODEL_PATTERN = re.compile(r"^[a-z0-9-]+:[A-Za-z0-9._-]+$")
DEFAULT_CUTOFF = 0.70
DEFAULT_MOST = 3
MOST_ALLOWED = 5
ANSWERS = ("shown", "opened", "not_duplicate", "same_work", "different")
EVENT = "goal_similarity_judged"
# How many of the newest answers the Performance page's table reads.
SHOWN_LIMIT = 200


def check(document: dict[str, Any]) -> list[str]:
    """Problems with the planning configuration's `similar_goals`."""
    config = document.get("similar_goals", {})
    if not isinstance(config, dict):
        return ["similar_goals must be an object: the settings of the similar goals New goal shows"]
    reasons = []
    if not isinstance(config.get("enabled", True), bool):
        reasons.append("similar_goals.enabled must be true or false: whether New goal looks for similar goals")
    model = config.get("model", "")
    if model and (not isinstance(model, str) or not MODEL_PATTERN.match(model)):
        reasons.append("similar_goals.model must be <engine>:<model>, an embedding model the HTTP Gateway serves")
    cutoff = config.get("cutoff", DEFAULT_CUTOFF)
    if isinstance(cutoff, bool) or not isinstance(cutoff, int | float) or not 0 <= cutoff <= 1:
        reasons.append("similar_goals.cutoff must be a number from 0 to 1: how alike a goal must be to be shown")
    most = config.get("most", DEFAULT_MOST)
    if isinstance(most, bool) or not isinstance(most, int) or not 1 <= most <= MOST_ALLOWED:
        reasons.append(f"similar_goals.most must be a whole number from 1 to {MOST_ALLOWED}: how many goals are shown")
    return reasons


def settings() -> dict[str, Any]:
    """The planning configuration's `similar_goals`, over the deployment's own (config/planner.json): a saved
    planning configuration from before this setting has none, and uses the deployment's."""
    seed = kinds.seed_planning().get("similar_goals") or {}
    config = {"enabled": True, "cutoff": DEFAULT_CUTOFF, "most": DEFAULT_MOST, **seed}
    config.update(planning.settings().get("similar_goals") or {})
    return config


def check_settings() -> None:
    """Raises RuntimeError unless an embedding model is named: another model costs differently, so none is
    assumed."""
    config = settings()
    problems = check({"similar_goals": config})
    if not config.get("model"):
        problems.append("similar_goals.model must name an embedding model, as <engine>:<model>")
    if problems:
        raise RuntimeError(f"the planning configuration's similar_goals is not usable: {'; '.join(problems)}")


def cosine(a: list[float], b: list[float]) -> float:
    """How alike two vectors point, from -1 to 1; 0 when either has no length or they differ in size."""
    if len(a) != len(b):
        return 0.0
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / norm if norm else 0.0


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _text(goal: dict) -> str:
    """What a goal is compared by: its title and its description."""
    return f"{goal.get('title') or ''}\n{goal.get('statement') or ''}".strip()


def open_goals() -> list[dict]:
    """Every open goal, with its stored embedding when it has one."""
    with db.connect(autocommit=True) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT g.id, g.title, g.statement, g.status, g.created_at, g.updated_at, e.model, e.vector, e.text_hash "
            "FROM planner.goal g LEFT JOIN planner.goal_embedding e ON e.goal_id = g.id "
            "WHERE g.status = ANY(%s) ORDER BY g.created_at",
            (list(OPEN),),
        )
        return cur.fetchall()


def _keep(goal_id: str, model: str, vector: list[float], text_hash: str) -> None:
    with db.connect(autocommit=True) as conn:
        conn.execute(
            "INSERT INTO planner.goal_embedding (goal_id, model, vector, text_hash) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (goal_id) DO UPDATE SET model = EXCLUDED.model, vector = EXCLUDED.vector, "
            "text_hash = EXCLUDED.text_hash, made_at = now()",
            (goal_id, model, vector, text_hash),
        )


async def similar(
    description: str, transport: httpx.AsyncBaseTransport | None = None, besides: str = ""
) -> dict[str, Any]:
    """The open goals closest to the description: {goals, cutoff, model, checked}. goals: the closest first,
    each at or above the cut-off, `most` at most. checked false: the description is too short, or the check is
    off, and nothing was compared. besides: a goal left out, the one being edited.

    Raises:
        embeddings.EmbeddingError: The texts could not be embedded.
    """
    config = settings()
    model = str(config.get("model") or "")
    none = {"goals": [], "cutoff": float(config["cutoff"]), "model": model, "checked": False}
    text = description.strip()
    if not config.get("enabled", True) or len(text) < MIN_CHARS or not model:
        return none
    goals = [g for g in await asyncio.to_thread(open_goals) if g["id"] != besides]
    if not goals:
        return {**none, "checked": True}
    engine, _, name = model.partition(":")
    # A goal's vector is made the first time it is compared, and again when its description or the model changed.
    stale = [g for g in goals if g["vector"] is None or g["model"] != model or g["text_hash"] != _hash(_text(g))]
    made = await embeddings.embed([text, *(_text(g) for g in stale)], engine, name, transport)
    mine = made[0]
    for goal, vector in zip(stale, made[1:], strict=True):
        goal["vector"] = vector
        await asyncio.to_thread(_keep, goal["id"], model, vector, _hash(_text(goal)))
    scored = sorted(((cosine(mine, list(g["vector"])), g) for g in goals), key=lambda found: found[0], reverse=True)
    close = [(score, g) for score, g in scored if score >= float(config["cutoff"])][: int(config["most"])]
    return {
        **none,
        "checked": True,
        "goals": [
            {
                "goal_id": g["id"],
                "title": g["title"] or (g["statement"] or "")[:80],
                "status": g["status"],
                "since": (g["updated_at"] or g["created_at"]).isoformat(),
                "score": round(score, 4),
            }
            for score, g in close
        ],
    }


class SimilarIn(BaseModel):
    description: str = Field(max_length=20000)
    # The goal being edited, left out of what it is compared with.
    besides: str = ""


class AnswerIn(BaseModel):
    goal_id: str = Field(min_length=1, max_length=64)
    score: float = Field(ge=-1, le=1)
    answer: str
    # The goal the operator saved, once there is one.
    new_goal_id: str = ""


def register(
    app: FastAPI, operator: Callable[..., str], events: Any, transport: httpx.AsyncBaseTransport | None = None
) -> None:
    @app.post("/v1/goals/similar")
    async def find_similar(body: SimilarIn, who: str = Depends(operator)) -> dict:
        """The open goals close to this description, the closest first. Each one shown is recorded. 502 says
        why the check could not run; the goal can still be saved."""
        try:
            found = await similar(body.description, transport, body.besides)
        except embeddings.EmbeddingError as err:
            raise HTTPException(status_code=502, detail=str(err)) from err
        for goal in found["goals"]:
            _record(events, who, goal["goal_id"], goal["score"], "shown", found)
        return found

    @app.post("/v1/goals/similar/answer", status_code=201)
    def answer(body: AnswerIn, who: str = Depends(operator)) -> dict:
        """What the operator said of a goal they were shown."""
        if body.answer not in ANSWERS or body.answer == "shown":
            allowed = ", ".join(a for a in ANSWERS if a != "shown")
            raise HTTPException(status_code=422, detail=f"answer must be one of {allowed}")
        config = settings()
        now = {"cutoff": float(config["cutoff"]), "model": str(config.get("model") or "")}
        _record(events, who, body.goal_id, body.score, body.answer, now, body.new_goal_id)
        return {"recorded": body.answer}

    @app.get("/v1/goals/similar/shown")
    def shown(who: str = Depends(operator)) -> dict:
        """Each goal New goal showed as similar, newest first, with its score and the last thing said of it:
        what the cut-off is set from."""
        page = events.read(0, SHOWN_LIMIT, before_position=2**62, event_type=EVENT)["events"]
        titles = {g["id"]: g["title"] or (g["statement"] or "")[:80] for g in _titles()}
        rows = [
            {
                "goal_id": (e.get("payload") or {}).get("goal_id", ""),
                "title": titles.get((e.get("payload") or {}).get("goal_id", ""), ""),
                "score": (e.get("payload") or {}).get("score"),
                "answer": (e.get("payload") or {}).get("answer", ""),
                "new_goal_id": (e.get("payload") or {}).get("new_goal_id", ""),
                "by": e.get("actor") or "",
                "at": e.get("created_at") or e.get("recorded_at") or "",
            }
            for e in sorted(page, key=lambda e: e["position"], reverse=True)
        ]
        config = settings()
        return {"shown": rows, "cutoff": float(config["cutoff"]), "model": str(config.get("model") or "")}


def _titles() -> list[dict]:
    with db.connect(autocommit=True) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id, title, statement FROM planner.goal")
        return cur.fetchall()


def _record(events: Any, who: str, goal_id: str, score: float, answer: str, now: dict, new_goal_id: str = "") -> None:
    """One `goal_similarity_judged` event. The new goal's description is never in it."""
    payload = {
        "goal_id": goal_id,
        "score": float(score),
        "answer": answer,
        "cutoff": float(now["cutoff"]),
        "model": str(now["model"]),
    }
    if new_goal_id:
        payload["new_goal_id"] = new_goal_id
    try:
        events.emit(EVENT, payload, actor=who, tags={"goal_id": goal_id})
    except Exception as err:  # noqa: BLE001 - a record that cannot be kept does not stop the operator's work
        log.error("could not record %s for goal %s: %s", EVENT, goal_id, json.dumps(str(err)))
