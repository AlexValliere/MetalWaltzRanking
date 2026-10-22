"""Public JSON output: complete scans replace snapshots atomically.

A failed refresh changes only the index attempt metadata. Previously validated
per-server snapshots remain byte-for-byte intact, including their capture time.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

SNAPSHOT_SCHEMA = "maiden-waltz.public-ranking-snapshot.v1"
INDEX_SCHEMA = "metal-waltz.rankings-index.v1"
SCOPE = "All server-exposed ranking entries; not an atomic snapshot or server population count."
SNAPSHOT_KEYS = frozenset(("schema", "serverKey", "aliases", "serverNames", "fund", "startedAtUtc",
                           "completedAtUtc", "rankingEnumerationComplete", "namesComplete", "scope", "rows"))
ROW_KEYS = frozenset(("rank", "name", "grandeur", "nameStatus"))


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep temporary files beside their destination so replace is atomic.
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_snapshot(snapshot, server):
    if (set(snapshot) != SNAPSHOT_KEYS or snapshot["schema"] != SNAPSHOT_SCHEMA
            or snapshot["serverKey"] != server["key"] or snapshot["aliases"] != server["aliases"]
            or snapshot["serverNames"] != server["serverNames"] or snapshot["fund"] != 5
            or snapshot["rankingEnumerationComplete"] is not True
            or type(snapshot["namesComplete"]) is not bool or snapshot["scope"] != SCOPE):
        raise ValueError("Invalid public snapshot metadata")
    timestamps = []
    for field in ("startedAtUtc", "completedAtUtc"):
        timestamp = datetime.fromisoformat(snapshot[field].replace("Z", "+00:00"))
        if timestamp.utcoffset() is None or timestamp.utcoffset().total_seconds() != 0:
            raise ValueError("Invalid public snapshot time")
        timestamps.append(timestamp)
    if timestamps[1] < timestamps[0]:
        raise ValueError("Invalid public snapshot time order")
    rows = snapshot["rows"]
    if not isinstance(rows, list) or len(rows) > 1_000_000:
        raise ValueError("Invalid public snapshot rows")
    resolved, previous = 0, None
    for rank, row in enumerate(rows, 1):
        if (set(row) != ROW_KEYS or type(row["rank"]) is not int or row["rank"] != rank
                or type(row["grandeur"]) is not int or not -2**31 <= row["grandeur"] < 2**31
                or previous is not None and row["grandeur"] > previous):
            raise ValueError("Invalid public ranking order")
        previous = row["grandeur"]
        if row["nameStatus"] == "resolved":
            name = row["name"]
            if not isinstance(name, str) or not name or "\0" in name or len(name.encode("utf-8")) > 48:
                raise ValueError("Invalid public commander name")
            resolved += 1
        elif row["nameStatus"] != "unavailable" or row["name"] is not None:
            raise ValueError("Invalid public name status")
    if snapshot["namesComplete"] != (resolved == len(rows)):
        raise ValueError("Invalid public name completeness")
    return resolved


def snapshot_entry(directory, server, refresh_status="seed"):
    filename = server["key"] + ".rankings.json"
    path = Path(directory) / filename
    entry = {"key": server["key"], "name": " / ".join(server["serverNames"]), "aliases": server["aliases"],
             "file": None, "rows": 0, "namesResolved": 0, "namesMissing": 0,
             "startedAtUtc": None, "completedAtUtc": None, "refreshStatus": refresh_status, "sha256": None}
    try:
        if path.stat().st_size > 256 * 1024 * 1024:
            raise ValueError("Snapshot size limit")
        contents = path.read_bytes()
        snapshot = json.loads(contents)
        resolved = validate_snapshot(snapshot, server)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        entry["refreshStatus"] = "failed"
        entry["errorCode"] = "snapshot_unavailable"
        return entry
    entry.update(file=filename, rows=len(snapshot["rows"]), namesResolved=resolved,
                 namesMissing=len(snapshot["rows"]) - resolved, startedAtUtc=snapshot["startedAtUtc"],
                 completedAtUtc=snapshot["completedAtUtc"], sha256=hashlib.sha256(contents).hexdigest())
    return entry


def previous_attempts(directory):
    try:
        index = json.loads((Path(directory) / "index.json").read_text(encoding="utf-8"))
        if index["schema"] != INDEX_SCHEMA:
            return {}
        result = {}
        for item in index["servers"]:
            metadata = {}
            if item.get("refreshStatus") in ("ok", "failed", "seed"):
                metadata["refreshStatus"] = item["refreshStatus"]
            if isinstance(item.get("errorCode"), str) and re.fullmatch(r"[a-z0-9_]{1,80}", item["errorCode"]):
                metadata["errorCode"] = item["errorCode"]
            if isinstance(item.get("lastAttemptAtUtc"), str):
                value = datetime.fromisoformat(item["lastAttemptAtUtc"].replace("Z", "+00:00"))
                if value.utcoffset() is not None and value.utcoffset().total_seconds() == 0:
                    metadata["lastAttemptAtUtc"] = item["lastAttemptAtUtc"]
            result[item["key"]] = metadata
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        return {}
