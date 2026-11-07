"""The console web app: server-rendered pages over the REST API.

The console owns no store. It reads and changes goals through the planner's
REST API and tasks through the Task API, with a service key, naming the
signed-in operator as the actor on every change.

Sign-in: in the cloud, Identity-Aware Proxy signs operators in with GitHub,
and the console will read the identity from IAP's signed header; that check
comes with the cloud deploy. Locally, with no IAP, MH_CONSOLE_DEV_IDENTITY
names the operator. The identity must be on the access list,
MH_CONSOLE_OPERATORS, or every page answers 403.
"""

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from meta_harness.planner.tracker import parse_ref
from meta_harness.tasks.client import TaskApiError

HERE = Path(__file__).parent


def operators_from_env() -> set[str]:
    return {o.strip().lower() for o in os.environ.get("MH_CONSOLE_OPERATORS", "").split(",") if o.strip()}


def issue_url(ref: str) -> str | None:
    try:
        repo, number = parse_ref(ref)
    except ValueError:
        return None
    return f"https://github.com/{repo}/issues/{number}"


def create_app(goals: Any, tasks: Any, operators: set[str] | None = None, dev_identity: str | None = None) -> FastAPI:
    allowed = operators if operators is not None else operators_from_env()
    local_identity = dev_identity if dev_identity is not None else os.environ.get("MH_CONSOLE_DEV_IDENTITY")
    templates = Jinja2Templates(directory=HERE / "templates")
    app = FastAPI(title="meta-harness console", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def identity(request: Request) -> str | None:
        # The IAP identity header is trusted only after its signed JWT is checked, which comes with the
        # cloud deploy. Until then the console reads only the local development identity.
        return local_identity.lower() if local_identity else None

    def page(request: Request, name: str, status_code: int = 200, **context: Any) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            name,
            {"who": context.pop("who", None), "error": request.query_params.get("error"), **context},
            status_code=status_code,
        )

    def signed_in(request: Request) -> str | HTMLResponse:
        who = identity(request)
        if who is None:
            return page(
                request, "forbidden.html", 401, reason="Sign in through the meta-harness's Identity-Aware Proxy."
            )
        if who not in allowed:
            return page(request, "forbidden.html", 403, who=who, reason=f"{who} is not on the console's access list.")
        return who

    def back(path: str, error: str | None = None) -> RedirectResponse:
        if error:
            from urllib.parse import quote

            path = f"{path}?error={quote(error)}"
        return RedirectResponse(path, status_code=303)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request):
        who = signed_in(request)
        if isinstance(who, HTMLResponse):
            return who
        all_goals = goals.list()
        open_goals = [g for g in all_goals if g["status"] not in ("met", "canceled")]
        closed_count = len(all_goals) - len(open_goals)
        waiting = tasks.list(["ready", "failed", "review_unresolved"], None, 100)
        needs_you = [
            {
                **t,
                "issue_url": issue_url(t["external_ref"]),
                "why": "its pull request is open for you to merge"
                if t["status"] == "ready"
                else (t["status_reason"] or t["status"]),
            }
            for t in waiting
            if t.get("goal_id")
        ]
        return page(request, "overview.html", who=who, goals=open_goals, closed_count=closed_count, needs_you=needs_you)

    @app.get("/goals/new", response_class=HTMLResponse)
    def new_goal(request: Request):
        who = signed_in(request)
        if isinstance(who, HTMLResponse):
            return who
        return page(request, "goal_new.html", who=who, repos=goals.repos(), form={})

    @app.post("/goals")
    def create_goal(
        request: Request,
        statement: str = Form(...),
        label: str = Form(...),
        repos: list[str] = Form(default=[]),
        max_usd: str = Form(default=""),
        priority: int = Form(default=0),
        auto_merge: str = Form(default=""),
    ):
        who = signed_in(request)
        if isinstance(who, HTMLResponse):
            return who
        form = {
            "statement": statement,
            "label": label,
            "repos": repos,
            "max_usd": max_usd,
            "priority": priority,
            "auto_merge": auto_merge == "on",
        }
        if not repos:
            return page(
                request, "goal_new.html", 422, who=who, repos=goals.repos(), form=form, error="Pick at least one repo."
            )
        body = {**form, "max_usd": max_usd or None}
        try:
            goal = goals.create(actor=who, **body)
        except TaskApiError as err:
            return page(request, "goal_new.html", 422, who=who, repos=goals.repos(), form=form, error=err.detail)
        return back(f"/goals/{goal['id']}")

    @app.get("/goals/{goal_id}", response_class=HTMLResponse)
    def goal_page(request: Request, goal_id: str):
        who = signed_in(request)
        if isinstance(who, HTMLResponse):
            return who
        try:
            goal = goals.get(goal_id)
        except TaskApiError as err:
            return page(request, "forbidden.html", err.status, who=who, reason=err.detail)
        issues = []
        for link in goal.pop("issues", []):
            task = None
            if link["task_id"]:
                try:
                    task = tasks.get(link["task_id"])
                except TaskApiError:
                    task = None
            issues.append(
                {
                    **link,
                    "issue_url": issue_url(link["ref"]),
                    "task_status": task["status"] if task else None,
                    "pr_url": task["pr_url"] if task else None,
                }
            )
        return page(request, "goal.html", who=who, goal=goal, issues=issues)

    def goal_action(request: Request, goal_id: str, name: str):
        who = signed_in(request)
        if isinstance(who, HTMLResponse):
            return who
        try:
            goals.action(goal_id, name, actor=who)
        except TaskApiError as err:
            return back(f"/goals/{goal_id}", err.detail)
        return back(f"/goals/{goal_id}")

    @app.post("/goals/{goal_id}/pause")
    def pause(request: Request, goal_id: str):
        return goal_action(request, goal_id, "pause")

    @app.post("/goals/{goal_id}/resume")
    def resume(request: Request, goal_id: str):
        return goal_action(request, goal_id, "resume")

    @app.post("/goals/{goal_id}/cancel")
    def cancel(request: Request, goal_id: str):
        return goal_action(request, goal_id, "cancel")

    @app.post("/goals/{goal_id}/sweep")
    def sweep(request: Request, goal_id: str):
        return goal_action(request, goal_id, "sweep")

    @app.post("/goals/{goal_id}/auto-merge")
    def auto_merge(request: Request, goal_id: str, on: str = Form(...)):
        who = signed_in(request)
        if isinstance(who, HTMLResponse):
            return who
        try:
            goals.patch(goal_id, actor=who, auto_merge=on == "on")
        except TaskApiError as err:
            return back(f"/goals/{goal_id}", err.detail)
        return back(f"/goals/{goal_id}")

    return app
