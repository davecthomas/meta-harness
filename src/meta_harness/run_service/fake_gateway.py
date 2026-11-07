"""A fake HTTP Gateway for the zero-cost end-to-end check. It makes no model calls.

    python -m meta_harness.run_service.fake_gateway [--host 127.0.0.1] [--port 8099]

It answers the calls the sample makes to ai-api-unified-http 1.19.0, with
the same paths, status codes and JSON fields:

  GET    /healthz                    service_version "fake"
  POST   /v1/run-grants              issue a run grant, or a new key for a live one
  GET    /v1/run-grants/{run_id}     the grant, without its key
  DELETE /v1/run-grants/{run_id}     revoke it and report what it spent
  POST   /v1/conversations/turn      a scripted model turn, on a grant key

The scripted run: the first turn asks pi to write hello.txt with
EXPECTED_TEXT, and any turn after a tool result answers that it is done.
Tool results count only in the HTTP Gateway's neutral shape
({"type": "tool_result", "tool_call_id", "result", "is_error"}), and a
malformed one gets 400, so the check fails if pi sends an engine's own shape.
Each turn takes FAKE_TURN_SECONDS (default 2) and costs TURN_USD, so the
cancel and restart demos have time to act and the spend-limit demo trips
on turn 2. Grants live in memory. Run grants work as on the real gateway:
the worker authenticates with GATEWAY_API_KEY, pi with the grant key, and a
run that has spent its limit gets 402 run_grant_spend_limit.

Environment: GATEWAY_API_KEY (default local-dev-key), FAKE_TURN_SECONDS.
"""

import argparse
import json
import os
import re
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

EXPECTED_FILE = "hello.txt"
EXPECTED_TEXT = "Hello from the fake HTTP Gateway."
TURN_USD = Decimal("0.002")
TURN_SECONDS = float(os.environ.get("FAKE_TURN_SECONDS", "2"))
API_KEY = os.environ.get("GATEWAY_API_KEY", "local-dev-key")
GRANT_PATH = re.compile(r"^/v1/run-grants/([^/]+)$")


def log(text: str) -> None:
    print(f"[fake-gateway] {text}", flush=True)


class Grants:
    """Run grants in memory, keyed by run id, with each grant key pointing at its run."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.grants: dict[str, dict[str, Any]] = {}
        self.keys: dict[str, str] = {}

    def issue(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        run_id = str(body.get("run_id", ""))
        if not run_id:
            return 422, {"detail": "run_id is required"}
        terms = (str(body.get("engine")), str(body.get("model")), str(Decimal(str(body.get("spend_limit_usd")))))
        key = "rg_" + secrets.token_urlsafe(32)
        with self.lock:
            grant = self.grants.get(run_id)
            created = grant is None
            if grant and (grant["engine"], grant["model"], grant["spend_limit_usd"]) != terms:
                return 409, {"detail": f"run {run_id} already has a grant with other terms"}
            if grant is None:
                expires = datetime.now(UTC) + timedelta(seconds=int(body.get("ttl_seconds", 900)))
                grant = {
                    "run_id": run_id,
                    "engine": terms[0],
                    "model": terms[1],
                    "spend_limit_usd": terms[2],
                    "spent_usd": "0",
                    "expires_at": expires.isoformat(timespec="seconds"),
                    "turns": 0,
                }
                self.grants[run_id] = grant
            self.keys[key] = run_id
            return 200, {**self.public(grant), "grant_key": key, "created": created}

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self.lock:
            grant = self.grants.get(run_id)
            return self.public(grant) if grant else None

    def revoke(self, run_id: str) -> dict[str, Any]:
        with self.lock:
            grant = self.grants.pop(run_id, None)
            for key in [k for k, v in self.keys.items() if v == run_id]:
                del self.keys[key]
        return {"run_id": run_id, "revoked": grant is not None, "spent_usd": grant["spent_usd"] if grant else "0"}

    def by_key(self, key: str) -> dict[str, Any] | None:
        with self.lock:
            run_id = self.keys.get(key)
            return self.grants.get(run_id) if run_id else None

    def charge(self, grant: dict[str, Any]) -> None:
        with self.lock:
            grant["spent_usd"] = str(Decimal(grant["spent_usd"]) + TURN_USD)

    @staticmethod
    def public(grant: dict[str, Any]) -> dict[str, Any]:
        return {k: grant[k] for k in ("run_id", "engine", "model", "spend_limit_usd", "spent_usd", "expires_at")}


GRANTS = Grants()


def tool_result_problem(messages: list[Any]) -> str | None:
    """What is wrong with the first malformed neutral tool result, as the real gateway checks it."""
    for index, message in enumerate(messages):
        if not (isinstance(message, dict) and message.get("type") == "tool_result" and "role" not in message):
            continue
        unknown = set(message) - {"type", "tool_call_id", "result", "is_error"}
        if unknown:
            return f"messages[{index}] (a tool_result) has unknown fields {sorted(unknown)}"
        if not isinstance(message.get("tool_call_id"), str) or not message["tool_call_id"]:
            return f"messages[{index}] (a tool_result) needs tool_call_id"
        if not isinstance(message.get("result"), dict):
            return f"messages[{index}] (a tool_result) needs result as a JSON object"
        if not isinstance(message.get("is_error", False), bool):
            return f"messages[{index}] (a tool_result) needs is_error as true or false"
    return None


def has_tool_result(messages: list[Any]) -> bool:
    """True once pi has sent back a tool result in the HTTP Gateway's neutral shape."""
    return any(isinstance(m, dict) and m.get("type") == "tool_result" and "role" not in m for m in messages)


