"""Sequential collection and sanitized refresh results; credentials in memory only."""

import json
from pathlib import Path
import socket
import struct

from .binary import Reader
from .cipher import GameDes
from .connection import CollectorConnection, MAX_ROWS
from .records import login_payload, login_reply, player_list, require_zero, ServerRejection
from .snapshots import (INDEX_SCHEMA, SNAPSHOT_SCHEMA, SCOPE, atomic_json, now,
                        previous_attempts, snapshot_entry, validate_snapshot)


class CollectionStopped(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__("Collection stopped")


def open_socket(server):
    # Try another reviewed port only if no connection has been established.
    # Authentication, reads and ambiguous responses are never replayed.
    for port in server["ports"]:
        try:
            return socket.create_connection((server["address"], port), timeout=5)
        except OSError:
            pass
    raise ConnectionError("No reviewed endpoint could be reached")


def enumerate_rankings(connection, *, progress=None):
    rows, seen, overlap_checked = [], set(), False
    while True:
        page = connection.fetch_rank_page(len(rows))
        if not page:
            break
        if len(rows) + len(page) > MAX_ROWS:
            raise CollectionStopped("row_limit")
        if any(player in seen for player, _ in page):
            raise CollectionStopped("ranking_changed_or_offset_ignored")
        seen.update(player for player, _ in page)
        rows.extend(page)
        connection.fetch_names([player for player, _ in page])
        if not overlap_checked and len(rows) >= 201:
            overlap = connection.fetch_rank_page(199, 2)
            if [p for p, _ in overlap] != [p for p, _ in rows[199:201]]:
                raise CollectionStopped("ranking_overlap_changed")
            overlap_checked = True
        if progress and (len(rows) % 5000 == 0 or len(page) < 200):
            progress(len(rows))
    if any(a[1] < b[1] for a, b in zip(rows, rows[1:])):
        raise CollectionStopped("ranking_score_order_changed")
    return rows


def scan_server(server, credentials, masks, *, progress=None):
    started = now()
    request = login_payload(*credentials)
    with open_socket(server) as sock:
        sock.settimeout(0.4)
        connection = CollectorConnection(sock, masks, GameDes(), request)
        connection.send(324, request)
        status, account = login_reply(connection.receive(331))
        require_zero("login", status)
        connection.send(396)
        status, players = player_list(connection.receive(310))
        require_zero("player_list", status)
        if len(players) != 1:
            raise CollectionStopped("commander_missing" if not players else "commander_ambiguous")
        player = players[0][0]
        connection.selected_player = player
        connection.send(316, struct.pack("<I", player))
        r = Reader(connection.receive(312))
        require_zero("selection", r.u16())
        identity = r.u32(), r.u32()
        r.finish()
        if identity != (account, player):
            raise CollectionStopped("selected_identity_mismatch")
        r = Reader(connection.receive(362))
        r.string(48)
        r.u8()
        r.u32()
        r.u32()
        r.finish()
        connection.send(357)
        connection.receive(368)
        connection.send(374)
        r = Reader(connection.receive(371))
        require_zero("town_entry", r.u16())
        r.u16()
        r.finish()
        connection.achievements = {}
        connection.send(436)
        connection.receive(438)
        rows = enumerate_rankings(connection, progress=progress)
        public_rows = []
        for rank, (identity, score) in enumerate(rows, 1):
            resolved = connection.name_resolved(identity)
            public_rows.append({"rank": rank, "name": connection.names[identity] if resolved else None,
                                "grandeur": score, "nameStatus": "resolved" if resolved else "unavailable"})
        snapshot = {"schema": SNAPSHOT_SCHEMA, "serverKey": server["key"], "aliases": server["aliases"],
                    "serverNames": server["serverNames"], "fund": 5, "startedAtUtc": started,
                    "completedAtUtc": now(), "rankingEnumerationComplete": True,
                    "namesComplete": all(row["nameStatus"] == "resolved" for row in public_rows),
                    "scope": SCOPE, "rows": public_rows}
        validate_snapshot(snapshot, server)
        return snapshot


def error_code(error):
    # Do not stringify arbitrary exceptions: packet bytes, identities or secrets
    # can appear in third-party, Unicode and socket exception text.
    if isinstance(error, ServerRejection):
        if error.phase in ("login", "player_list", "selection", "town_entry", "ranking"):
            return "server_" + error.phase + "_" + str(int(error.status))
        return "server_rejected"
    if isinstance(error, CollectionStopped):
        return error.code
    if isinstance(error, TimeoutError):
        return "reply_timeout"
    if isinstance(error, (ConnectionError, OSError)):
        return "connection_failed"
    if isinstance(error, (ValueError, KeyError, TypeError, struct.error)):
        return "protocol_or_snapshot_invalid"
    return "collector_failed"


def log(**fields):
    print(json.dumps(fields, ensure_ascii=True), flush=True)


def run_collection(directory, servers, selected, credentials, masks, *, scanner=scan_server, emit=log):
    directory = Path(directory)
    old_attempts = previous_attempts(directory)
    entries, failures = [], 0
    # Read existing files once. Counts and hashes always derive from validated
    # snapshot bytes; only sanitized attempt metadata comes from the old index.
    for server in servers:
        entry = snapshot_entry(directory, server)
        if entry["file"] is not None:
            entry.update(old_attempts.get(server["key"], {}))
        entries.append(entry)
    for position, server in enumerate(servers):
        key = server["key"]
        if key not in selected:
            continue
        entry = entries[position]
        attempted = now()
        emit(serverKey=key, phase="collecting")
        try:
            if credentials is None:
                raise CollectionStopped("credentials_missing")
            snapshot = scanner(server, credentials, masks,
                               progress=lambda count: emit(serverKey=key, phase="progress", rows=count))
            validate_snapshot(snapshot, server)
            atomic_json(directory / (key + ".rankings.json"), snapshot)
            entry = snapshot_entry(directory, server, "ok")
            if entry["file"] is None:
                raise CollectionStopped("snapshot_write_failed")
        except Exception as error:
            failures += 1
            entry.update(refreshStatus="failed", errorCode=error_code(error))
        entry["lastAttemptAtUtc"] = attempted
        entries[position] = entry
        emit(serverKey=key, phase="finished", refreshStatus=entry["refreshStatus"],
             rows=entry["rows"], namesResolved=entry["namesResolved"],
             **({"errorCode": entry["errorCode"]} if "errorCode" in entry else {}))
        # Save each completed backend immediately, keeping a coherent full index
        # even if a later backend reaches the runner's outer timeout.
        atomic_json(directory / "index.json", {"schema": INDEX_SCHEMA, "generatedAtUtc": now(),
                                               "servers": entries})
    atomic_json(directory / "index.json", {"schema": INDEX_SCHEMA, "generatedAtUtc": now(), "servers": entries})
    return 1 if failures else 0
