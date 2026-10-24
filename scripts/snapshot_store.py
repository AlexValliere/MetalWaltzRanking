"""Keep the latest validated public data in a dedicated, data-only Git branch."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from build_site import LEGACY_SERVER_KEYS, MAX_FILE_BYTES, MAX_TOTAL_BYTES, ValidationError, require, validate_data

BRANCH = "snapshots"
REF = f"refs/heads/{BRANCH}"
MARKER = ".metalwaltz-snapshots.json"
MARKER_VALUE = {"schema": "metal-waltz.snapshots-branch.v1", "history": "latest-only"}
DEFAULT_STATE = Path(".snapshot-state.json")


def git(repo: Path, *args: str, data: bytes | None = None, env: dict[str, str] | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], input=data, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, env=env, timeout=180,
    )
    if result.returncode:
        # Git diagnostics can include configured URLs. Keep logs to a fixed message.
        raise ValidationError(f"Git operation failed ({args[0]})")
    return result.stdout


def remote_head(repo: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-remote", "origin", REF],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90, check=False,
    )
    require(result.returncode == 0, "cannot inspect snapshot branch")
    text = result.stdout.decode("ascii").strip()
    if not text:
        return None
    require(re.fullmatch(r"[0-9a-f]{40,64}\s+refs/heads/snapshots", text) is not None, "unexpected snapshot branch response")
    return text.split()[0]


def inspect_remote(repo: Path, expected: str, destination: Path) -> bool:
    """Extract a validated owned branch; return whether its data needs migration."""
    git(repo, "fetch", "--quiet", "--no-tags", "--depth=1", "origin", REF)
    actual = git(repo, "rev-parse", "FETCH_HEAD").decode("ascii").strip()
    require(actual == expected, "snapshot branch changed while loading")
    commit = git(repo, "cat-file", "-p", actual)
    require(not any(line.startswith(b"parent ") for line in commit.splitlines()), "snapshot branch must have latest-only root history")
    tree = git(repo, "ls-tree", "-r", "-z", "--full-tree", actual)
    files: dict[str, bytes] = {}
    total = 0
    for record in tree.split(b"\0"):
        if not record:
            continue
        metadata, path_raw = record.split(b"\t", 1)
        mode, kind, oid = metadata.split()
        require(mode == b"100644" and kind == b"blob", "snapshot branch contains non-regular files")
        path = path_raw.decode("utf-8")
        require(path == MARKER or re.fullmatch(r"data/[A-Za-z0-9_.-]+\.json", path) is not None, "snapshot branch contains unrelated files")
        require(path not in files, "duplicate snapshot branch path")
        size = int(git(repo, "cat-file", "-s", oid.decode("ascii")))
        require(0 <= size <= MAX_FILE_BYTES, "snapshot branch file exceeds size limit")
        total += size
        require(total <= MAX_TOTAL_BYTES + 1024, "snapshot branch exceeds size limit")
        files[path] = git(repo, "cat-file", "blob", oid.decode("ascii"))
    require(MARKER in files, "snapshot branch ownership marker missing")
    require(json.loads(files.pop(MARKER)) == MARKER_VALUE, "unrecognized snapshot branch ownership marker")
    require(files, "snapshot branch data missing")
    for path, raw in files.items():
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    validate_data(destination / "data", allow_legacy_snapshot_branch=True)
    index = json.loads((destination / "data" / "index.json").read_text(encoding="utf-8"))
    return {entry["key"] for entry in index["servers"]} == LEGACY_SERVER_KEYS


def migrate_legacy(incoming: Path, seed: Path, destination: Path) -> Path:
    """Keep all validated remote backends and add only the new local seed entries."""
    validate_data(seed)
    remote_index = json.loads((incoming / "index.json").read_text(encoding="utf-8"))
    seed_index = json.loads((seed / "index.json").read_text(encoding="utf-8"))
    remote_entries = {entry["key"]: entry for entry in remote_index["servers"]}
    shutil.copytree(incoming, destination)
    entries = []
    for entry in seed_index["servers"]:
        if entry["key"] in LEGACY_SERVER_KEYS:
            entries.append(remote_entries[entry["key"]])
        else:
            entries.append(entry)
            if entry["file"] is not None:
                shutil.copyfile(seed / entry["file"], destination / entry["file"])
    seed_index["servers"] = entries
    seed_index["generatedAtUtc"] = max(
        seed_index["generatedAtUtc"], remote_index["generatedAtUtc"],
        key=lambda value: datetime.fromisoformat(value[:-1] + "+00:00"),
    )
    (destination / "index.json").write_text(json.dumps(seed_index, ensure_ascii=False) + "\n", encoding="utf-8")
    validate_data(destination)
    return destination


def restore(repo: Path, data_dir: Path, state_path: Path) -> None:
    head = remote_head(repo)
    if head is not None:
        with tempfile.TemporaryDirectory(prefix="mw-rankings-restore-") as temporary:
            extracted = Path(temporary)
            legacy = inspect_remote(repo, head, extracted)
            incoming = extracted / "data"
            if legacy:
                incoming = migrate_legacy(incoming, data_dir, extracted / "merged-data")
            # Validation precedes replacement, so damaged remote data cannot erase the seed.
            if data_dir.exists():
                require(data_dir.is_dir() and not data_dir.is_symlink(), "data destination must be a directory")
                shutil.rmtree(data_dir)
            shutil.copytree(incoming, data_dir)
        print("Restored the latest validated public snapshots.")
    else:
        validate_data(data_dir)
        print("Snapshot branch absent; using the validated repository seed.")
    state_path.write_text(json.dumps({"branch": BRANCH, "observedHead": head}) + "\n", encoding="utf-8")


def save(repo: Path, data_dir: Path, state_path: Path) -> None:
    paths = validate_data(data_dir)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    require(set(state) == {"branch", "observedHead"} and state["branch"] == BRANCH, "invalid snapshot restore state")
    expected = state["observedHead"]
    require(expected is None or isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{40,64}", expected) is not None, "invalid observed snapshot commit")
    current = remote_head(repo)
    require(current == expected, "snapshot branch changed since restore; refusing to overwrite")
    if current is not None:
        with tempfile.TemporaryDirectory(prefix="mw-rankings-inspect-") as temporary:
            inspect_remote(repo, current, Path(temporary))
    environment = dict(os.environ)
    environment.update({
        "GIT_AUTHOR_NAME": "github-actions[bot]",
        "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "github-actions[bot]",
        "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
    })
    with tempfile.TemporaryDirectory(prefix="mw-rankings-index-") as temporary:
        environment["GIT_INDEX_FILE"] = str(Path(temporary) / "index")
        git(repo, "read-tree", "--empty", env=environment)
        contents = {f"data/{path.name}": path.read_bytes() for path in paths}
        contents[MARKER] = (json.dumps(MARKER_VALUE, sort_keys=True) + "\n").encode("utf-8")
        for name, raw in sorted(contents.items()):
            oid = git(repo, "hash-object", "-w", "--stdin", data=raw, env=environment).decode("ascii").strip()
            git(repo, "update-index", "--add", "--cacheinfo", f"100644,{oid},{name}", env=environment)
        tree = git(repo, "write-tree", env=environment).decode("ascii").strip()
        if expected is not None:
            previous_tree = git(repo, "rev-parse", f"{expected}^{{tree}}").decode("ascii").strip()
            if tree == previous_tree:
                print("Validated public snapshots are unchanged.")
                return
        commit = git(repo, "commit-tree", tree, data=b"Update latest public ranking snapshots\n", env=environment).decode("ascii").strip()
        lease = f"--force-with-lease={REF}:{expected or ''}"
        git(repo, "push", "--quiet", lease, "origin", f"{commit}:{REF}")
    print("Saved validated public snapshots to the latest-only data branch.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("restore", "save"))
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--data", type=Path, default=Path("site/data"))
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    args = parser.parse_args()
    try:
        if args.command == "restore":
            restore(args.repo.resolve(), args.data.resolve(), args.state.resolve())
        else:
            save(args.repo.resolve(), args.data.resolve(), args.state.resolve())
    except (ValidationError, OSError, ValueError, subprocess.TimeoutExpired):
        print("Snapshot storage failed; no unvalidated data will be published. Check branch ownership, authentication, and concurrent updates.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