def scripted_turn(grant: dict[str, Any], messages: list[Any]) -> dict[str, Any]:
    grant["turns"] += 1
    usage = {
        "input_tokens": 1000,
        "output_tokens": 50,
        "cached_input_tokens": 0,
        "cache_write_5m_tokens": 0,
        "cache_write_1h_tokens": 0,
        "total_tokens": 1050,
    }
    base = {
        "usage": usage,
        "usd_cost": str(TURN_USD),
        "cache_hit": False,
        "conversation_token": f"fake-{grant['run_id']}-{grant['turns']}",
        "engine": grant["engine"],
        "model": grant["model"],
        "served_by_fallback": False,
    }
    if has_tool_result(messages):
        return {**base, "text": f"Done. I wrote {EXPECTED_FILE}.", "tool_calls": [], "finish_reason": "complete"}
    call = {"id": f"call_{grant['turns']}", "name": "write", "input": {"path": EXPECTED_FILE, "content": EXPECTED_TEXT}}
    return {**base, "text": None, "tool_calls": [call], "finish_reason": "tool_use"}


class Handler(BaseHTTPRequestHandler):
    server_version = "fake-gateway"

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _bearer(self) -> str:
        header = self.headers.get("authorization", "")
        return header[7:] if header.startswith("Bearer ") else ""

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length") or 0)
        try:
            parsed = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def do_GET(self) -> None:
        if self.path == "/healthz":
            return self._send(
                200, {"status": "ok", "service_version": "fake", "api_version": "v1", "library_version": "none"}
            )
        match = GRANT_PATH.match(self.path)
        if match:
            if self._bearer() != API_KEY:
                return self._send(401, {"detail": "unknown API key"})
            grant = GRANTS.get(match.group(1))
            return self._send(200, grant) if grant else self._send(404, {"detail": "no grant for that run"})
        self._send(404, {"detail": f"no route GET {self.path}"})

    def do_DELETE(self) -> None:
        match = GRANT_PATH.match(self.path)
        if not match:
            return self._send(404, {"detail": f"no route DELETE {self.path}"})
        if self._bearer() != API_KEY:
            return self._send(401, {"detail": "unknown API key"})
        revoked = GRANTS.revoke(match.group(1))
        log(f"grant for {revoked['run_id']} revoked, spent ${revoked['spent_usd']}")
        self._send(200, revoked)

    def do_POST(self) -> None:
        if self.path == "/v1/run-grants":
            if self._bearer() != API_KEY:
                return self._send(401, {"detail": "unknown API key"})
            status, body = GRANTS.issue(self._body())
            if status == 200:
                log(f"grant for {body['run_id']} {'issued' if body['created'] else 'still live, new key added'}")
            return self._send(status, body)
        if self.path == "/v1/conversations/turn":
            return self._turn()
        self._send(404, {"detail": f"no route POST {self.path}"})

    def _turn(self) -> None:
        body = self._body()
        grant = GRANTS.by_key(self._bearer())
        if grant is None:
            return self._send(401, {"detail": "unknown or revoked grant key"})
        if Decimal(grant["spent_usd"]) >= Decimal(grant["spend_limit_usd"]):
            log(f"turn refused for {grant['run_id']}: spent ${grant['spent_usd']} of ${grant['spend_limit_usd']}")
            return self._send(
                402,
                {
                    "error": "run_grant_spend_limit",
                    "detail": f"Run '{grant['run_id']}' has spent ${grant['spent_usd']} of its "
                    f"${grant['spend_limit_usd']} limit, so no further turns are allowed on this grant.",
                },
            )
        messages = body.get("messages") or []
        problem = tool_result_problem(messages)
        if problem:
            log(f"turn refused for {grant['run_id']}: {problem}")
            return self._send(400, {"detail": problem})
        time.sleep(TURN_SECONDS)
        result = scripted_turn(grant, messages)
        GRANTS.charge(grant)
        result["run_grant"] = {**{k: grant[k] for k in ("run_id", "spent_usd", "spend_limit_usd", "expires_at")}}
        calls = ",".join(c["name"] for c in result["tool_calls"]) or "none"
        log(f"turn {grant['turns']} for {grant['run_id']}: finish={result['finish_reason']} tool_calls={calls}")
        try:
            self._send(200, result)
        except (BrokenPipeError, ConnectionResetError):
            log(f"turn {grant['turns']} for {grant['run_id']}: the caller went away")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    log(f"listening on http://{args.host}:{args.port}, {TURN_SECONDS:g}s and ${TURN_USD} per turn, no model calls")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
