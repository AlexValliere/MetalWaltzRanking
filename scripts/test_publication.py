"""Offline regression cases for publication and durable last-good snapshots."""

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from build_site import LEGACY_SERVER_KEYS, SERVER_KEYS, ValidationError, build_site, validate_data
from snapshot_store import MARKER, MARKER_VALUE, restore, save

STAMP = "2026-10-22T12:00:00Z"


def fixture(directory: Path, keys=SERVER_KEYS, stamp: str = STAMP) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    entries = []
    for key in sorted(keys):
        value = {
            "schema": "maiden-waltz.public-ranking-snapshot.v1", "serverKey": key,
            "aliases": [key], "serverNames": [key], "fund": 5,
            "startedAtUtc": stamp, "completedAtUtc": stamp,
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
            "startedAtUtc": stamp, "completedAtUtc": stamp,
            "refreshStatus": "seed", "sha256": hashlib.sha256(raw).hexdigest(),
        })
    index = {"schema": "metal-waltz.rankings-index.v1", "generatedAtUtc": stamp, "servers": entries}
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
            self.assertEqual(build_site(source, base / "_site"), len(SERVER_KEYS))
            self.assertFalse((base / "_site" / "private.dpapi").exists())
            self.assertEqual(len(list((base / "_site" / "data").iterdir())), len(SERVER_KEYS) + 1)

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
            self.assertEqual(len(validate_data(directory)), len(SERVER_KEYS) + 1)

    def test_legacy_validation_requires_opt_in_and_exact_original_keys(self):
        examples = [
            (LEGACY_SERVER_KEYS, True),
            (LEGACY_SERVER_KEYS - {"Bonn"}, False),
            ((LEGACY_SERVER_KEYS - {"Bonn"}) | {"SteamJP"}, False),
            (LEGACY_SERVER_KEYS | {"SteamJP"}, False),
            (SERVER_KEYS | {"SteamTEST"}, False),
        ]
        for keys, legacy_valid in examples:
            with self.subTest(keys=keys), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                fixture(directory, keys)
                with self.assertRaises(ValidationError):
                    validate_data(directory)
                if legacy_valid:
                    self.assertEqual(len(validate_data(directory, allow_legacy_snapshot_branch=True)), len(LEGACY_SERVER_KEYS) + 1)
                else:
                    with self.assertRaises(ValidationError):
                        validate_data(directory, allow_legacy_snapshot_branch=True)

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

    def publish_branch(self, repo: Path) -> str:
        """Publish an owned fixture to a local bare repository, without a network."""
        (repo / MARKER).write_text(json.dumps(MARKER_VALUE) + "\n", encoding="utf-8")
        self.git(repo, "add", "data", MARKER)
        tree = self.git(repo, "write-tree")
        commit = self.git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit-tree", tree, "-m", "Fixture snapshots")
        self.git(repo, "push", "--quiet", "--force", "origin", f"{commit}:refs/heads/snapshots")
        return commit

    def test_legacy_restore_preserves_newer_remote_and_migrates_save_to_full_set(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repo, remote = self.repositories(base)
            remote_data = repo / "data"
            remote_index = fixture(remote_data, LEGACY_SERVER_KEYS, "2026-10-23T12:00:00Z")
            remote_index["servers"][0].update(refreshStatus="failed", errorCode="connect_timeout", lastAttemptAtUtc="2026-10-23T13:00:00Z")
            write_index(remote_data, remote_index)
            original = {path.name: path.read_bytes() for path in remote_data.iterdir()}
            old_head = self.publish_branch(repo)
            data = repo / "site" / "data"
            seed_index = fixture(data)
            missing = next(entry for entry in seed_index["servers"] if entry["key"] == "SteamJP")
            (data / missing["file"]).unlink()
            missing.update(
                file=None, rows=0, namesResolved=0, namesMissing=0,
                startedAtUtc=None, completedAtUtc=None, sha256=None,
                refreshStatus="failed", errorCode="steam_auth_unverified",
            )
            write_index(data, seed_index)
            steam_entries = [entry for entry in seed_index["servers"] if entry["key"] not in LEGACY_SERVER_KEYS]
            steam_files = {entry["file"]: (data / entry["file"]).read_bytes() for entry in steam_entries if entry["file"] is not None}
            state = base / "state.json"

            restore(repo, data, state)

            merged = json.loads((data / "index.json").read_text(encoding="utf-8"))
            entries = {entry["key"]: entry for entry in merged["servers"]}
            self.assertEqual(merged["generatedAtUtc"], remote_index["generatedAtUtc"])
            for entry in remote_index["servers"]:
                self.assertEqual(entries[entry["key"]], entry)
                self.assertEqual((data / entry["file"]).read_bytes(), original[entry["file"]])
            for entry in steam_entries:
                self.assertEqual(entries[entry["key"]], entry)
                if entry["file"] is not None:
                    self.assertEqual((data / entry["file"]).read_bytes(), steam_files[entry["file"]])
            self.assertEqual(len(validate_data(data)), len(LEGACY_SERVER_KEYS) + len(steam_files) + 1)
            self.assertEqual(json.loads(state.read_text())["observedHead"], old_head)

            save(repo, data, state)

            new_head = self.git(remote, "rev-parse", "refs/heads/snapshots")
            self.assertNotEqual(new_head, old_head)
            self.assertEqual(self.git(remote, "rev-list", "--count", new_head), "1")
            saved_index = json.loads(self.git(remote, "show", f"{new_head}:data/index.json"))
            self.assertEqual({entry["key"] for entry in saved_index["servers"]}, SERVER_KEYS)
            restore(repo, data, state)
            save(repo, data, state)
            self.assertEqual(self.git(remote, "rev-parse", "refs/heads/snapshots"), new_head)

    def test_invalid_legacy_remote_cannot_replace_seed_or_write_restore_state(self):
        for corruption in ("partial", "mixed_keys", "private_field", "extra_file", "hash"):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                repo, _ = self.repositories(base)
                remote_data = repo / "data"
                keys = (LEGACY_SERVER_KEYS - {"Bonn"}) | {"SteamJP"} if corruption == "mixed_keys" else LEGACY_SERVER_KEYS
                remote_index = fixture(remote_data, keys)
                entry = remote_index["servers"][0]
                if corruption == "partial":
                    remote_index["servers"].pop(0)
                    (remote_data / entry["file"]).unlink()
                elif corruption == "private_field":
                    path = remote_data / entry["file"]
                    snapshot = json.loads(path.read_text(encoding="utf-8"))
                    snapshot["rows"][0]["playerId"] = 123
                    raw = json.dumps(snapshot).encode("utf-8")
                    path.write_bytes(raw)
                    entry["sha256"] = hashlib.sha256(raw).hexdigest()
                elif corruption == "extra_file":
                    (remote_data / "capture.json").write_text("{}", encoding="utf-8")
                elif corruption == "hash":
                    with (remote_data / entry["file"]).open("ab") as output:
                        output.write(b" ")
                write_index(remote_data, remote_index)
                self.publish_branch(repo)
                data = repo / "site" / "data"
                fixture(data)
                before = {path.name: path.read_bytes() for path in data.iterdir()}
                state = base / "state.json"

                with self.assertRaises(ValidationError):
                    restore(repo, data, state)

                self.assertEqual({path.name: path.read_bytes() for path in data.iterdir()}, before)
                self.assertFalse(state.exists())

    def test_legacy_migration_rejects_invalid_seed_before_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repo, _ = self.repositories(base)
            fixture(repo / "data", LEGACY_SERVER_KEYS)
            self.publish_branch(repo)
            data = repo / "site" / "data"
            fixture(data)
            with (data / "SteamJP.rankings.json").open("ab") as output:
                output.write(b" ")
            before = {path.name: path.read_bytes() for path in data.iterdir()}

            with self.assertRaises(ValidationError):
                restore(repo, data, base / "state.json")

            self.assertEqual({path.name: path.read_bytes() for path in data.iterdir()}, before)

    def test_save_requires_full_set_and_refuses_changed_remote_after_migration(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repo, remote = self.repositories(base)
            fixture(repo / "data", LEGACY_SERVER_KEYS)
            original_head = self.publish_branch(repo)
            data = repo / "site" / "data"
            fixture(data, LEGACY_SERVER_KEYS)
            state = base / "state.json"
            state.write_text(json.dumps({"branch": "snapshots", "observedHead": original_head}), encoding="utf-8")
            with self.assertRaises(ValidationError):
                save(repo, data, state)
            self.assertEqual(self.git(remote, "rev-parse", "refs/heads/snapshots"), original_head)
            fixture(data)
            restore(repo, data, state)
            fixture(repo / "data", LEGACY_SERVER_KEYS, "2026-10-23T12:00:00Z")
            changed_head = self.publish_branch(repo)

            with self.assertRaises(ValidationError):
                save(repo, data, state)

            self.assertEqual(self.git(remote, "rev-parse", "refs/heads/snapshots"), changed_head)

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
