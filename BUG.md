# Bug Repro: `Boxlite.rest()` against BoxLite Cloud

This document gives other developers everything they need to reproduce
the underlying bug that motivates this repo's proxy. If you can spare
~10 minutes and a BoxLite Cloud account, please run through it and
report whether you see the same behavior.

## TL;DR

When the boxlite Python SDK is pointed at the hosted cloud
(`https://dev.boxlite.ai/api`), `await rt.create(...)` succeeds — the box
**does** appear in the dashboard with a real UUID — but the returned
`Box` object's `.id` is a freshly-fabricated 12-char string that doesn't
match anything server-side. Every box-scoped follow-up call (`box.start`,
`box.exec`, `rt.remove(box.id)`, `rt.get_info(box.id)`, `rt.list_info()`)
embeds the bogus id in its URL and 404s.

## Confirmed environments

Reproduced on:

| Component | Version |
|---|---|
| `boxlite` | 0.9.1 (PyPI), 0.9.2 (GitHub release wheel) |
| Python | 3.14.4 |
| OS | Ubuntu 25.10 (`manylinux_2_28_x86_64` wheel) running under WSL2 |
| Host | Windows Server 2025 Azure Edition |
| Cloud | `https://dev.boxlite.ai/api` |

If you can confirm or deny on macOS arm64, native Linux, or other
Python versions (3.10–3.13), please add a comment in this repo's
issues.

## What you need before reproducing

The cloud requires three things, all done once via
https://boxlite.ai/dashboard:

1. **An API token** — dashboard → API Keys → create. Starts with `dtn_`.
2. **A registered snapshot** — dashboard → Snapshots → "Create New
   Snapshot". The image must include an explicit tag (e.g.
   `ubuntu:22.04`); the tag `latest` is rejected.
3. **A default region on the org** — otherwise `create` returns:
   ```
   HTTP 428: This organization does not have a default region.
   ```

You only need to do this once per account.

## Steps to reproduce

```bash
# 1. Set up Python 3.10+ on Linux x86_64 (or macOS arm64; Windows needs WSL2).
python3 -m venv .venv && source .venv/bin/activate
pip install boxlite

# 2. Set credentials and the snapshot alias from your dashboard.
export BOXLITE_CLIENT_SECRET='dtn_...'
export BOXLITE_SNAPSHOT='<your-snapshot-name>'

# 3. Save and run this script (`repro.py`):
```

```python
# repro.py
import asyncio, os
from boxlite import Boxlite, BoxliteRestOptions, BoxOptions

async def main():
    rt = Boxlite.rest(BoxliteRestOptions(
        url="https://dev.boxlite.ai/api",
        client_id="default",
        client_secret=os.environ["BOXLITE_CLIENT_SECRET"],
    ))

    box = await rt.create(
        BoxOptions(image=os.environ["BOXLITE_SNAPSHOT"]),
        name="repro-1",
    )
    print("box.id =", box.id)
    print("box.name =", box.name)

    # The dashboard now shows 'repro-1' running with a real UUID.
    # But the next call fails:
    await box.start()         # <-- 404

    # Never reached:
    execution = await box.exec("echo", args=["hello"])
    result = await execution.wait()
    print("exit_code:", result.exit_code)

    await rt.remove(box.id, force=True)

asyncio.run(main())
```

```bash
python3 repro.py
```

### Expected

```
box.id = <real-server-uuid>
box.name = repro-1
exit_code: 0
```

### Actual

```
box.id = JV915zsnhKTo                             # 12-char base62, not a UUID
box.name = repro-1
Traceback (most recent call last):
  ...
RuntimeError: box not found:
  {"path":"/api/v1/default/boxes/JV915zsnhKTo/start",
   "statusCode":404, "error":"Not Found",
   "message":"Sandbox with ID or name JV915zsnhKTo not found"}
```

The 12-char value of `box.id` will differ on each run, but it will
never be a UUID and will never match what the dashboard or a direct
REST call to the cloud reports.

### Cleanup after the failed run

`repro-1` is now stuck on the cloud (the script never reached `remove`).
Drop it by name:

```bash
curl -X DELETE \
  -H "Authorization: Bearer $BOXLITE_CLIENT_SECRET" \
  "https://dev.boxlite.ai/api/v1/default/boxes/repro-1?force=true"
# expect 204
```

## Smoking gun #1 — `box.id` is generated locally

Run `rt.list_info()` twice in a row against the same set of boxes. The
`id` values come back **different on each call** for the same boxes:

```python
async def main():
    rt = Boxlite.rest(BoxliteRestOptions(
        url="https://dev.boxlite.ai/api",
        client_id="default",
        client_secret=os.environ["BOXLITE_CLIENT_SECRET"],
    ))
    import json
    a = await rt.list_info()
    b = await rt.list_info()
    for x in a:
        print("call 1:", json.loads(str(x))["id"], json.loads(str(x))["name"])
    for x in b:
        print("call 2:", json.loads(str(x))["id"], json.loads(str(x))["name"])

asyncio.run(main())
```

