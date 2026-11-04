"""Checks the per-run sandbox network rules, with no model call.

    python -m run_task.egress_check

`make egress-check` runs it after starting the HTTP Gateway and the egress
proxy. It starts a throwaway per-run sandbox the way the run activity does,
then checks from inside it:

1. the HTTP Gateway's /healthz, through the egress proxy: allowed
2. https://example.com, through the egress proxy: refused with 403
3. https://example.com, bypassing the proxy: no route out
4. the egress proxy's control listener: out of reach

Starting the sandbox already checks the fifth rule: the worker reached the
harness server's /health through the control listener. The sandbox is removed
at the end. Exits 0 when every check passes.
"""

import asyncio
import secrets
import sys

from run_task import sandbox


async def _exec(container: str, *command: str) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        "docker", "exec", container, *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await process.communicate()
    return process.returncode or 0, out.decode(errors="replace").strip()


async def _control_address() -> str:
    """The control listener's address on the proxy's ordinary network, which a per-run sandbox must not reach."""
    process = await asyncio.create_subprocess_exec(
        "docker", "inspect", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}",
        sandbox.EGRESS_PROXY, stdout=asyncio.subprocess.PIPE,
    )  # fmt: skip
    out, _ = await process.communicate()
    # The proxy's first network is its ordinary one; the control listener binds there.
    return out.decode().split()[0]


async def main() -> int:
    ref = f"egress-check-{secrets.token_hex(3)}"
    run_id = f"{ref}:check"
    print(f"[egress-check] starting a throwaway per-run sandbox for {run_id}", flush=True)
    try:
        box = await sandbox.ensure(run_id, ref)
    except sandbox.SandboxError as err:
        print(f"[egress-check] FAIL the per-run sandbox did not start: {err}", flush=True)
        return 1
    print(
        f"[egress-check] PASS the worker reached the harness server's /health through the control listener {box.proxy}",
        flush=True,
    )
    failures = 0

    def report(ok: bool, text: str) -> None:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"[egress-check] {'PASS' if ok else 'FAIL'} {text}", flush=True)

    try:
        gateway = sandbox.SANDBOX_GATEWAY_URL.rstrip("/") + "/healthz"
        code, out = await _exec(box.name, "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-m", "10", gateway)
        report(out == "200", f"the HTTP Gateway through the egress proxy: {gateway} answered {out or code}")

        code, out = await _exec(
            box.name, "curl", "-s", "-o", "/dev/null", "-w", "%{http_connect}", "-m", "10", "https://example.com"
        )
        report(out == "403", f"https://example.com through the egress proxy: the proxy answered {out or 'nothing'} (curl exit {code})")

        code, out = await _exec(box.name, "curl", "-s", "-o", "/dev/null", "-m", "5", "--noproxy", "*", "https://example.com")
        report(code != 0, f"https://example.com bypassing the proxy: curl exit {code}, no route out")

        control = f"http://{await _control_address()}:{sandbox.CONTROL_PORT}"
        # -x alone: curl must connect to the control listener itself. 7 is "could not connect", 28 a timeout.
        code, out = await _exec(
            box.name, "curl", "-s", "-o", "/dev/null", "-m", "5", "-x", control,
            f"http://{box.name}:{sandbox.CONTAINER_PORT}/health",
        )  # fmt: skip
        report(code in (7, 28), f"the control listener {control} from inside: curl exit {code}, out of reach")
    finally:
        await sandbox.destroy(run_id, ref)
        print(f"[egress-check] removed {box.name} and its network", flush=True)

    print(f"[egress-check] {'all checks passed' if failures == 0 else f'{failures} check(s) failed'}", flush=True)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
