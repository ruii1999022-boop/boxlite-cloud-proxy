"""End-to-end demo using direct REST (no SDK, no proxy).

Prereqs:
  export BOXLITE_URL='https://dev.boxlite.ai/api'
  export BOXLITE_CLIENT_SECRET='dtn_...'      # from your dashboard
  export BOXLITE_SNAPSHOT='<your-snapshot>'   # from your dashboard

Then:
  python3 via_rest.py
"""
import os
import sys
import uuid

# allow running from the examples directory or repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rest_client import create_box, exec_command, wait_for_output, remove_box


def main() -> None:
    snapshot = os.environ["BOXLITE_SNAPSHOT"]
    name = f"demo-{uuid.uuid4().hex[:8]}"

    print(f"creating box name={name} snapshot={snapshot}")
    info = create_box(snapshot, name)
    box_id = info["box_id"]
    print(f"  box_id = {box_id}")
    print(f"  status = {info['status']}")

    print("running echo...")
    eid = exec_command(box_id, "echo", ["Hello, BoxLite Cloud!"])
    print(f"  execution_id = {eid}")

    print("waiting for output...")
    result = wait_for_output(box_id, eid)
    print(f"  exit_code = {result.get('exit_code')}")
    print(f"  stdout    = {result.get('stdout')!r}")
    print(f"  stderr    = {result.get('stderr')!r}")

    print("cleaning up...")
    remove_box(name, force=True)
    print("done.")


if __name__ == "__main__":
    main()
