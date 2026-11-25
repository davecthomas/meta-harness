"""Names the operator an activity acts as before it runs (meta_harness.acting).

Every activity that reaches GitHub takes a request naming its task or its goal. The worker reads that goal's
operator here, once per activity, so no activity or code host call has to pass it along. A request naming
neither, and a goal with no operator, use the GitHub App.
"""

from __future__ import annotations

import asyncio
from typing import Any

from temporalio.worker import ActivityInboundInterceptor, ExecuteActivityInput, Interceptor

from meta_harness import acting


class _Inbound(ActivityInboundInterceptor):
    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        request = input.args[0] if input.args else None
        who = await asyncio.to_thread(acting.of_request, request)
        with acting.as_operator(who):
            return await super().execute_activity(input)


class ActingInterceptor(Interceptor):
    """Sets the acting operator around each activity."""

    def intercept_activity(self, next: ActivityInboundInterceptor) -> ActivityInboundInterceptor:
        return _Inbound(next)
