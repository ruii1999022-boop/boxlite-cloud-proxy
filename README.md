# boxlite-cloud-proxy

A small URL-rewriting HTTP proxy that lets the [boxlite] Python SDK
(v0.9.x) work against the hosted **BoxLite Cloud** at
`https://dev.boxlite.ai/api`.

Also includes a ~70-line direct-REST client for users who don't need the
SDK at all.

[boxlite]: https://github.com/boxlite-ai/boxlite

## The problem

The boxlite Python SDK's `Boxlite.rest(...)` accepts any URL, so it's
natural to point it at the hosted cloud:

```python
rt = Boxlite.rest(BoxliteRestOptions(
    url="https://dev.boxlite.ai/api",
    client_id="default",
    client_secret=os.environ["BOXLITE_CLIENT_SECRET"],
))
box = await rt.create(BoxOptions(image="...", name="..."))
await box.start()   # → 404
```

The create call succeeds server-side (you can see the box appear in your
dashboard), but the returned `box.id` is a 12-char string fabricated by
the SDK — it doesn't match the cloud's UUID `box_id`. Every subsequent
call (`box.start()`, `box.exec()`, `rt.remove(box.id)`) embeds that fake
id in its URL and 404s.

This is a category mismatch: the Python SDK is built for the *local*
`boxlite serve` runtime where 12-char ids are native; the cloud uses
UUIDs. The cloud has its own client at
[`apps/api-client-go`](https://github.com/boxlite-ai/boxlite/tree/main/apps/api-client-go)
(Go, generated from the OpenAPI spec). A Python equivalent doesn't ship
yet.

## How the proxy fixes it

```
┌──────────┐   SDK builds URLs with    ┌────────────────────┐
│   your   │   FABRICATED short ids    │   proxy.py on      │   HTTPS, real UUID
│   code   │ ─────────────────────────►│   127.0.0.1:8765   │ ──────────────────► dev.boxlite.ai
│  (SDK)   │                           │   swaps id → UUID  │
└──────────┘                           └────────────────────┘
```

1. Forwards every request to `dev.boxlite.ai`.
2. On `POST /api/v1/<org>/boxes` (create), parses the response and
   captures the real `box_id` UUID.
3. On any later request whose path segment after `/boxes/` is **not** a
   UUID, rewrites it to the captured UUID before forwarding.

The SDK is unmodified and unaware. Result: `Exit code: 0`.

## Quick start

You'll need:

- A **BoxLite Cloud account** with at least one snapshot registered in
  the dashboard at https://boxlite.ai/dashboard. The snapshot's tag must
  be explicit (`ubuntu:22.04`) — `latest` is rejected.
- An **API token** from the dashboard (starts with `dtn_`).
- A **default region** set on your org in the dashboard (otherwise create
  calls return HTTP 428).

```bash
git clone https://github.com/<your-handle>/boxlite-cloud-proxy
cd boxlite-cloud-proxy
python3 -m venv .venv && source .venv/bin/activate
pip install boxlite aiohttp

export BOXLITE_CLIENT_SECRET='dtn_...'        # from dashboard
export BOXLITE_SNAPSHOT='<your-snapshot>'     # from dashboard
```

### Path A — keep the SDK, run the proxy

Two shells:

```bash
# shell 1
python3 proxy.py
# → boxlite-cloud-proxy listening on http://127.0.0.1:8765
```

```bash
# shell 2
python3 examples/via_proxy.py
# → exit_code: 0
```

In your own SDK code, just point the URL at the proxy:

```python
rt = Boxlite.rest(BoxliteRestOptions(
    url="http://127.0.0.1:8765/api",      # ← the proxy
    client_id="default",
    client_secret=os.environ["BOXLITE_CLIENT_SECRET"],
))
```

### Path B — skip the SDK, talk REST directly

`rest_client.py` is a self-contained ~70-line stdlib client. No SDK
install needed.

```bash
export BOXLITE_URL='https://dev.boxlite.ai/api'
python3 examples/via_rest.py
# → exit_code = 0  stdout = 'Hello, BoxLite Cloud!\n'
```

The endpoints `rest_client.py` uses:

```
GET    /v1/<org>/boxes
POST   /v1/<org>/boxes                                         {"image":..., "name":...}
POST   /v1/<org>/boxes/<box_uuid>/exec                         {"command":..., "args":[...]}
GET    /v1/<org>/boxes/<box_uuid>/executions/<eid>/output
DELETE /v1/<org>/boxes/<name-or-uuid>?force=true
```

Auth: `Authorization: Bearer <dtn_token>` on every request. `<org>` is
typically `default`.

## Caveats of the proxy

These all matter only if you intend to use the proxy as a long-term
solution rather than a debug aid:

- **Single-box-at-a-time.** The proxy redirects every fake id to the
  *most recent* captured UUID. If you create two boxes back-to-back and
  operate on both, calls intended for box A get redirected to box B.
  Extending this to a real `(fake-id → uuid)` map keyed off each create
  response is straightforward; not done here because typical sequential
  usage doesn't need it.
- **Localhost-only, plain HTTP.** Don't expose port 8765 outside the
  host.
- **No retries.** A single transient cloud failure bubbles up to the
  SDK as the SDK's own `RuntimeError`.
- **Workaround, not a fix.** Once a Python cloud client lands upstream,
  remove the proxy and use that.

## Configuration

The proxy reads environment variables:

| Var | Default | Notes |
|---|---|---|
| `BOXLITE_CLIENT_SECRET` | (required) | The `dtn_...` token. |
| `BOXLITE_TARGET_BASE` | `https://dev.boxlite.ai` | Upstream cloud host. |
| `BOXLITE_PROXY_HOST` | `127.0.0.1` | Listen address. Don't bind to public interfaces. |
| `BOXLITE_PROXY_PORT` | `8765` | Listen port. |
| `BOXLITE_KNOWN_UUID` | (none) | Optional starting `LATEST_UUID`, useful if you're operating on a pre-existing box. |

`rest_client.py` reads:

| Var | Notes |
|---|---|
| `BOXLITE_URL` | e.g. `https://dev.boxlite.ai/api`. |
| `BOXLITE_CLIENT_SECRET` | `dtn_...` token. |

## License

MIT — see [LICENSE](./LICENSE).

## Acknowledgements

This grew out of a debugging session against `dev.boxlite.ai`. Thanks to
the boxlite-ai team for the open-source codebase that made the bypass
possible to design.
