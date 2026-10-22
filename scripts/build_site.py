"""Validate the public export and copy only the static site's public files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

SERVER_KEYS = {"Bonn", "YaleAndroid", "YaleIOS", "JP", "KOREA", "JKMIX", "BANGKOK"}
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 192 * 1024 * 1024
STATIC_EXTENSIONS = {".html", ".css", ".js", ".mjs", ".svg", ".png", ".webp", ".ico", ".woff2", ".txt"}
INDEX_FIELDS = {"schema", "generatedAtUtc", "servers"}
ENTRY_FIELDS = {
    "key", "name", "aliases", "file", "rows", "namesResolved", "namesMissing",
    "startedAtUtc", "completedAtUtc", "refreshStatus", "errorCode", "sha256", "lastAttemptAtUtc",
}
SNAPSHOT_FIELDS = {
    "schema", "serverKey", "aliases", "serverNames", "fund", "startedAtUtc", "completedAtUtc",
    "rankingEnumerationComplete", "namesComplete", "scope", "rows",
}
ROW_FIELDS = {"rank", "name", "grandeur", "nameStatus"}


class ValidationError(ValueError):
    """The proposed public export does not match the reviewed public schema."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValidationError(reason)


def fields(value: object, allowed: set[str], required: set[str], label: str) -> dict:
    require(isinstance(value, dict), f"{label}: expected object")
    require(required <= value.keys() <= allowed, f"{label}: unexpected or missing fields")
    return value


