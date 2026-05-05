"""End-to-end demo using the boxlite Python SDK pointed at the local proxy.

Prereqs:
  pip install boxlite aiohttp
  export BOXLITE_CLIENT_SECRET=dtn_...        # from your dashboard
  export BOXLITE_SNAPSHOT=<your-snapshot>     # from your dashboard
  python3 ../proxy.py &                       # in another shell

Then:
  python3 via_proxy.py
"""
import asyncio
import os
import uuid

from boxlite import Boxlite, BoxliteRestOptions, BoxOptions


async def main() -> None:
    rt = Boxlite.rest(BoxliteRestOptions(
        url="http://127.0.0.1:8765/api",                 # the proxy
        client_id="default",
        client_secret=os.environ["BOXLITE_CLIENT_SECRET"],
    ))

    name = f"demo-{uuid.uuid4().hex[:8]}"
    snapshot = os.environ["BOXLITE_SNAPSHOT"]

    print(f"creating box name={name} snapshot={snapshot}")
    box = await rt.create(BoxOptions(image=snapshot), name=name)
    print(f"  box.name = {box.name}")
    print(f"  box.id   = {box.id}  (SDK-fabricated; proxy will swap it)")

    # Cloud auto-starts; box.start() is a no-op via the proxy.
    await box.start()

    execution = await box.exec("echo", args=["Hello, BoxLite Cloud!"])
    result = await execution.wait()
    print(f"exit_code: {result.exit_code}")

    if hasattr(result, "stdout"):
        out = result.stdout
        if isinstance(out, (bytes, bytearray)):
            out = out.decode("utf-8", "replace")
        print(f"stdout:    {out!r}")

    await rt.remove(box.id, force=True)
    print("cleaned up.")


if __name__ == "__main__":
    asyncio.run(main())
