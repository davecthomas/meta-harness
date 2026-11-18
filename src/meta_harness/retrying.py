"""Retries for every HTTP call the meta-harness makes, as an httpx transport.

A transient failure (no connection, a timeout, a dropped reply, 429, 5xx) is tried again with exponential
backoff and jitter before anything reports it. Each client gets this by being built with `transport()` or
`async_transport()`, so no call site carries its own loop, and `tests/test_retrying.py` fails a client built
without one.

What may be sent again depends on whether the service can have acted on the first try:

- A request that never reached the service (no connection, a connect or pool timeout) is always repeated, and
  so is one the service turned away unread: 429, 503, or a 403 that says when to come back (GitHub's rate limits).
- A request that may have been acted on (a read timeout, a dropped reply, 500, 502, 504) is repeated only when
  repeating it is harmless: GET, HEAD, OPTIONS, PUT and DELETE, and a POST the client marks with `safe`, such as
  a search.

A wait the service asks for (Retry-After, x-ratelimit-reset) is honored when it fits the deadline. Each retry is
logged as a warning, a call that recovers at info, and one that gives up as an error, so a recurring failure
shows in the logs even when no caller reports it. A query string is never logged: it can hold a value a caller
would not want there.
"""

import asyncio
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import httpx

log = logging.getLogger("meta_harness.retrying")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
# Never reached the service, so sending it again cannot do anything twice.
NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
TURNED_AWAY = frozenset({429, 503})
MAYBE_ACTED_ON = frozenset({408, 425, 500, 502, 504})

Safe = Callable[[httpx.Request], bool]


@dataclass(frozen=True)
class Policy:
    """How hard to try. deadline_seconds bounds the whole call: an operator's page is waiting on many of them,
    and Firebase Hosting ends a request at 60 seconds."""

    attempts: int = 5
    base_seconds: float = 0.5
    cap_seconds: float = 8.0
    deadline_seconds: float = 45.0


DEFAULT = Policy()
# For a call made while someone waits on something else, with a fallback when it fails.
QUICK = Policy(attempts=3, base_seconds=0.2, cap_seconds=1.0, deadline_seconds=4.0)


def _never(request: httpx.Request) -> bool:
    return False


def always(request: httpx.Request) -> bool:
    """For a client whose POSTs only read, or carry their own id so a repeat changes nothing."""
    return True


def asked_wait(response: httpx.Response) -> float | None:
    """How long the service asked to be left alone, in seconds; None when it did not say."""
    after = response.headers.get("retry-after")
    if after:
        try:
            return max(0.0, float(after))
        except ValueError:
            try:
                return max(0.0, parsedate_to_datetime(after).timestamp() - time.time())
            except (TypeError, ValueError):
                return None
    if response.headers.get("x-ratelimit-remaining") == "0" and (reset := response.headers.get("x-ratelimit-reset")):
        try:
            return max(0.0, float(reset) - time.time())
        except ValueError:
            return None
    return None


def backoff(policy: Policy, attempt: int) -> float:
    """The wait after a failed try: doubling up to the cap, jittered so callers that failed together spread out."""
    ceiling = min(policy.cap_seconds, policy.base_seconds * 2 ** (attempt - 1))
    return random.uniform(ceiling / 2, ceiling)


class _Tries:
    """One call's retry decisions, shared by the sync and async transports."""

    def __init__(self, request: httpx.Request, policy: Policy, safe: Safe) -> None:
        self.request = request
        self.policy = policy
        self.repeatable = request.method in SAFE_METHODS or safe(request)
        self.started = time.monotonic()
        self.attempt = 0
        self.where = f"{request.method} {request.url.host}{request.url.path}"

    def _wait(self, why: str, asked: float | None) -> float | None:
        """The wait before the next try, logged; None, also logged, when this call has run out of tries."""
        left = self.policy.deadline_seconds - (time.monotonic() - self.started)
        wait = asked if asked is not None else backoff(self.policy, self.attempt)
        if self.attempt >= self.policy.attempts or wait >= left:
            log.error("%s failed after %d tries in %.1fs: %s", self.where, self.attempt, self.elapsed(), why)
            return None
        log.warning(
            "%s: %s; try %d of %d failed, trying again in %.1fs",
            self.where,
            why,
            self.attempt,
            self.policy.attempts,
            wait,
        )
        return wait

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def after_error(self, err: httpx.TransportError) -> float | None:
        why = f"{type(err).__name__}: {err}" if str(err) else type(err).__name__
        if not (isinstance(err, NOT_SENT) or self.repeatable):
            log.error("%s failed and is not safe to repeat: %s", self.where, why)
            return None
        return self._wait(why, None)

    def after_response(self, response: httpx.Response) -> tuple[bool, float | None]:
        """Whether the response is a transient failure, and the wait before trying again if there is one."""
        status = response.status_code
        told_when = status == 403 and asked_wait(response) is not None
        if status in TURNED_AWAY or told_when or (status in MAYBE_ACTED_ON and self.repeatable):
            return True, self._wait(f"answered {status}", asked_wait(response))
        return False, None

    def finished(self) -> None:
        if self.attempt > 1:
            log.info("%s succeeded on try %d after %.1fs", self.where, self.attempt, self.elapsed())


# Named so tests can take the waiting out.
_sleep = time.sleep
_async_sleep = asyncio.sleep


class RetryTransport(httpx.BaseTransport):
    def __init__(self, inner: httpx.BaseTransport, policy: Policy, safe: Safe) -> None:
        self.inner, self.policy, self.safe = inner, policy, safe

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        request.read()
        tries = _Tries(request, self.policy, self.safe)
        while True:
            tries.attempt += 1
            try:
                response = self.inner.handle_request(request)
            except httpx.TransportError as err:
                wait = tries.after_error(err)
                if wait is None:
                    raise
            else:
                transient, wait = tries.after_response(response)
                if not transient:
                    tries.finished()
                    return response
                if wait is None:
                    return response
                response.close()
            _sleep(wait)

    def close(self) -> None:
        self.inner.close()


class AsyncRetryTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport, policy: Policy, safe: Safe) -> None:
        self.inner, self.policy, self.safe = inner, policy, safe

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        tries = _Tries(request, self.policy, self.safe)
        while True:
            tries.attempt += 1
            try:
                response = await self.inner.handle_async_request(request)
            except httpx.TransportError as err:
                wait = tries.after_error(err)
                if wait is None:
                    raise
            else:
                transient, wait = tries.after_response(response)
                if not transient:
                    tries.finished()
                    return response
                if wait is None:
                    return response
                await response.aclose()
            await _async_sleep(wait)

    async def aclose(self) -> None:
        await self.inner.aclose()


def transport(
    inner: httpx.BaseTransport | None = None, policy: Policy = DEFAULT, safe: Safe = _never
) -> RetryTransport:
    """A retrying transport for an `httpx.Client`, over the given one (a test's) or the network."""
    return RetryTransport(inner or httpx.HTTPTransport(), policy, safe)


def async_transport(
    inner: httpx.AsyncBaseTransport | None = None, policy: Policy = DEFAULT, safe: Safe = _never
) -> AsyncRetryTransport:
    """A retrying transport for an `httpx.AsyncClient`, over the given one (a test's) or the network."""
    return AsyncRetryTransport(inner or httpx.AsyncHTTPTransport(), policy, safe)
