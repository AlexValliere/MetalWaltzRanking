"""Validate the public export and copy only the static site's public files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import struct
from datetime import datetime
from pathlib import Path

LEGACY_SERVER_KEYS = frozenset({"Bonn", "YaleAndroid", "YaleIOS", "JP", "KOREA", "JKMIX", "BANGKOK"})
SERVER_KEYS = LEGACY_SERVER_KEYS | {"SteamJP", "SteamBonn", "SteamUSAVA"}
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
BASE_ROW_FIELDS = {"rank", "name", "grandeur", "nameStatus"}
ROW_FIELDS = BASE_ROW_FIELDS | {"vehiclesStatus", "vehicles"}
VEHICLE_FIELDS = {"capsuleTypeId", "phase", "level", "fate"}


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


def validate_data(data_dir: Path, *, allow_legacy_snapshot_branch: bool = False) -> list[Path]:
    """Validate the full export, or the exact old set during remote branch inspection."""
    require(data_dir.is_dir() and not data_dir.is_symlink(), "data directory missing or symbolic link")
    index, raw_index = public_json(data_dir / "index.json")
    fields(index, INDEX_FIELDS, INDEX_FIELDS, "index")
    require(index["schema"] == "metal-waltz.rankings-index.v1", "unsupported index schema")
    timestamp(index["generatedAtUtc"], "index generatedAtUtc")
    entries = index["servers"]
    require(isinstance(entries, list), "expected backend list")
    expected_keys = LEGACY_SERVER_KEYS if allow_legacy_snapshot_branch and len(entries) == len(LEGACY_SERVER_KEYS) else SERVER_KEYS
    require(len(entries) == len(expected_keys), f"expected {len(expected_keys)} backends")
    seen: set[str] = set()
    paths = [data_dir / "index.json"]
    total_bytes = len(raw_index)
    for entry in entries:
        fields(entry, ENTRY_FIELDS, ENTRY_FIELDS - {"errorCode", "lastAttemptAtUtc"}, "server entry")
        key = entry["key"]
        require(isinstance(key, str) and key in expected_keys and key not in seen, "unexpected or duplicate backend")
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
            fields(row, ROW_FIELDS, BASE_ROW_FIELDS, "ranking row")
            if "vehiclesStatus" in row or "vehicles" in row:
                require({"vehiclesStatus", "vehicles"} <= row.keys(), "incomplete vehicle observation")
                vehicle_status = row["vehiclesStatus"]
                require(isinstance(vehicle_status, str) and vehicle_status in {"resolved", "unavailable", "not_collected"}, "invalid vehicle status")
                vehicles = row["vehicles"]
                if vehicle_status == "resolved":
                    require(isinstance(vehicles, list) and len(vehicles) <= 255, "invalid public vehicle list")
                    types = set()
                    for vehicle in vehicles:
                        fields(vehicle, VEHICLE_FIELDS, VEHICLE_FIELDS, "public vehicle")
                        kind = vehicle["capsuleTypeId"]
                        require(type(kind) is int and 0 < kind <= 0xffffffff and kind not in types, "invalid public vehicle type")
                        types.add(kind)
                        require(type(vehicle["phase"]) is int and 0 <= vehicle["phase"] <= 255, "invalid public vehicle phase")
                        require(vehicle["level"] is None or (type(vehicle["level"]) is int and 0 <= vehicle["level"] < 2**31), "invalid public vehicle level")
                        require(type(vehicle["fate"]) is bool, "invalid public vehicle Fate flag")
                else:
                    require(vehicles is None, "unobserved vehicle list must be null")
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
    require(seen == expected_keys, "backend list incomplete")
    require({path.name for path in data_dir.iterdir()} == {path.name for path in paths}, "unexpected files in public data directory")
    return paths


def validate_vehicle_art(source: Path) -> set[Path]:
    """Allow one public reference catalog and hash-addressed static portraits."""
    directory = source / "assets" / "vehicles"
    if not directory.exists():
        return set()
    require(directory.is_dir() and not directory.is_symlink(), "invalid vehicle artwork directory")
    catalog_path = directory / "catalog.json"
    catalog, _ = public_json(catalog_path)
    expected = {"schema", "dataCommit", "models", "units", "assets"}
    fields(catalog, expected, expected, "vehicle artwork catalog")
    require(catalog["schema"] == "metal-waltz.vehicle-art.v1", "unsupported vehicle artwork catalog")
    require(catalog["dataCommit"] == "fee198aee089d57ab4184f4ab0cad5f3ccf67e35", "vehicle artwork data pin mismatch")
    models, units, assets = catalog["models"], catalog["units"], catalog["assets"]
    require(isinstance(models, dict) and 0 < len(models) <= 5000, "invalid artwork model catalog")
    require(isinstance(units, dict) and 0 < len(units) <= 5000, "invalid vehicle reference catalog")
    require(isinstance(assets, dict) and 0 < len(assets) <= 5000, "invalid portrait asset catalog")
    paths, total_bytes = {catalog_path}, 0
    for filename, asset in assets.items():
        require(isinstance(filename, str) and re.fullmatch(r"[a-f0-9]{64}\.png", filename) is not None, "invalid portrait filename")
        fields(asset, {"sha256", "width", "height", "bytes"}, {"sha256", "width", "height", "bytes"}, "portrait asset")
        require(asset["sha256"] == filename[:-4], "portrait filename hash mismatch")
        for dimension in ("width", "height"):
            require(type(asset[dimension]) is int and 0 < asset[dimension] <= 4096, "invalid portrait dimensions")
        require(type(asset["bytes"]) is int and 0 < asset["bytes"] <= 2 * 1024 * 1024, "invalid portrait byte count")
        path = directory / filename
        require(path.is_file() and not path.is_symlink(), "portrait file missing or symbolic link")
        require(path.stat().st_size == asset["bytes"], "portrait byte count mismatch")
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == asset["sha256"], "portrait bytes do not match catalog")
        require(len(raw) >= 33 and raw[:8] == b"\x89PNG\r\n\x1a\n" and raw[12:16] == b"IHDR", "portrait must be a PNG")
        require(struct.unpack(">II", raw[16:24]) == (asset["width"], asset["height"]), "portrait dimensions mismatch")
        paths.add(path)
        total_bytes += len(raw)
        require(total_bytes <= 128 * 1024 * 1024, "portrait collection exceeds size limit")
    referenced = set()
    for model_id, model in models.items():
        require(isinstance(model_id, str) and re.fullmatch(r"[1-9][0-9]{0,9}", model_id) is not None, "invalid model reference")
        fields(model, {"portrait", "portraitKind"}, {"portrait", "portraitKind"}, "portrait model")
        require(isinstance(model["portraitKind"], str) and model["portraitKind"] in {"native-square", "live2d-generated", "unavailable"}, "invalid portrait source kind")
        if model["portraitKind"] == "unavailable":
            require(model["portrait"] is None, "unavailable portrait has a file")
            continue
        prefix = "./assets/vehicles/"
        portrait = model["portrait"]
        require(isinstance(portrait, str) and portrait.startswith(prefix) and portrait[len(prefix):] in assets, "portrait model file is not reviewed")
        referenced.add(portrait[len(prefix):])
    require(referenced == set(assets), "unreferenced portrait assets")
    for type_id, unit in units.items():
        require(isinstance(type_id, str) and re.fullmatch(r"[1-9][0-9]{0,9}", type_id) is not None and int(type_id) <= 0xffffffff, "invalid vehicle reference type")
        fields(unit, {"phases"}, {"phases"}, "vehicle reference")
        require(isinstance(unit["phases"], dict) and bool(unit["phases"]), "vehicle reference phases missing")
        for phase_id, phase in unit["phases"].items():
            require(isinstance(phase_id, str) and phase_id.isdecimal() and 0 <= int(phase_id) <= 255, "invalid reference phase")
            fields(phase, {"name", "maidenName", "defaultModel"}, {"name", "maidenName", "defaultModel"}, "vehicle phase")
            for label in ("name", "maidenName"):
                require(isinstance(phase[label], str) and 0 < len(phase[label]) <= 512, "invalid vehicle reference name")
            require(type(phase["defaultModel"]) is int and str(phase["defaultModel"]) in models, "vehicle model reference missing")
    require(set(directory.iterdir()) == paths, "unexpected files in portrait directory")
    return paths


def build_site(source: Path, output: Path) -> int:
    source = source.resolve()
    output = output.resolve()
    require(source != output and source not in output.parents and output not in source.parents, "source and output must be separate")
    data_paths = validate_data(source / "data")
    art_paths = validate_vehicle_art(source)
    require((source / "index.html").is_file(), "site index missing")
    static_paths: list[Path] = []
    for path in source.rglob("*"):
        require(not path.is_symlink(), "symbolic links are not public site assets")
        if path.is_file() and "data" not in path.relative_to(source).parts:
            require(path.suffix.lower() in STATIC_EXTENSIONS or path.name == ".nojekyll" or path in art_paths, "unexpected static site asset")
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
