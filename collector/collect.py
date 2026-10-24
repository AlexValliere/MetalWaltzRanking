"""Sequential collection and sanitized refresh results; credentials in memory only."""

import json
import math
from pathlib import Path
import socket
import struct
import time

from .binary import Reader
from .cipher import GameDes
from .connection import (CONNECTION_FAILURE_OPERATIONS, CONNECTION_FAILURE_REASONS,
                         CollectorConnection, MAX_ROWS, SESSION_SECONDS)
from .configuration import load_masks
from .records import login_payload, login_reply, player_list, require_zero, ServerRejection
from .snapshots import (INDEX_SCHEMA, SNAPSHOT_SCHEMA, SCOPE, atomic_json, now,
                        previous_attempts, snapshot_entry, validate_snapshot)
from .timing import (CollectionDeadline, DEFAULT_MAX_DURATION_SECONDS, positive_duration,
                     remaining_seconds)

READ_PHASES = frozenset(("not_started", "connect", "login", "commander_list", "selection", "commander_base",
                         "town_prepare", "town_entry", "achievements", "rankings", "primary_names",
                         "nickname_fallback", "public_vehicles", "snapshot", "unknown"))


class CollectionStopped(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__("Collection stopped")


def open_socket(server, *, deadline=None, clock=time.monotonic):
    # Try another reviewed port only if no connection has been established.
    # Authentication, reads and ambiguous responses are never replayed.
    for route in (server, *server.get("alternateRoutes", [])):
        for port in route["ports"]:
            remaining = remaining_seconds(deadline, clock)
            try:
                sock = socket.create_connection((route["address"], port),
                                                timeout=min(5, remaining) if remaining is not None else 5)
            except OSError:
                remaining_seconds(deadline, clock)
                continue
            try:
                remaining_seconds(deadline, clock)
            except CollectionDeadline:
                sock.close()
                raise
            return sock
    raise ConnectionError("No reviewed endpoint could be reached")


def enumerate_rankings(connection, *, progress=None, collect_vehicles=True):
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
        connection.ranking_rows_fetched = len(rows)
        connection.fetch_names([player for player, _ in page])
        if collect_vehicles:
            connection.fetch_vehicles([player for player, _ in page])
        if not overlap_checked and len(rows) >= 201:
            overlap = connection.fetch_rank_page(199, 2)
            if [p for p, _ in overlap] != [p for p, _ in rows[199:201]]:
                raise CollectionStopped("ranking_overlap_changed")
            overlap_checked = True
        connection.ranking_rows_completed = len(rows)
        if progress and (len(rows) % 5000 == 0 or len(page) < 200):
            progress(len(rows))
    if any(a[1] < b[1] for a, b in zip(rows, rows[1:])):
        raise CollectionStopped("ranking_score_order_changed")
    return rows


def scan_server(server, credentials, masks, *, progress=None, deadline=None, clock=time.monotonic):
    connection = None
    try:
        remaining_seconds(deadline, clock)
        started = now()
        request = login_payload(*credentials)
        if server.get("protocolProfile", "mobile") != "mobile":
            masks = load_masks(server["protocolProfile"])
        with open_socket(server, deadline=deadline, clock=clock) as sock:
            sock.settimeout(0.4)
            connection = CollectorConnection(sock, masks, GameDes(), request, deadline=deadline, clock=clock,
                                             batch_request_gap_seconds=server.get("batchRequestGapSeconds", 0.25),
                                             session_seconds=server.get("sessionSeconds", SESSION_SECONDS),
                                             vehicle_zero_recovery=server.get("protocolProfile", "mobile") == "mobile",
                                             vehicle_partial_results=server.get("protocolProfile", "mobile") == "mobile",
                                             native_keepalive=server.get("protocolProfile", "mobile") == "mobile")
            connection.read_phase = "login"
            connection.send(324, request)
            status, account = login_reply(connection.receive(331))
            require_zero("login", status)
            connection.read_phase = "commander_list"
            connection.send(396)
            status, players = player_list(connection.receive(310))
            require_zero("player_list", status)
            if len(players) != 1:
                raise CollectionStopped("commander_missing" if not players else "commander_ambiguous")
            player = players[0][0]
            connection.selected_player = player
            connection.read_phase = "selection"
            connection.send(316, struct.pack("<I", player))
            r = Reader(connection.receive(312))
            require_zero("selection", r.u16())
            identity = r.u32(), r.u32()
            r.finish()
            if identity != (account, player):
                raise CollectionStopped("selected_identity_mismatch")
            connection.read_phase = "commander_base"
            r = Reader(connection.receive(362))
            r.string(48)
            r.u8()
            r.u32()
            r.u32()
            r.finish()
            connection.read_phase = "town_prepare"
            connection.send(357)
            connection.receive(368)
            connection.read_phase = "town_entry"
            connection.send(374)
            r = Reader(connection.receive(371))
            require_zero("town_entry", r.u16())
            r.u16()
            r.finish()
            connection.achievements = {}
            connection.read_phase = "achievements"
            connection.send(436)
            connection.receive(438)
            collect_vehicles = server.get("collectVehicles", True)
            rows = enumerate_rankings(connection, progress=progress, collect_vehicles=collect_vehicles)
            if collect_vehicles:
                connection.finish_vehicles()
            public_rows = []
            for rank, (identity, score) in enumerate(rows, 1):
                resolved = connection.name_resolved(identity)
                vehicle_status = ("not_collected" if not collect_vehicles else
                                  "resolved" if identity in connection.vehicles else
                                  "unresolved" if identity in connection.vehicle_unresolved else
                                  "unavailable" if identity in connection.vehicle_failures else None)
                if vehicle_status is None:
                    raise ValueError("Missing public vehicle observation")
                public_rows.append({"rank": rank, "name": connection.names[identity] if resolved else None,
                                    "grandeur": score, "nameStatus": "resolved" if resolved else "unavailable",
                                    "vehiclesStatus": vehicle_status,
                                    "vehicles": connection.vehicles[identity] if vehicle_status == "resolved" else None})
            snapshot = {"schema": SNAPSHOT_SCHEMA, "serverKey": server["key"], "aliases": server["aliases"],
                        "serverNames": server["serverNames"], "fund": 5, "startedAtUtc": started,
                        "completedAtUtc": now(), "rankingEnumerationComplete": True,
                        "namesComplete": all(row["nameStatus"] == "resolved" for row in public_rows),
                        "scope": SCOPE, "rows": public_rows}
            validate_snapshot(snapshot, server)
            return snapshot
    except Exception as error:
        error.collector_read_phase = connection.read_phase if connection is not None else "connect"
        if connection is not None:
            error.collector_diagnostics = connection.failure_diagnostics(error)
        raise


def error_code(error):
    # Do not stringify arbitrary exceptions: packet bytes, identities or secrets
    # can appear in third-party, Unicode and socket exception text.
    if isinstance(error, CollectionDeadline):
        return "collection_deadline"
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


def failure_diagnostics(error):
    """Whitelist local failure diagnostics again at the log boundary."""
    supplied = getattr(error, "collector_diagnostics", None)
    if not isinstance(supplied, dict):
        return {}
    result = {}
    for key in ("heartbeatsReceived", "heartbeatAcksSent", "idlePingsSent",
                "attemptRowsFetched", "attemptRowsCompleted"):
        value = supplied.get(key)
        if type(value) is int and value >= 0:
            result[key] = value
    for key in ("sessionElapsedSeconds", "lastReceiveAgeSeconds", "lastSuccessfulSendAgeSeconds",
                "lastHeartbeatReceiveAgeSeconds", "lastHeartbeatAckAgeSeconds", "lastIdlePingAgeSeconds"):
        if key not in supplied:
            continue
        value = supplied[key]
        if (value is None or type(value) is int and value >= 0
                or type(value) is float and math.isfinite(value) and value >= 0):
            result[key] = value
    for key, allowed in (("transportFailure", CONNECTION_FAILURE_REASONS),
                         ("transportOperation", CONNECTION_FAILURE_OPERATIONS)):
        value = supplied.get(key)
        if isinstance(value, str) and value in allowed:
            result[key] = value
    return result


def run_collection(directory, servers, selected, credentials, masks, *, scanner=scan_server, emit=log,
                   max_duration_seconds=DEFAULT_MAX_DURATION_SECONDS, clock=time.monotonic):
    deadline = clock() + positive_duration(max_duration_seconds)
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
        read_phase, failed_read_phase = "not_started", None
        diagnostics = {}
        vehicle_coverage = {}
        try:
            remaining_seconds(deadline, clock)
            if credentials is None:
                raise CollectionStopped("credentials_missing")
            emit(serverKey=key, phase="collecting")
            read_phase = "connect"
            snapshot = scanner(server, credentials, masks,
                               progress=lambda count: emit(serverKey=key, phase="progress", rows=count),
                               deadline=deadline, clock=clock)
            read_phase = "snapshot"
            validate_snapshot(snapshot, server)
            atomic_json(directory / (key + ".rankings.json"), snapshot)
            entry = snapshot_entry(directory, server, "ok")
            if entry["file"] is None:
                raise CollectionStopped("snapshot_write_failed")
            vehicle_coverage = {"vehiclesResolvedEmpty": 0, "vehiclesResolvedNonempty": 0,
                                "vehiclesUnavailable": 0, "vehiclesUnresolved": 0,
                                "vehiclesNotCollected": 0}
            for row in snapshot["rows"]:
                status = row.get("vehiclesStatus", "not_collected")
                field = (("vehiclesResolvedNonempty" if row["vehicles"] else "vehiclesResolvedEmpty")
                         if status == "resolved" else {"unavailable": "vehiclesUnavailable",
                                                      "unresolved": "vehiclesUnresolved",
                                                      "not_collected": "vehiclesNotCollected"}[status])
                vehicle_coverage[field] += 1
        except Exception as error:
            failures += 1
            entry.update(refreshStatus="failed", errorCode=error_code(error))
            phase = getattr(error, "collector_read_phase", read_phase)
            failed_read_phase = phase if isinstance(phase, str) and phase in READ_PHASES else "unknown"
            diagnostics = failure_diagnostics(error)
        entry["lastAttemptAtUtc"] = attempted
        entries[position] = entry
        emit(serverKey=key, phase="finished", refreshStatus=entry["refreshStatus"],
             rows=entry["rows"], namesResolved=entry["namesResolved"],
             **vehicle_coverage,
             **({"errorCode": entry["errorCode"]} if "errorCode" in entry else {}),
             **({"readPhase": failed_read_phase} if failed_read_phase is not None else {}),
             **diagnostics)
        # Save each completed backend immediately, keeping a coherent full index
        # even if a later backend reaches the runner's outer timeout.
        atomic_json(directory / "index.json", {"schema": INDEX_SCHEMA, "generatedAtUtc": now(),
                                               "servers": entries})
    atomic_json(directory / "index.json", {"schema": INDEX_SCHEMA, "generatedAtUtc": now(), "servers": entries})
    return 1 if failures else 0
