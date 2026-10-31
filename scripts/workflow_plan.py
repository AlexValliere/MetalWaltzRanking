"""Select canonical backend jobs without accessing game credentials or the network."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from collector.configuration import EXPECTED_BACKENDS, PAUSED_BACKENDS, load_servers

AFFECTED_MOBILE = ("Bonn", "KOREA", "BANGKOK")
MANUAL_SCOPES = {
    "affected-mobile": AFFECTED_MOBILE,
    "bonn": ("Bonn",),
    "europe-thailand": ("Bonn", "BANGKOK"),
}


def collection_plan(event: str, attempt: int, fetch: bool, scope: str = "all") -> dict:
    if type(attempt) is not int or attempt < 1 or type(fetch) is not bool or scope not in ("all", *MANUAL_SCOPES):
        raise ValueError("Invalid workflow collection selection")
    keys = [server["key"] for server in load_servers()]
    if len(keys) != len(EXPECTED_BACKENDS) or set(keys) != EXPECTED_BACKENDS:
        raise ValueError("Invalid workflow backend directory")
    available = [key for key in keys if key not in PAUSED_BACKENDS]
    selected = list(MANUAL_SCOPES[scope]) if event == "workflow_dispatch" and scope in MANUAL_SCOPES else keys
    selected = [key for key in selected if key not in PAUSED_BACKENDS]
    fresh = attempt == 1 and (event == "schedule" or event == "workflow_dispatch" and fetch)
    # Paused-only scopes skip collection; GitHub still requires a nonempty matrix.
    return {"collect": fresh and bool(selected),
            "matrix": {"server": selected or available or ["collection-paused"]}}


def main() -> int:
    try:
        fetch = os.environ.get("WORKFLOW_FETCH", "false")
        if fetch not in ("true", "false"):
            raise ValueError("Invalid workflow fetch flag")
        plan = collection_plan(os.environ["WORKFLOW_EVENT"], int(os.environ["WORKFLOW_ATTEMPT"]),
                               fetch == "true", os.environ.get("WORKFLOW_SCOPE", "all"))
    except (KeyError, ValueError):
        print("Invalid workflow collection selection.", file=sys.stderr)
        return 2
    scope = os.environ.get("WORKFLOW_SCOPE", "all")
    if PAUSED_BACKENDS == EXPECTED_BACKENDS:
        print("All fresh collection is temporarily paused; no game login will run. "
              "Validated last-good snapshots remain available for publication.", file=sys.stderr)
    elif os.environ["WORKFLOW_EVENT"] == "workflow_dispatch" and scope in MANUAL_SCOPES:
        if set(MANUAL_SCOPES[scope]) <= PAUSED_BACKENDS:
            print("Requested collection scope is temporarily paused; no fresh collection will run. "
                  "Validated last-good snapshots remain available for publication.", file=sys.stderr)
        elif set(MANUAL_SCOPES[scope]) & PAUSED_BACKENDS:
            print("Temporarily paused mobile Europe1/Bonn and Thailand/BANGKOK are excluded "
                  "from the requested collection scope.", file=sys.stderr)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write("collect=" + str(plan["collect"]).lower() + "\n")
            stream.write("matrix=" + json.dumps(plan["matrix"], separators=(",", ":")) + "\n")
    else:
        print(json.dumps(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
