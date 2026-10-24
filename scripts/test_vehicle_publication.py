"""Public profile observations and generated portrait provenance stay verifiable."""

import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from build_site import SERVER_KEYS, ValidationError, build_site, validate_data, validate_vehicle_art
from test_publication import fixture, write_index


def observed_row(directory, index, **changes):
    entry = index["servers"][0]
    path = directory / entry["file"]
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    snapshot["rows"][0].update(changes)
    raw = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    entry["sha256"] = hashlib.sha256(raw).hexdigest()
    write_index(directory, index)


def portrait_fixture(source):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
    raw = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    raw += chunk(b"IDAT", zlib.compress(b"\0\x30\x40\x50\xff")) + chunk(b"IEND", b"")
    sha = hashlib.sha256(raw).hexdigest()
    filename = sha + ".png"
    directory = source / "assets" / "vehicles"
    directory.mkdir(parents=True)
    (directory / filename).write_bytes(raw)
    catalog = {
        "schema": "metal-waltz.vehicle-art.v1",
        "dataCommit": "fee198aee089d57ab4184f4ab0cad5f3ccf67e35",
        "models": {"6032": {"portrait": "./assets/vehicles/" + filename, "portraitKind": "live2d-generated"}},
        "units": {"32": {"phases": {"0": {"name": "Invented vehicle", "maidenName": "Invented maiden", "defaultModel": 6032}}}},
        "assets": {filename: {"sha256": sha, "width": 1, "height": 1, "bytes": len(raw)}},
    }
    (directory / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    return directory, catalog, filename


class VehiclePublicationTests(unittest.TestCase):
    def test_observed_empty_unresolved_unavailable_and_legacy_profiles_remain_distinct(self):
        profiles = [
            {},
            {"vehiclesStatus": "resolved", "vehicles": []},
            {"vehiclesStatus": "unavailable", "vehicles": None},
            {"vehiclesStatus": "unresolved", "vehicles": None},
            {"vehiclesStatus": "not_collected", "vehicles": None},
            {"vehiclesStatus": "resolved", "vehicles": [{"capsuleTypeId": 32, "phase": 4, "level": None, "fate": False}]},
        ]
        for profile in profiles:
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as folder:
                directory = Path(folder)
                index = fixture(directory)
                observed_row(directory, index, **profile)
                self.assertEqual(len(validate_data(directory)), len(SERVER_KEYS) + 1)

    def test_private_instance_fields_and_fabricated_unavailable_vehicles_are_rejected(self):
        examples = [
            {"vehiclesStatus": "resolved"},
            {"vehicles": []},
            {"vehiclesStatus": "unavailable", "vehicles": []},
            {"vehiclesStatus": "unresolved", "vehicles": []},
            {"vehiclesStatus": "unresolved", "vehicles": [{"capsuleTypeId": 32, "phase": 4, "level": 50, "fate": True}]},
            {"vehiclesStatus": "unresolved"},
            {"vehiclesStatus": "guessed", "vehicles": None},
            {"vehiclesStatus": "resolved", "vehicles": [{"capsuleTypeId": 32, "phase": 4, "level": 50, "fate": True, "instanceId": 123}]},
            {"vehiclesStatus": "resolved", "vehicles": [{"capsuleTypeId": 32, "phase": 4, "level": -1, "fate": True}]},
        ]
        for profile in examples:
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as folder:
                directory = Path(folder)
                index = fixture(directory)
                observed_row(directory, index, **profile)
                with self.assertRaises(ValidationError):
                    validate_data(directory)

    def test_unresolved_null_and_actual_vehicle_data_are_published_without_rewriting(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / "site"
            index = fixture(source / "data")
            (source / "index.html").write_text("<h1>Rankings</h1>", encoding="utf-8")
            entry = index["servers"][0]
            path = source / "data" / entry["file"]
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            snapshot["rows"][0].update(vehiclesStatus="unresolved", vehicles=None)
            snapshot["rows"][1].update(vehiclesStatus="resolved", vehicles=[
                {"capsuleTypeId": 32, "phase": 4, "level": 50, "fate": True},
            ])
            raw = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
            path.write_bytes(raw)
            entry["sha256"] = hashlib.sha256(raw).hexdigest()
            write_index(source / "data", index)
            self.assertEqual(build_site(source, base / "public"), len(SERVER_KEYS))
            self.assertEqual((base / "public" / "data" / entry["file"]).read_bytes(), raw)

    def test_generated_portrait_and_catalog_are_published_after_hash_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / "site"
            fixture(source / "data")
            (source / "index.html").write_text("<h1>Rankings</h1>", encoding="utf-8")
            directory, _, filename = portrait_fixture(source)
            self.assertEqual(len(validate_vehicle_art(source)), 2)
            self.assertEqual(build_site(source, base / "public"), len(SERVER_KEYS))
            self.assertEqual((directory / filename).read_bytes(), (base / "public" / "assets" / "vehicles" / filename).read_bytes())

    def test_tampered_portrait_bytes_and_dimensions_block_publication(self):
        for corruption in ("bytes", "dimensions"):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as folder:
                source = Path(folder)
                directory, catalog, filename = portrait_fixture(source)
                if corruption == "bytes":
                    path = directory / filename
                    raw = path.read_bytes()
                    path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
                else:
                    catalog["assets"][filename]["width"] = 2
                    (directory / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
                with self.assertRaises(ValidationError):
                    validate_vehicle_art(source)

    def test_external_portraits_and_private_catalog_fields_are_rejected(self):
        for corruption in ("external", "private"):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as folder:
                source = Path(folder)
                directory, catalog, _ = portrait_fixture(source)
                if corruption == "external":
                    catalog["models"]["6032"]["portrait"] = "https://example.com/avatar.png"
                else:
                    catalog["accountId"] = 123
                (directory / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
                with self.assertRaises(ValidationError):
                    validate_vehicle_art(source)


if __name__ == "__main__":
    unittest.main()