Sample output (note the same `name` paired with different `id`s):

```
call 1: qyVk8I1FkQu4 my-sandbox
call 2: kgSbQkfWmheZ my-sandbox
```

A direct REST call returns a stable UUID for the same box:

```bash
curl -s -H "Authorization: Bearer $BOXLITE_CLIENT_SECRET" \
  https://dev.boxlite.ai/api/v1/default/boxes
# {"boxes":[{"box_id":"ade8b888-b245-4f38-a942-885b29508eec",
#            "name":"my-sandbox", ...}]}
```

So `box_id` (the field the cloud actually returns) is being discarded
on the SDK side and replaced.

## Smoking gun #2 — different bogus ids per method

If you route the SDK through this repo's `proxy.py` you can log every
request. The same `Box` object yields **different** fake ids in
different methods:

```
captured server UUID = a7584460-0800-48aa-a314-45624d491a0a
SDK box.id reported  = w65m3cn9evap

REWRITE  POST /api/v1/default/boxes/w65m3cn9evap/start
                                    ^^^^^^^^^^^^   (matches box.id)
REWRITE  POST /api/v1/default/boxes/0fzdliGrzDFl/exec
                                    ^^^^^^^^^^^^   (different! same Box)
REWRITE  GET  /api/v1/default/boxes/0fzdliGrzDFl/executions/<eid>/output
REWRITE  DELETE /api/v1/default/boxes/w65m3cn9evap         (back to start id)
```

So the fabrication isn't a single field stored once — the SDK regenerates
ids in different code paths.

## Smoking gun #3 — no Python-side patch works

Tried. None of these recover a usable `Box`:

| Approach | Result |
|---|---|
| `box.id = "<real-uuid>"` | `AttributeError` (read-only at pyo3 layer) |
| `Box(id="<real-uuid>")` | `TypeError: cannot create 'builtins.Box' instances` |
| `await rt.get("<real-uuid>")` | Returns `Box(id="<different-bogus-id>")` |
| `await rt.get("<box-name>")` | Returns `Box(id="<different-bogus-id>")` |
| `await rt.get_info("<real-uuid>")` | Returns dict with a fresh bogus `id` |
| `await rt.import_box(...)` | Loads a `.boxlite` archive from disk; not for hosted boxes |
| `await rt.get_or_create(...)` | Returns a tuple, then errors |

Conclusion: the bug can't be patched in user code. The fix has to be in
the SDK itself, or you avoid the SDK against the cloud.

## Workaround

Two options, both in this repo:

1. **`proxy.py`** — keeps the SDK; rewrites every URL the SDK builds so
   the bogus id segment becomes the real captured UUID before forwarding.
   The canonical SDK example then runs end-to-end. See
   [`examples/via_proxy.py`](./examples/via_proxy.py).
2. **`rest_client.py`** — skip the SDK entirely. ~70 lines of `urllib`
   covering create / list / exec / wait-for-output / delete. See
   [`examples/via_rest.py`](./examples/via_rest.py).

## Why this is a category mismatch, not just a bug

The boxlite-ai/boxlite repo distinguishes two things that look the same
on the surface but use different id conventions:

- **`sdks/python/`** — the embeddable SDK with `Boxlite.rest()`. Built
  for the local `boxlite serve` runtime, where boxes have 12-char
  base62 ids.
- **`apps/api/`** — the hosted cloud REST server, with its OpenAPI spec
  at `openapi/rest-sandbox-open-api.yaml`. Uses UUID `box_id`s. A Go
  client generated from that spec is at `apps/api-client-go/`.

`Boxlite.rest()` accepts any URL and happens to send the right HTTP
shape that the cloud also speaks — except for the id format. So the
constructor doesn't error, the first call works, and you only find out
when `box.start()` 404s. There is no Python equivalent of
`apps/api-client-go/` shipped today.

A small upstream change (warn or error in `BoxliteRestOptions` when
`url` looks hosted) would have made this trap obvious.

## What I'd appreciate from testers

- ✔/✘ **Same behavior on macOS arm64?** (The non-Linux supported
  platform.)
- ✔/✘ **Same on `boxlite==0.9.1` and `0.9.2`?**
- ✔/✘ **Same on Python 3.10, 3.11, 3.12, 3.13?**
- ✔/✘ **Does `proxy.py` fix it for you end-to-end?**
- Any other failure modes (auth, region, snapshot) you hit before
  reaching `box.start()`.

Open an issue on this repo with your environment + output, or comment
on the upstream issue if/when one is filed.

## Related

- Upstream repo: https://github.com/boxlite-ai/boxlite
- OpenAPI spec for the cloud:
  https://github.com/boxlite-ai/boxlite/blob/main/openapi/rest-sandbox-open-api.yaml
- Working Go cloud client:
  https://github.com/boxlite-ai/boxlite/tree/main/apps/api-client-go
