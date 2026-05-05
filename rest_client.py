"""Minimal direct-REST client for BoxLite Cloud (no SDK, no proxy).

The boxlite Python SDK is built for the local embedded runtime and doesn't
work correctly against the hosted cloud (see proxy.py for context). If you
don't need the SDK's box class hierarchy, talking to the cloud REST API
directly is simpler:

    import os
    from rest_client import create_box, exec_command, wait_for_output, remove_box

    info = create_box(os.environ["BOXLITE_SNAPSHOT"], "my-job")
    eid = exec_command(info["box_id"], "echo", ["hello cloud"])
    result = wait_for_output(info["box_id"], eid)
    print(result["exit_code"], result.get("stdout"))
    remove_box(info["box_id"], force=True)

Reads URL and credentials from environment:
    BOXLITE_URL              e.g. https://dev.boxlite.ai/api
    BOXLITE_CLIENT_SECRET    the dtn_... token from the dashboard

Uses only Python stdlib (urllib).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


URL = os.environ["BOXLITE_URL"].rstrip("/")
SECRET = os.environ["BOXLITE_CLIENT_SECRET"]


def _call(method: str, path: str, body=None, timeout: float = 120.0) -> tuple[int, str]:
    headers = {"Authorization": f"Bearer {SECRET}"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(f"{URL}{path}", headers=headers, method=method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def list_boxes(org: str = "default") -> list[dict]:
    code, text = _call("GET", f"/v1/{org}/boxes")
    if code != 200:
        raise RuntimeError(f"list_boxes: {code} {text}")
    return json.loads(text).get("boxes", [])


def create_box(image: str, name: str, org: str = "default", **extra) -> dict:
    """Create a box from a snapshot. `image` is the snapshot alias from the
    dashboard, not a Docker tag. Cloud auto-starts the box."""
    body = {"image": image, "name": name, **extra}
    code, text = _call("POST", f"/v1/{org}/boxes", body, timeout=180)
    if code not in (200, 201):
        raise RuntimeError(f"create_box: {code} {text}")
    return json.loads(text)


def exec_command(box_id: str, command: str, args: list[str] | None = None,
                 org: str = "default") -> str:
    body = {"command": command, "args": args or []}
    code, text = _call("POST", f"/v1/{org}/boxes/{box_id}/exec", body)
    if code not in (200, 201):
        raise RuntimeError(f"exec_command: {code} {text}")
    return json.loads(text)["execution_id"]


def wait_for_output(box_id: str, execution_id: str, max_wait: float = 120.0,
                    poll: float = 1.0, org: str = "default") -> dict:
    """Poll until the execution finishes. Returns the raw cloud response,
    typically {"exit_code": int, "stdout": str, "stderr": str}."""
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        code, text = _call("GET",
            f"/v1/{org}/boxes/{box_id}/executions/{execution_id}/output")
        if code == 200:
            return json.loads(text)
        if code != 404:
            raise RuntimeError(f"wait_for_output: {code} {text}")
        time.sleep(poll)
    raise TimeoutError(f"no output after {max_wait}s")


def remove_box(name_or_id: str, force: bool = True, org: str = "default") -> None:
    suffix = f"?force={'true' if force else 'false'}"
    code, text = _call("DELETE", f"/v1/{org}/boxes/{name_or_id}{suffix}")
    if code not in (200, 204):
        raise RuntimeError(f"remove_box: {code} {text}")


def cleanup_all(org: str = "default") -> int:
    """Remove every box on the account. Returns the number removed."""
    n = 0
    for b in list_boxes(org=org):
        try:
            remove_box(b["name"], force=True, org=org)
            n += 1
        except Exception as e:
            print(f"  failed to remove {b['name']}: {e}")
    return n
