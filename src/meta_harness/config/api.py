"""The configuration part of the REST API.

    GET  /v1/config                       every kind: its title, description and active version; max_sandboxes
    GET  /v1/config/models                the models a tier candidate can name, for the tier form
    GET  /v1/config/{kind}                the active version, with its document
    GET  /v1/config/{kind}/versions       the version history, newest first
    PUT  /v1/config/{kind}                save a new version: {document, note}; 422 lists every reason it is refused
    POST /v1/config/{kind}/rollback       make an earlier version active again: {version, note}
    POST /v1/config/skills/read           read a skill from a git repository or from uploaded files, to look at
                                          before it is saved: nothing is saved here

The planner process serves it for every kind, until the run service has an
HTTP API of its own. Every save and rollback is a `config_changed` event with
the operator as actor.
"""

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from meta_harness.config import skills, store
from meta_harness.config.kinds import KINDS, ConfigError, sandbox_ceiling
from meta_harness.config.models import catalog


class SaveIn(BaseModel):
    document: dict[str, Any]
    note: str | None = Field(default=None, max_length=500)


class SkillIn(BaseModel):
    """Where to read a skill from: a git repository (url, with a branch or tag and a path inside it), or its
    files, uploaded."""

    url: str | None = Field(default=None, max_length=500)
    ref: str | None = Field(default=None, max_length=200)
    path: str | None = Field(default=None, max_length=500)
    files: dict[str, str] | None = None


class RollbackIn(BaseModel):
    version: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=500)


def router(
    pool: Callable[[], ConnectionPool],
    operator: Callable[..., str],
    events: Any,
    extra_check: Callable[[str, dict[str, Any]], list[str]] | None = None,
) -> APIRouter:
    """The configuration API. extra_check adds the owning service's own reasons to refuse a document, such as
    the planner's: a repo an open goal names cannot leave the registry."""
    api = APIRouter()

    def refuse_extra(kind: str, document: dict[str, Any]) -> None:
        reasons = extra_check(kind, document) if extra_check else []
        if reasons:
            raise HTTPException(status_code=422, detail={"reasons": reasons})

    def kind_or_404(kind: str) -> str:
        if kind not in KINDS:
            raise HTTPException(
                status_code=404, detail=f"no configuration kind {kind}; the kinds are {', '.join(KINDS)}"
            )
        return kind

    def changed(kind: str, saved: dict[str, Any], who: str) -> None:
        events.emit(
            "config_changed",
            {
                "kind": kind,
                "version": saved["version"],
                "origin": saved["origin"],
                "note": saved["note"],
                "rolled_back_from": saved["rolled_back_from"],
            },
            actor=who,
        )

    @api.get("/v1/config")
    def kinds(who: str = Depends(operator)) -> dict:
        with pool().connection() as conn:
            items = []
            for name, kind in KINDS.items():
                current = store.active(conn, name)
                items.append(
                    {
                        "kind": name,
                        "title": kind.title,
                        "description": kind.description,
                        "version": current["version"],
                        "edited_by": current["edited_by"],
                        "created_at": current["created_at"],
                    }
                )
        try:
            ceiling = sandbox_ceiling()
        except ConfigError:
            ceiling = None
        return {"kinds": items, "source": "db" if store.from_database() else "file", "max_sandboxes": ceiling}

    @api.get("/v1/config/models")
    def models(who: str = Depends(operator)) -> dict:
        """Every priced completions model a tier candidate can name, as <engine>:<model>."""
        return {"models": catalog()}

    @api.post("/v1/config/skills/read")
    def read_skill(body: SkillIn, who: str = Depends(operator)) -> dict:
        """A skill as an import would take it, for an admin to read before saving: its name, description,
        source and every file. Saving is a new version of the skills kind (PUT /v1/config/skills)."""
        try:
            if body.files is not None:
                name, skill = skills.from_files(body.files, {"kind": "upload", "by": who})
            elif body.url:
                name, skill = skills.from_git(body.url.strip(), (body.ref or "").strip() or None, body.path)
            else:
                raise skills.SkillError("give a git url, or the skill's files")
        except skills.SkillError as err:
            raise HTTPException(status_code=422, detail=str(err)) from err
        return {"name": name, "skill": skill}

    @api.get("/v1/config/{kind}")
    def read(kind: str, who: str = Depends(operator)) -> dict:
        with pool().connection() as conn:
            return store.active(conn, kind_or_404(kind))

    @api.get("/v1/config/{kind}/versions")
    def history(kind: str, who: str = Depends(operator)) -> dict:
        with pool().connection() as conn:
            store.active(conn, kind_or_404(kind))
            return {"versions": store.versions(conn, kind)}

    @api.put("/v1/config/{kind}")
    def save(kind: str, body: SaveIn, who: str = Depends(operator)) -> dict:
        refuse_extra(kind_or_404(kind), body.document)
        try:
            with pool().connection() as conn:
                saved = store.save(conn, kind_or_404(kind), body.document, who, body.note)
        except ConfigError as err:
            raise HTTPException(status_code=422, detail={"reasons": err.reasons}) from err
        changed(kind, saved, who)
        return saved

    @api.post("/v1/config/{kind}/rollback")
    def rollback(kind: str, body: RollbackIn, who: str = Depends(operator)) -> dict:
        try:
            with pool().connection() as conn:
                saved = store.rollback(conn, kind_or_404(kind), body.version, who, body.note)
        except LookupError as err:
            raise HTTPException(status_code=404, detail=str(err)) from err
        except ConfigError as err:
            raise HTTPException(status_code=422, detail={"reasons": err.reasons}) from err
        changed(kind, saved, who)
        return saved

    return api
