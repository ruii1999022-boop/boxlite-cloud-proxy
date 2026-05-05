"""URL-rewriting proxy for the boxlite Python SDK against BoxLite Cloud.

The Python SDK at sdks/python (v0.9.x) is built for the local embedded
runtime (`boxlite serve`), where boxes have short 12-char base62 ids.
The hosted cloud at https://dev.boxlite.ai/api uses UUID `box_id`s. When
you point Boxlite.rest(url=...) at the cloud, the create call succeeds
server-side, but the returned Box object's `.id` is a freshly fabricated
short id that doesn't correspond to any cloud-side identifier — every
subsequent box-scoped call (start/exec/remove/get_info) builds a URL
with that bogus id and 404s.

This proxy sits between the SDK and the cloud:

  1. Forwards every request to dev.boxlite.ai.
  2. On POST /api/v1/<org>/boxes (create), captures the real `box_id`
     UUID from the response.
  3. On any subsequent request whose path segment after /boxes/ is not
     a UUID, rewrites it to the captured UUID before forwarding.

The SDK never knows. Point Boxlite.rest(url="http://127.0.0.1:8765/api")
and the canonical cloud example runs end-to-end (Exit code: 0).

Caveats
-------
- Single-box-at-a-time. Every fake id is rewritten to the *most recent*
  captured UUID. If you create two boxes back-to-back and operate on
  both, calls intended for box A get redirected to box B. Extending to
  a real (fake-id -> uuid) map keyed off the create response is left as
  an exercise; not needed for typical sequential usage.
- Localhost-only. Don't expose port 8765.
- Workaround, not a fix. Track upstream for an SDK fix.

Usage
-----
    export BOXLITE_CLIENT_SECRET=dtn_...   # token from your dashboard
    python3 proxy.py                       # listens on 127.0.0.1:8765

Then in client code:
    Boxlite.rest(BoxliteRestOptions(
        url="http://127.0.0.1:8765/api",
        client_id="default",
        client_secret=os.environ["BOXLITE_CLIENT_SECRET"],
    ))
"""
import json
import os
import re
import sys

from aiohttp import web, ClientSession, ClientTimeout

TARGET_BASE = os.environ.get("BOXLITE_TARGET_BASE", "https://dev.boxlite.ai")
SECRET = os.environ["BOXLITE_CLIENT_SECRET"]
LISTEN_HOST = os.environ.get("BOXLITE_PROXY_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("BOXLITE_PROXY_PORT", "8765"))

LATEST_UUID: str | None = os.environ.get("BOXLITE_KNOWN_UUID")

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
BOX_PATH_RE = re.compile(r"^(/api/v1/[^/]+/boxes/)([^/?]+)(/.*)?$")


def log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def maybe_rewrite(path: str) -> tuple[str, str | None]:
    """If `path` contains a non-UUID box id and we have a captured UUID,
    return the rewritten path and a human-readable swap label.
    Otherwise return the path unchanged."""
    m = BOX_PATH_RE.match(path)
    if not m:
        return path, None
    bid = m.group(2)
    rest = m.group(3) or ""
    if UUID_RE.match(bid) or not LATEST_UUID:
        return path, None
    return f"{m.group(1)}{LATEST_UUID}{rest}", f"{bid} -> {LATEST_UUID}"


async def handler(request: web.Request) -> web.Response:
    global LATEST_UUID
    body = await request.read()
    new_path, swap = maybe_rewrite(request.path)

    if swap:
        log(f"  REWRITE  {request.method} {request.path}  ({swap})")
    else:
        log(f"  PASS     {request.method} {request.path}")

    qs = ("?" + request.query_string) if request.query_string else ""
    target = f"{TARGET_BASE}{new_path}{qs}"

    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ("host", "content-length", "transfer-encoding", "connection")}
    headers["Authorization"] = f"Bearer {SECRET}"

    timeout = ClientTimeout(total=300)
    async with ClientSession(timeout=timeout) as s:
        async with s.request(request.method, target, headers=headers,
                             data=body if body else None,
                             allow_redirects=False) as r:
            resp_body = await r.read()
            log(f"           -> {r.status} ({len(resp_body)} bytes)")

            # Capture box_id UUID from create responses so we can rewrite
            # subsequent requests for this box.
            if (request.method == "POST"
                    and re.match(r"^/api/v1/[^/]+/boxes$", request.path)
                    and 200 <= r.status < 300):
                try:
                    data = json.loads(resp_body)
                    bid = data.get("box_id")
                    if bid:
                        LATEST_UUID = bid
                        log(f"           captured LATEST_UUID = {LATEST_UUID}")
                except Exception as e:
                    log(f"           (failed to parse create resp: {e})")

            resp_headers = {k: v for k, v in r.headers.items()
                            if k.lower() not in ("content-encoding",
                                                  "content-length",
                                                  "transfer-encoding",
                                                  "connection")}
            return web.Response(status=r.status, body=resp_body, headers=resp_headers)


def main() -> None:
    app = web.Application()
    app.add_routes([web.route("*", "/{path:.*}", handler)])
    log(f"boxlite-cloud-proxy listening on http://{LISTEN_HOST}:{LISTEN_PORT}")
    log(f"  forwarding to {TARGET_BASE}")
    log(f"  initial LATEST_UUID = {LATEST_UUID}")
    web.run_app(app, host=LISTEN_HOST, port=LISTEN_PORT, print=None)


if __name__ == "__main__":
    main()
