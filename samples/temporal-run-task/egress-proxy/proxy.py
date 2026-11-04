"""The local egress proxy for per-run sandboxes (technical design section 15, Local development).

Every per-run sandbox container sits on its own internal Docker network, which
has no route out. This proxy is attached to each of those networks and to one
ordinary network, so it is the only way in or out. It has two listeners:

  egress   0.0.0.0:3128, for the per-run sandboxes. A request goes through only
           when its host and port match EGRESS_ALLOW: the HTTP Gateway, the code
           host and package registries. Anything else gets 403.
  control  the proxy's address on its ordinary network, port 8091, published on
           the developer's 127.0.0.1. The run activity reaches each harness
           server through it. Only hosts and ports matching CONTROL_ALLOW (the
           per-run sandbox containers' harness server port) go through. A per-run
           sandbox cannot reach this listener: its network has no route to the
           ordinary network.

Both take HTTP CONNECT tunnels and plain HTTP requests in absolute form. Each
egress decision, and each refused or failed control request, is logged on one
line. Python standard library only.

Environment:
  EGRESS_ALLOW   comma-separated host:port patterns, `*` as a wildcard, e.g.
                 host.docker.internal:8080,github.com:443,*.github.com:443
  CONTROL_ALLOW  the same, for the control listener, e.g. mh-sandbox-*:8090
"""

import asyncio
import fnmatch
import os
import re
import socket
import sys
from urllib.parse import urlsplit

EGRESS_PORT = 3128
CONTROL_PORT = 8091
HEAD_LIMIT = 64 * 1024
HEAD_TIMEOUT_SECONDS = 30
CONNECT_TIMEOUT_SECONDS = 10
HOST_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")
HOP_HEADERS = {"connection", "keep-alive", "proxy-connection", "proxy-authorization", "te", "trailer", "upgrade"}


def log(text: str) -> None:
    print(f"[egress-proxy] {text}", flush=True)


def log_allow(listener: str, text: str) -> None:
    """Every egress decision is logged. The worker's control traffic is logged only when refused or failed."""
    if listener == "egress":
        log(f"{listener} allow {text}")


def patterns(name: str) -> list[str]:
    return [item.strip().lower() for item in os.environ.get(name, "").split(",") if item.strip()]


def allowed(host: str, port: int, rules: list[str]) -> bool:
    """True when host:port matches a rule. Hosts must be plain DNS names, so no rule can be tricked by odd characters."""
    if not HOST_PATTERN.match(host):
        return False
    target = f"{host}:{port}"
    return any(fnmatch.fnmatchcase(target, rule) for rule in rules)


def split_host_port(value: str, default_port: int) -> tuple[str, int]:
    host, _, port = value.rpartition(":") if ":" in value else (value, "", "")
    if not host:
        host, port = value, ""
    return host.lower().strip("[]"), int(port) if port.isdigit() else default_port


async def refuse(writer: asyncio.StreamWriter, status: str, text: str) -> None:
    body = (text + "\n").encode()
    writer.write(
        f"HTTP/1.1 {status}\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )
    try:
        await writer.drain()
    finally:
        writer.close()


async def pipe(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
    try:
        while data := await source.read(65536):
            sink.write(data)
            await sink.drain()
        if sink.can_write_eof():
            sink.write_eof()
    except (ConnectionError, OSError):
        pass


async def relay(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_reader: asyncio.StreamReader,
    upstream_writer: asyncio.StreamWriter,
) -> None:
    """Copies both ways until the upstream side closes, then closes both connections."""
    to_upstream = asyncio.create_task(pipe(client_reader, upstream_writer))
    try:
        await pipe(upstream_reader, client_writer)
    finally:
        to_upstream.cancel()
        for writer in (client_writer, upstream_writer):
            writer.close()


async def handle(listener: str, rules: list[str], reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    client = peer[0] if peer else "?"
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT_SECONDS)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError, ConnectionError):
        writer.close()
        return
    lines = head.decode("latin-1").split("\r\n")
    try:
        method, target, version = lines[0].split(" ", 2)
    except ValueError:
        await refuse(writer, "400 Bad Request", "malformed request line")
        return

    if method == "CONNECT":
        host, port = split_host_port(target, 443)
        if not allowed(host, port, rules):
            log(f"{listener} deny  CONNECT {host}:{port} from {client}: not on the allowlist")
            await refuse(writer, "403 Forbidden", f"{host}:{port} is not on the per-run sandbox {listener} allowlist")
            return
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), CONNECT_TIMEOUT_SECONDS
            )
        except (OSError, TimeoutError) as err:
            log(f"{listener} fail  CONNECT {host}:{port} from {client}: {err}")
            await refuse(writer, "502 Bad Gateway", f"could not reach {host}:{port}")
            return
        log_allow(listener, f"CONNECT {host}:{port} from {client}")
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await relay(reader, writer, upstream_reader, upstream_writer)
        return

    url = urlsplit(target)
    if url.scheme != "http" or not url.hostname:
        await refuse(writer, "400 Bad Request", "send CONNECT, or an absolute http:// URL")
        return
    host, port = url.hostname.lower(), url.port or 80
    if not allowed(host, port, rules):
        log(f"{listener} deny  {method} http://{host}:{port} from {client}: not on the allowlist")
        await refuse(writer, "403 Forbidden", f"{host}:{port} is not on the per-run sandbox {listener} allowlist")
        return
    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), CONNECT_TIMEOUT_SECONDS
        )
    except (OSError, TimeoutError) as err:
        log(f"{listener} fail  {method} http://{host}:{port} from {client}: {err}")
        await refuse(writer, "502 Bad Gateway", f"could not reach {host}:{port}")
        return
    path = url.path or "/"
    if url.query:
        path += "?" + url.query
    headers = [line for line in lines[1:] if line and line.split(":", 1)[0].strip().lower() not in HOP_HEADERS]
    # One request per connection keeps the relay simple: the upstream closes after its response.
    upstream_writer.write(f"{method} {path} {version}\r\n".encode("latin-1"))
    upstream_writer.write("".join(f"{line}\r\n" for line in headers).encode("latin-1"))
    upstream_writer.write(b"Connection: close\r\n\r\n")
    await upstream_writer.drain()
    log_allow(listener, f"{method} http://{host}:{port}{url.path} from {client}")
    await relay(reader, writer, upstream_reader, upstream_writer)


async def main() -> None:
    egress_rules = patterns("EGRESS_ALLOW")
    control_rules = patterns("CONTROL_ALLOW")
    # At start the container is on its ordinary network only, so its hostname resolves to that address.
    control_host = os.environ.get("CONTROL_BIND") or socket.gethostbyname(socket.gethostname())

    async def serve(listener: str, rules: list[str], host: str, port: int) -> asyncio.Server:
        return await asyncio.start_server(
            lambda r, w: handle(listener, rules, r, w), host, port, limit=HEAD_LIMIT
        )

    egress = await serve("egress", egress_rules, "0.0.0.0", EGRESS_PORT)
    control = await serve("control", control_rules, control_host, CONTROL_PORT)
    log(f"egress listener on 0.0.0.0:{EGRESS_PORT}, allowing {', '.join(egress_rules) or 'nothing'}")
    log(f"control listener on {control_host}:{CONTROL_PORT}, allowing {', '.join(control_rules) or 'nothing'}")
    async with egress, control:
        await asyncio.gather(egress.serve_forever(), control.serve_forever())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
