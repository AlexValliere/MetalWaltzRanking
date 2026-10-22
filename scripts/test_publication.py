"""Offline regression cases for publication and durable last-good snapshots."""

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from build_site import SERVER_KEYS, ValidationError, build_site, validate_data
from snapshot_store import restore, save

STAMP = "2026-10-22T12:00:00Z"


def fixture(directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    entries = []
    for key in sorted(SERVER_KEYS):
        value = {
            "schema": "maiden-waltz.public-ranking-snapshot.v1", "serverKey": key,
            "aliases": [key], "serverNames": [key], "fund": 5,
            "startedAtUtc": STAMP, "completedAtUtc": STAMP,
            "rankingEnumerationComplete": True, "namesComplete": False,
            "scope": "All server-exposed rows during a sequential scan.",
            "rows": [
                {"rank": 1, "name": "雪姫<script>", "grandeur": 250, "nameStatus": "resolved"},
                {"rank": 2, "name": None, "grandeur": 200, "nameStatus": "unavailable"},
            ],
        }
        raw = (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")
        filename = f"{key}.rankings.json"
        (directory / filename).write_bytes(raw)
        entries.append({
            "key": key, "name": key, "aliases": [key], "file": filename,
            "rows": 2, "namesResolved": 1, "namesMissing": 1,
            "startedAtUtc": STAMP, "completedAtUtc": STAMP,
            "refreshStatus": "seed", "sha256": hashlib.sha256(raw).hexdigest(),
        })
    index = {"schema": "metal-waltz.rankings-index.v1", "generatedAtUtc": STAMP, "servers": entries}
    (directory / "index.json").write_text(json.dumps(index) + "\n", encoding="utf-8")
    return index


def write_index(directory: Path, index: dict) -> None:
    (directory / "index.json").write_text(json.dumps(index) + "\n", encoding="utf-8")


class PublicationTests(unittest.TestCase):
    def test_unicode_missing_names_and_copy_allowlist(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "site"
            fixture(source / "data")
            (source / "index.html").write_text("<h1>Rankings</h1>", encoding="utf-8")
            (base / "private.dpapi").write_bytes(b"private")
            self.assertEqual(build_site(source, base / "_site"), 7)
            self.assertFalse((base / "_site" / "private.dpapi").exists())
            self.assertEqual(len(list((base / "_site" / "data").iterdir())), 8)

    def test_private_extra_field_is_rejected_even_with_matching_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            index = fixture(directory)
            entry = index["servers"][0]
            path = directory / entry["file"]
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            snapshot["rows"][0]["playerId"] = 123
            raw = json.dumps(snapshot).encode("utf-8")
            path.write_bytes(raw)
            entry["sha256"] = hashlib.sha256(raw).hexdigest()
            write_index(directory, index)
            with self.assertRaises(ValidationError):
                validate_data(directory)

    def test_stale_snapshot_preserved_with_failure_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            index = fixture(directory)
            entry = index["servers"][0]
            entry.update(refreshStatus="failed", errorCode="connect_timeout", lastAttemptAtUtc=STAMP)
            write_index(directory, index)
            self.assertEqual(len(validate_data(directory)), 8)

    def test_path_traversal_and_extra_file_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            index = fixture(directory)
            index["servers"][0]["file"] = "../private.json"
            write_index(directory, index)
            with self.assertRaises(ValidationError):
                validate_data(directory)
            fixture(directory)
            (directory / "capture.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(ValidationError):
                validate_data(directory)

    def test_tampered_snapshot_hash_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            index = fixture(directory)
            with (directory / index["servers"][0]["file"]).open("ab") as output:
                output.write(b" ")
            with self.assertRaises(ValidationError):
                validate_data(directory)


class SnapshotStoreTests(unittest.TestCase):
    @staticmethod
    def git(repo: Path, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(repo), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return result.stdout.decode("utf-8").strip()

    def repositories(self, base: Path) -> tuple[Path, Path]:
        remote = base / "remote.git"
        local = base / "repo"
        remote.mkdir()
        local.mkdir()
        self.git(remote, "init", "--bare", "--quiet")
        self.git(local, "init", "--quiet")
        self.git(local, "remote", "add", "origin", str(remote))
        return local, remote

    def test_latest_only_round_trip_and_unchanged_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repo, remote = self.repositories(base)
            data = repo / "site" / "data"
            fixture(data)
            state = base / "state.json"
            restore(repo, data, state)
            save(repo, data, state)
            first = self.git(remote, "rev-parse", "refs/heads/snapshots")
            self.assertEqual(self.git(remote, "rev-list", "--count", first), "1")
            restore(repo, data, state)
            save(repo, data, state)
            self.assertEqual(self.git(remote, "rev-parse", "refs/heads/snapshots"), first)
            index = json.loads((data / "index.json").read_text(encoding="utf-8"))
            index["servers"][0].update(refreshStatus="failed", errorCode="connect_timeout")
            write_index(data, index)
            save(repo, data, state)
            second = self.git(remote, "rev-parse", "refs/heads/snapshots")
            self.assertNotEqual(first, second)
            self.assertEqual(self.git(remote, "rev-list", "--count", second), "1")

    def test_unrelated_remote_branch_refused_without_replacing_seed(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repo, remote = self.repositories(base)
            data = repo / "site" / "data"
            fixture(data)
            (repo / "unrelated.txt").write_text("Keep me", encoding="utf-8")
            self.git(repo, "add", "unrelated.txt")
            self.git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "Existing unrelated branch")
            self.git(repo, "push", "--quiet", "origin", "HEAD:refs/heads/snapshots")
            before = (data / "index.json").read_bytes()
            with self.assertRaises(ValidationError):
                restore(repo, data, base / "state.json")
            self.assertEqual((data / "index.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
