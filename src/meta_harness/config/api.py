"""The configuration part of the REST API.

    GET  /v1/config                       every kind: its title, description and active version
    GET  /v1/config/{kind}                the active version, with its document
    GET  /v1/config/{kind}/versions       the version history, newest first
    PUT  /v1/config/{kind}                save a new version: {document, note}; 422 lists every reason it is refused
    POST /v1/config/{kind}/rollback       make an earlier version active again: {version, note}

The planner process serves it for every kind, until the run service has an
HTTP API of its own. Every save and rollback is a `config_changed` event with
the operator as actor.
"""

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from meta_harness.config import store
from meta_harness.config.kinds import KINDS, ConfigError


class SaveIn(BaseModel):
    document: dict[str, Any]
    note: str | None = Field(default=None, max_length=500)


class RollbackIn(BaseModel):
    version: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=500)


def router(pool: Callable[[], ConnectionPool], operator: Callable[..., str], events: Any) -> APIRouter:
    api = APIRouter()

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
        return {"kinds": items, "source": "db" if store.from_database() else "file"}

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