def timestamp(value: object, label: str, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    require(isinstance(value, str) and value.endswith("Z"), f"{label}: expected UTC timestamp")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValidationError(f"{label}: invalid timestamp") from exc


def strings(value: object, label: str) -> None:
    require(isinstance(value, list) and bool(value), f"{label}: expected nonempty list")
    require(all(isinstance(item, str) and item and len(item) <= 128 for item in value), f"{label}: invalid text")


def public_json(path: Path) -> tuple[dict, bytes]:
    require(path.is_file() and not path.is_symlink(), "public file missing or symbolic link")
    require(path.stat().st_size <= MAX_FILE_BYTES, "public file exceeds size limit")
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError("invalid public JSON") from exc
    require(isinstance(value, dict), "public JSON must contain an object")
    return value, raw


def validate_data(data_dir: Path) -> list[Path]:
    """Return the exact allowed data file list; never expose IDs or captures."""
    require(data_dir.is_dir() and not data_dir.is_symlink(), "data directory missing or symbolic link")
    index, raw_index = public_json(data_dir / "index.json")
    fields(index, INDEX_FIELDS, INDEX_FIELDS, "index")
    require(index["schema"] == "metal-waltz.rankings-index.v1", "unsupported index schema")
    timestamp(index["generatedAtUtc"], "index generatedAtUtc")
    entries = index["servers"]
    require(isinstance(entries, list) and len(entries) == len(SERVER_KEYS), "expected seven backends")
    seen: set[str] = set()
    paths = [data_dir / "index.json"]
    total_bytes = len(raw_index)
    for entry in entries:
        fields(entry, ENTRY_FIELDS, ENTRY_FIELDS - {"errorCode", "lastAttemptAtUtc"}, "server entry")
        key = entry["key"]
        require(isinstance(key, str) and key in SERVER_KEYS and key not in seen, "unexpected or duplicate backend")
        seen.add(key)
        require(isinstance(entry["name"], str) and bool(entry["name"]), "server name missing")
        strings(entry["aliases"], "server aliases")
        status = entry["refreshStatus"]
        require(status in {"ok", "failed", "seed"}, "invalid refresh status")
        if "errorCode" in entry:
            require(status == "failed" and isinstance(entry["errorCode"], str) and re.fullmatch(r"[a-z0-9_]{1,80}", entry["errorCode"]) is not None, "unsanitized error code")
        if "lastAttemptAtUtc" in entry:
            timestamp(entry["lastAttemptAtUtc"], "last attempt")
        for field in ("rows", "namesResolved", "namesMissing"):
            require(type(entry[field]) is int and 0 <= entry[field] <= 1_000_000, "invalid entry count")
        require(entry["rows"] == entry["namesResolved"] + entry["namesMissing"], "inconsistent name counts")
        if entry["file"] is None:
            require(status == "failed" and entry["rows"] == 0 and entry["sha256"] is None, "missing snapshot requires explicit failure")
            require(entry["startedAtUtc"] is None and entry["completedAtUtc"] is None, "missing snapshot has timestamps")
            continue
        filename = entry["file"]
        require(filename == f"{key}.rankings.json", "unexpected snapshot filename")
        timestamp(entry["startedAtUtc"], "snapshot start")
        timestamp(entry["completedAtUtc"], "snapshot completion")
        path = data_dir / filename
        snapshot, raw = public_json(path)
        total_bytes += len(raw)
        require(total_bytes <= MAX_TOTAL_BYTES, "public data exceeds total size limit")
        require(isinstance(entry["sha256"], str) and hashlib.sha256(raw).hexdigest() == entry["sha256"], "snapshot hash mismatch")
        fields(snapshot, SNAPSHOT_FIELDS, SNAPSHOT_FIELDS, "snapshot")
        require(snapshot["schema"] == "maiden-waltz.public-ranking-snapshot.v1", "unsupported snapshot schema")
        require(snapshot["serverKey"] == key and snapshot["fund"] == 5, "snapshot backend or ranking fund mismatch")
        require(snapshot["aliases"] == entry["aliases"], "snapshot aliases mismatch")
        strings(snapshot["serverNames"], "server names")
        require(snapshot["startedAtUtc"] == entry["startedAtUtc"] and snapshot["completedAtUtc"] == entry["completedAtUtc"], "snapshot timestamps mismatch")
        require(snapshot["rankingEnumerationComplete"] is True, "incomplete ranking enumeration")
        require(isinstance(snapshot["scope"], str) and 0 < len(snapshot["scope"]) <= 1000, "snapshot scope missing")
        rows = snapshot["rows"]
        require(isinstance(rows, list) and len(rows) == entry["rows"], "snapshot row count mismatch")
        resolved = 0
        previous_score: int | None = None
        for rank, row in enumerate(rows, 1):
            fields(row, ROW_FIELDS, ROW_FIELDS, "ranking row")
            require(type(row["rank"]) is int and row["rank"] == rank, "noncontiguous rank")
            score = row["grandeur"]
            require(type(score) is int and -(2**31) <= score < 2**31, "invalid Grandeur")
            require(previous_score is None or score <= previous_score, "ranking order is not descending")
            previous_score = score
            if row["nameStatus"] == "resolved":
                require(isinstance(row["name"], str) and bool(row["name"]) and len(row["name"].encode("utf-8")) <= 48, "invalid resolved name")
                resolved += 1
            else:
                require(row["nameStatus"] == "unavailable" and row["name"] is None, "invalid unavailable name")
        require(resolved == entry["namesResolved"], "resolved name count mismatch")
        require(type(snapshot["namesComplete"]) is bool and snapshot["namesComplete"] == (resolved == len(rows)), "name completeness mismatch")
        paths.append(path)
    require(seen == SERVER_KEYS, "backend list incomplete")
    require({path.name for path in data_dir.iterdir()} == {path.name for path in paths}, "unexpected files in public data directory")
    return paths


def build_site(source: Path, output: Path) -> int:
    source = source.resolve()
    output = output.resolve()
    require(source != output and source not in output.parents and output not in source.parents, "source and output must be separate")
    data_paths = validate_data(source / "data")
    require((source / "index.html").is_file(), "site index missing")
    static_paths: list[Path] = []
    for path in source.rglob("*"):
        require(not path.is_symlink(), "symbolic links are not public site assets")
        if path.is_file() and "data" not in path.relative_to(source).parts:
            require(path.suffix.lower() in STATIC_EXTENSIONS or path.name == ".nojekyll", "unexpected static site asset")
            static_paths.append(path)
    if output.exists():
        require(output.is_dir() and not output.is_symlink(), "output must be a directory")
        shutil.rmtree(output)
    output.mkdir(parents=True)
    for path in static_paths + data_paths:
        destination = output / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    (output / ".nojekyll").touch()
    return len(data_paths) - 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("site"))
    parser.add_argument("--output", type=Path, default=Path("_site"))
    parser.add_argument("--check", action="store_true", help="validate public JSON without copying")
    args = parser.parse_args()
    try:
        count = len(validate_data(args.source / "data")) - 1 if args.check else build_site(args.source, args.output)
    except (ValidationError, OSError) as exc:
        print(f"Public site validation failed: {exc}")
        return 1
    print(f"Validated {count} public ranking snapshots.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
