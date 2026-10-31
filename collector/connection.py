"""A single read-only native session with bounded, sequential transactions.

Adapted from the live-verified MaidenWaltz collector experiments. No account or
commander creation, battle, reward, tutorial, purchase or claim commands exist.
Unkeyed ranking/17 replies are drained before a new transaction is started.
"""

from collections import Counter, deque
import errno
import math
import socket
import struct
import time

from .binary import Reader
from .records import detail_records, ranking_records, require_zero, social_records, validate_outgoing
from .timing import CollectionDeadline, positive_duration, remaining_seconds
from .transport import TransportDecoder, TransportEncoder

MAX_ROWS = 1_000_000
LIMITS = {324: 1, 396: 1, 316: 1, 357: 1, 374: 1, 436: 1,
          137: 10_000, 15: 10_000, 16: MAX_ROWS + 200, 20: 40_000, 65530: 1000, 65531: 1000}
REPLY_SECONDS = 30
PRIMARY_NAME_SECONDS = 12
SESSION_SECONDS = 3600
BATCH_REQUEST_GAP_SECONDS = 1.0
PRIMARY_NAME_BATCH_SIZE = 50
NICKNAME_BATCH_SIZE = 1
NATIVE_IDLE_SECONDS = (30, 40, 50, 60)
CONNECTION_FAILURE_REASONS = frozenset(("server_session_ended", "socket_eof", "socket_reset",
                                        "socket_broken_pipe", "socket_other", "native_idle_timeout"))
CONNECTION_FAILURE_OPERATIONS = frozenset(("send", "receive", "connect", "unknown"))


def socket_failure_reason(error):
    if (isinstance(error, ConnectionResetError) or error.errno == errno.ECONNRESET
            or getattr(error, "winerror", None) == 10054):
        return "socket_reset"
    if isinstance(error, BrokenPipeError) or error.errno == errno.EPIPE:
        return "socket_broken_pipe"
    return "socket_other"


def transport_failure(error, reason, operation):
    """Attach fixed local diagnostic values without changing exception behavior."""
    error.collector_connection_reason = reason
    error.collector_connection_operation = operation
    return error


class CollectorConnection:
    def __init__(self, sock, masks, cipher, expected_login, *, deadline=None,
                 clock=time.monotonic, sleeper=time.sleep,
                 batch_request_gap_seconds=BATCH_REQUEST_GAP_SECONDS,
                 session_seconds=SESSION_SECONDS, vehicle_fields=0x0c, vehicle_batch_size=50,
                 vehicle_zero_recovery=False, vehicle_partial_results=False, native_keepalive=False):
        if type(native_keepalive) is not bool:
            raise ValueError("Invalid native keepalive mode")
        if type(vehicle_fields) is not int or vehicle_fields not in (4, 0x0c):
            raise ValueError("Invalid public vehicle fields")
        if type(vehicle_batch_size) is not int or not 1 <= vehicle_batch_size <= 50:
            raise ValueError("Invalid public vehicle batch size")
        if type(vehicle_zero_recovery) is not bool:
            raise ValueError("Invalid public vehicle zero recovery mode")
        if type(vehicle_partial_results) is not bool or vehicle_partial_results and not vehicle_zero_recovery:
            raise ValueError("Invalid public vehicle partial results mode")
        self.vehicle_fields = vehicle_fields
        self.vehicle_batch_size = vehicle_batch_size
        self.vehicle_zero_recovery = vehicle_zero_recovery
        self.vehicle_partial_results = vehicle_partial_results
        self.native_keepalive, self.keepalive_stage = native_keepalive, 0
        self.socket, self.clock, self.sleeper = sock, clock, sleeper
        self.batch_request_gap_seconds = positive_duration(batch_request_gap_seconds)
        self.session_seconds = positive_duration(session_seconds)
        self.request_limits = dict(LIMITS)
        # Longer sessions retain the existing bounded ACK allowance per hour.
        # Dispatch still sends an ACK only in response to an actual 65530.
        self.request_limits[65531] = max(LIMITS[65531],
                                        math.ceil(self.session_seconds / SESSION_SECONDS * LIMITS[65531]))
        self.request_limits[65530] = (max(LIMITS[65530],
                                         math.ceil(self.session_seconds / SESSION_SECONDS * LIMITS[65530]))
                                      if native_keepalive else 0)
        self.encoder = TransportEncoder(masks["c2g"], cipher.encrypt)
        self.decoder = TransportDecoder(masks["g2c"], cipher.decrypt)
        self.messages = deque()
        self.sent, self.received = Counter(), Counter()
        self.successfully_sent = Counter()
        self.lookup_identities_attempted, self.lookup_identities_sent = Counter(), Counter()
        self.vehicle_recovery_batches = self.vehicle_recovery_identities = 0
        self.expected_login, self.selected_player = expected_login, None
        self.base_notification = None
        self.name_masks, self.names, self.name_failures = {}, {}, set()
        self.fallback_sent, self.fallback_failures = set(), set()
        self.fallback_successes = set()
        self.fallback_status_counts = Counter()
        self.primary_name_timeouts = 0
        self.vehicle_masks, self.vehicles, self.vehicle_failures = {}, {}, set()
        self.vehicle_failed_masks = {}
        self.vehicle_zero_hints = set()
        self.vehicle_pending = set()
        self.vehicle_unresolved, self.vehicle_retired_pending = set(), set()
        self.achievements = None
        self.incoming_bytes = 0
        self.started, self.last_request_at = clock(), None
        self.last_receive_at = self.last_successful_send_at = None
        self.last_heartbeat_receive_at = self.last_heartbeat_ack_at = None
        self.heartbeat_acks_sent = 0
        self.idle_pings_sent, self.last_idle_ping_at = 0, None
        self.ranking_rows_fetched = self.ranking_rows_completed = 0
        self.collection_deadline = deadline
        self.read_phase = "connect"

    def failure_diagnostics(self, error=None):
        """Aggregate session diagnostics; error categories are added only on failure."""
        current = self.clock()
        def age(previous):
            return None if previous is None else round(max(0.0, current - previous), 3)
        result = {"sessionElapsedSeconds": age(self.started),
                  "lastReceiveAgeSeconds": age(self.last_receive_at),
                  "lastSuccessfulSendAgeSeconds": age(self.last_successful_send_at),
                  "heartbeatsReceived": self.received[65530],
                  "heartbeatAcksSent": self.heartbeat_acks_sent,
                  "lastHeartbeatReceiveAgeSeconds": age(self.last_heartbeat_receive_at),
                  "lastHeartbeatAckAgeSeconds": age(self.last_heartbeat_ack_at),
                  "idlePingsSent": self.idle_pings_sent,
                  "lastIdlePingAgeSeconds": age(self.last_idle_ping_at),
                  "attemptRowsFetched": self.ranking_rows_fetched,
                  "attemptRowsCompleted": self.ranking_rows_completed,
                  "requestAttempts": {str(opcode): self.sent[opcode] for opcode in LIMITS},
                  "requestsSent": {str(opcode): self.successfully_sent[opcode] for opcode in LIMITS},
                  "lookupIdentitiesAttempted": {str(opcode): self.lookup_identities_attempted[opcode]
                                                for opcode in (15, 16, 20)},
                  "lookupIdentitiesSent": {str(opcode): self.lookup_identities_sent[opcode]
                                           for opcode in (15, 16, 20)},
                  "primaryNameTimeouts": self.primary_name_timeouts,
                  "vehicleRecoveryBatches": self.vehicle_recovery_batches,
                  "vehicleRecoveryIdentities": self.vehicle_recovery_identities}
        reason = getattr(error, "collector_connection_reason", None)
        if isinstance(reason, str) and reason in CONNECTION_FAILURE_REASONS:
            result["transportFailure"] = reason
        elif isinstance(error, OSError) and not isinstance(error, TimeoutError):
            result["transportFailure"] = socket_failure_reason(error)
        operation = getattr(error, "collector_connection_operation", "unknown")
        if ("transportFailure" in result and isinstance(operation, str)
                and operation in CONNECTION_FAILURE_OPERATIONS):
            result["transportOperation"] = operation
        return result

    def check_deadlines(self):
        remaining_seconds(self.collection_deadline, self.clock)
        if self.clock() - self.started >= self.session_seconds:
            raise TimeoutError("Collector session deadline")

    def _service_keepalive(self, *, reply_deadline=None):
        """Advance at most one native idle stage; required deadlines always win."""
        self.check_deadlines()
        current = self.clock()
        if reply_deadline is not None and current >= reply_deadline:
            raise TimeoutError("Expected reply missing")
        if not self.native_keepalive:
            return
        if self.keepalive_stage == len(NATIVE_IDLE_SECONDS):
            raise transport_failure(ConnectionError("Native receive idle limit"),
                                    "native_idle_timeout", "receive")
        received_at = self.started if self.last_receive_at is None else self.last_receive_at
        if current - received_at < NATIVE_IDLE_SECONDS[self.keepalive_stage]:
            return
        self.keepalive_stage += 1
        if self.keepalive_stage == len(NATIVE_IDLE_SECONDS):
            raise transport_failure(ConnectionError("Native receive idle limit"),
                                    "native_idle_timeout", "receive")
        self.send(65530)

    def send(self, opcode, payload=b""):
        if opcode not in self.request_limits or self.sent[opcode] >= self.request_limits[opcode]:
            raise ValueError("Request outside read-only collector allowlist")
        validate_outgoing(opcode, payload, expected_login=self.expected_login,
                          selected_player=self.selected_player, requested_names=self.name_masks,
                          fallback_sent=self.fallback_sent,
                          requested_vehicles={p: self.vehicle_fields & ~self.vehicle_masks.get(p, 0)
                                              for p in self.vehicle_pending})
        self.check_deadlines()
        if opcode == 16:
            self.fallback_sent.add(struct.unpack("<I", payload)[0])
        if opcode in (137, 15, 16, 20):
            # One shared clock covers all data reads, including the first
            # nickname of a later call. Heartbeat traffic remains immediate.
            delay = (0.0 if self.last_request_at is None else
                     self.batch_request_gap_seconds - (self.clock() - self.last_request_at))
            if delay > 0:
                session_remaining = self.session_seconds - (self.clock() - self.started)
                delay = max(0.0, min(delay, session_remaining))
                remaining = remaining_seconds(self.collection_deadline, self.clock)
                self.sleeper(min(delay, remaining) if remaining is not None else delay)
            self.check_deadlines()
            self.last_request_at = self.clock()
        self.sent[opcode] += 1
        lookup_count = 1 if opcode == 16 else payload[0] if opcode in (15, 20) else 0
        if lookup_count:
            self.lookup_identities_attempted[opcode] += lookup_count
        wire = self.encoder.encode(opcode, payload)
        try:
            self.socket.sendall(wire)
        except OSError as error:
            raise transport_failure(error, socket_failure_reason(error), "send")
        self.last_successful_send_at = self.clock()
        self.successfully_sent[opcode] += 1
        if lookup_count:
            self.lookup_identities_sent[opcode] += lookup_count
        if opcode == 65531:
            self.heartbeat_acks_sent += 1
            self.last_heartbeat_ack_at = self.last_successful_send_at
        elif opcode == 65530:
            self.idle_pings_sent += 1
            self.last_idle_ping_at = self.last_successful_send_at

    def observe(self, opcode, payload):
        if opcode == 434 and self.achievements is not None:
            r = Reader(payload)
            for _ in range(r.u8()):
                key, value = (r.u32(), r.u32()), r.u32()
                if key in ((2, 1), (621080, 1)):
                    self.achievements[key] = value
            r.finish()
        elif opcode == 380:
            for player, mask, name in social_records(payload):
                if player in self.name_masks:
                    self.name_masks[player] |= mask & 5
                    if mask & 4 and name:
                        self.names[player] = name
        elif opcode == 14:
            r = Reader(payload)
            failed = r.vector()
            r.finish()
            self.name_failures.update(player for player in failed if player in self.name_masks)
        elif opcode == 19:
            correlated = self.vehicle_pending | self.vehicle_retired_pending
            for player, mask, vehicles in detail_records(payload):
                if player not in correlated:
                    raise ValueError("Uncorrelated public vehicle reply")
                if mask & ~self.vehicle_fields:
                    raise ValueError("Unrequested public vehicle fields")
                if not mask:
                    # Native19 can carry a descriptor with no field updates.
                    # An optional bounded recovery uses it only as a keyed hint;
                    # it never establishes field completion or availability.
                    if self.vehicle_zero_recovery and self.vehicle_masks[player] != self.vehicle_fields:
                        self.vehicle_zero_hints.add(player)
                    continue
                if self.vehicle_masks[player] & mask:
                    raise ValueError("Repeated public vehicle reply")
                self.vehicle_masks[player] |= mask
                self.vehicle_failed_masks[player] = self.vehicle_failed_masks.get(player, 0) & ~mask
                if mask & 4:
                    self.vehicles[player] = vehicles
                if player in self.vehicle_retired_pending:
                    self._refresh_retired_vehicle(player)
        elif opcode == 18:
            r = Reader(payload)
            failed = r.vector()
            r.finish()
            if (len(set(failed)) != len(failed) or any((p not in self.vehicle_pending
                    and p not in self.vehicle_retired_pending)
                    or self.vehicle_failed_masks.get(p, 0) or self.vehicle_masks[p] == self.vehicle_fields
                    for p in failed)):
                raise ValueError("Uncorrelated public vehicle failure")
            for player in failed:
                self.vehicle_failed_masks[player] = self.vehicle_fields & ~self.vehicle_masks[player]
                if player in self.vehicle_retired_pending:
                    self._refresh_retired_vehicle(player)

    def _refresh_retired_vehicle(self, player):
        """Project actual capsules independently from unknown or failed tactics."""
        self.vehicle_unresolved.discard(player)
        self.vehicle_failures.discard(player)
        if not self.vehicle_masks[player] & 4:
            if self.vehicle_failed_masks[player] & 4:
                self.vehicle_failures.add(player)
            elif player in self.vehicle_zero_hints:
                self.vehicle_unresolved.add(player)
            else:
                raise ValueError("Incomplete public vehicle observation")
        if self.vehicle_masks[player] == self.vehicle_fields:
            self.vehicle_retired_pending.discard(player)
            self.vehicle_zero_hints.discard(player)
        else:
            # Keep the known request context for late disjoint keyed updates.
            self.vehicle_retired_pending.add(player)

    def finish_vehicles(self):
        """Validate decoded late replies before projection; never poll or retry."""
        self.read_phase = "public_vehicles"
        self.check_deadlines()
        if self.vehicle_pending:
            raise ValueError("Active public vehicle transaction")
        self._drain_queued_messages()

    def _dispatch_message(self, opcode, payload, target=None):
        self.received[opcode] += 1
        # Heartbeat counts/ages describe normal dispatch, not queued wire arrival.
        # last_receive_at separately records any positive socket read.
        if opcode == 65530:
            self.last_heartbeat_receive_at = self.clock()
        if sum(self.received.values()) > MAX_ROWS * 2 + 10_000:
            raise ValueError("Incoming message limit")
        self.observe(opcode, payload)
        if opcode == 362 and target != 362:
            if self.base_notification is not None:
                raise ValueError("Duplicate base notification")
            self.base_notification = payload
        if opcode == 65530:
            self.send(65531)
        elif opcode == 317:
            raise transport_failure(ConnectionError("Session ended"), "server_session_ended", "receive")
        elif opcode in (131, 17) and opcode != target:
            raise ValueError("Uncorrelated read reply")
        elif opcode == target:
            return True
        return False

    def _drain_queued_messages(self):
        # Consume only decoded messages already in memory; never poll the socket.
        while self.messages:
            self.check_deadlines()
            self._dispatch_message(*self.messages.popleft())

    def receive(self, target=None, until=None, timeout=REPLY_SECONDS):
        self.check_deadlines()
        if target == 362 and self.base_notification is not None:
            payload, self.base_notification = self.base_notification, None
            return payload
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            self.check_deadlines()
            if until is not None and until():
                return b""
            if self.messages:
                opcode, payload = self.messages.popleft()
                if self._dispatch_message(opcode, payload, target):
                    return payload
                continue
            try:
                data = self.socket.recv(65536)
            except socket.timeout:
                # Read bytes first, as the native routine does. A ready fragment
                # resets inactivity; only a timed-out read services the timer.
                self._service_keepalive(reply_deadline=deadline)
                continue
            except OSError as error:
                raise transport_failure(error, socket_failure_reason(error), "receive")
            if not data:
                raise transport_failure(ConnectionError("Connection closed"), "socket_eof", "receive")
            self.last_receive_at = self.clock()
            self.keepalive_stage = 0
            self.incoming_bytes += len(data)
            if self.incoming_bytes > 128 * 1024 * 1024:
                raise ValueError("Incoming byte limit")
            self.messages.extend((opcode, payload) for _, opcode, payload in self.decoder.feed(data))
        self.check_deadlines()
        raise TimeoutError("Expected reply missing")

    def fetch_rank_page(self, offset, count=200):
        self.read_phase = "rankings"
        self.send(137, struct.pack("<HIB", 5, offset, count))
        status, rows = ranking_records(self.receive(131), count)
        require_zero("ranking", status)
        return rows

    def name_resolved(self, player):
        return bool(self.name_masks.get(player, 0) & 4) and bool(self.names.get(player))

    def fetch_names(self, players):
        if (not 1 <= len(players) <= 200
                or any(type(player) is not int or not 0 < player < 2**32 for player in players)
                or len(set(players)) != len(players)):
            raise ValueError("Invalid primary name cohort")
        # The native wire ceiling is 200, not a demonstrated safe workload.
        # Limit each request to 50 identities without changing rank cohorts.
        for offset in range(0, len(players), PRIMARY_NAME_BATCH_SIZE):
            self._fetch_name_batch(players[offset:offset + PRIMARY_NAME_BATCH_SIZE])

    def _fetch_name_batch(self, players):
        self.read_phase = "primary_names"
        for player in players:
            self.name_masks.setdefault(player, 0)
        self.name_failures.difference_update(players)
        self.send(15, bytes([len(players)]) + b"".join(struct.pack("<IB", player, 5) for player in players))
        try:
            # Mask4 completes the primary field even when its name is empty.
            # Only a nonempty name resolves it; empty fields enter fallback now.
            self.receive(until=lambda: all(self.name_masks[p] & 4 or p in self.name_failures for p in players),
                         timeout=PRIMARY_NAME_SECONDS)
        except CollectionDeadline:
            raise  # The whole-run budget never permits a recovery read.
        except TimeoutError:
            # Primary 380/14 are keyed. A bounded, separate native 16/17 batch
            # may recover names while observing late primary packets. No 15
            # retry, transport/layout recovery or repeated fallback is allowed.
            self.primary_name_timeouts += 1
            if self.clock() - self.started >= self.session_seconds:
                raise
        missing = [p for p in players if not self.name_resolved(p) and p not in self.fallback_sent]
        if missing:
            self.fetch_nickname_fallback(missing, skip_resolved=True)

    def fetch_nickname_fallback(self, players, *, skip_resolved=False):
        self.read_phase = "nickname_fallback"
        if (not 1 <= len(players) <= 200 or len(set(players)) != len(players)
                or any(player in self.fallback_sent for player in players)):
            raise ValueError("Invalid nickname fallback batch")
        for offset in range(0, len(players), NICKNAME_BATCH_SIZE):
            if skip_resolved:
                # A late keyed primary reply may resolve a later name while
                # earlier sequential fallbacks are being drained. Vehicle
                # warm-up retains its separate explicit lookup semantics.
                self._drain_queued_messages()
                if self.name_resolved(players[offset]):
                    continue
            # Exactly one lookup is outstanding. send() enforces the same
            # start-to-start gap across batches, callers and other read types.
            self._fetch_nickname_batch(players[offset:offset + NICKNAME_BATCH_SIZE])

    def _fetch_nickname_batch(self, players):
        if len(players) != NICKNAME_BATCH_SIZE:
            raise ValueError("Nickname lookups must be sequential")
        expected, successful, failures = set(players), set(), 0
        for player in players:
            self.send(16, struct.pack("<I", player))
        for _ in players:
            r = Reader(self.receive(17))
            status = r.u16()
            if status:
                r.finish()
                failures += 1
                self.fallback_status_counts[status] += 1
                continue
            player, name = r.u32(), r.string(48)
            r.u8()
            r.finish()
            if player not in expected or player in successful:
                raise ValueError("Uncorrelated nickname fallback")
            successful.add(player)
            if name:
                self.names[player] = name
            self.name_masks[player] |= 4
        # 17 failure carries no identity: only a completely drained fixed batch
        # permits assigning remaining identities by elimination.
        unresolved = expected - successful
        if len(unresolved) != failures:
            raise ValueError("Nickname fallback response count mismatch")
        self.fallback_failures.update(unresolved)
        self.fallback_successes.update(successful)

    def fetch_vehicles(self, players):
        """Load selected public fields in batches of at most 50 with bounded warm-up."""
        self.read_phase = "public_vehicles"
        if (not players or len(players) > 200 or len(set(players)) != len(players)
                or any(not p or p in self.vehicle_masks for p in players) or self.vehicle_pending):
            raise ValueError("Invalid or repeated public vehicle transaction")
        for player in players:
            self.name_masks.setdefault(player, 0)  # Native18 may need a keyed16 warm-up.
        for offset in range(0, len(players), self.vehicle_batch_size):
            batch = players[offset:offset + self.vehicle_batch_size]
            self.vehicle_masks.update((p, 0) for p in batch)
            self.vehicle_failed_masks.update((p, 0) for p in batch)
            self._query_vehicles(batch, allow_zero_hints=self.vehicle_zero_recovery)
            if self.vehicle_zero_recovery:
                self._drain_queued_messages()
            # Native18 invokes16. A successful17 calls clearIDQueryTransaction
            # (0x007f8a98), which reissues still-pending public detail masks.
            # Name lookup already performed that same warm-up for many players;
            # never repeat16 for an identity or retry a timeout/malformed reply.
            candidates = [p for p in batch if self.vehicle_masks[p] != self.vehicle_fields
                          and (self.vehicle_failed_masks[p]
                               or self.vehicle_zero_recovery and p in self.vehicle_zero_hints)]
            warm = [p for p in candidates if p not in self.fallback_sent]
            if warm:
                self.fetch_nickname_fallback(warm)
            self.read_phase = "public_vehicles"
            if self.vehicle_zero_recovery:
                self._drain_queued_messages()
            eligible = self.fallback_successes if self.vehicle_zero_recovery else set(warm) - self.fallback_failures
            retry = [p for p in candidates if p in eligible and self.vehicle_masks[p] != self.vehicle_fields]
            if retry:
                # Each identity gets at most one extra20, shared by18/zero
                # recovery. A zero on this requery cannot trigger a third20.
                self.vehicle_recovery_batches += 1
                self.vehicle_recovery_identities += len(retry)
                self._query_vehicles(retry, allow_zero_hints=self.vehicle_partial_results)
            if self.vehicle_zero_recovery:
                # Failed/missing warm-up does not turn a zero hint into a miss.
                # Strict mode waits for actual fields or18. Opt-in observation
                # mode also accepts current keyedzero as unresolved information.
                self._drain_queued_messages()
                self.receive(until=lambda: self._vehicle_batch_complete(
                    batch, allow_zero_hints=self.vehicle_partial_results))
                self._drain_queued_messages()
            # A terminal tactics miss does not erase actual capsule observations.
            # The public projection reports only capsule-field availability.
            if self.vehicle_partial_results:
                for player in batch:
                    self._refresh_retired_vehicle(player)
            else:
                self.vehicle_failures.update(p for p in batch if not self.vehicle_masks[p] & 4)
            self.vehicle_pending.clear()

    def _vehicle_batch_complete(self, players, *, allow_zero_hints=False):
        return all((self.vehicle_masks[p] | self.vehicle_failed_masks[p]) == self.vehicle_fields
                   or allow_zero_hints and p in self.vehicle_zero_hints for p in players)

    def _query_vehicles(self, players, *, allow_zero_hints=False):
        if self.vehicle_zero_recovery:
            self.vehicle_pending.update(players)
        else:
            self.vehicle_pending = set(players)
        self.vehicle_zero_hints.difference_update(players)
        missing = {p: self.vehicle_fields & ~self.vehicle_masks[p] for p in players}
        for player, mask in missing.items():
            self.vehicle_failed_masks[player] &= ~mask
        self.send(20, bytes([len(players)]) + b"".join(struct.pack("<IB", p, missing[p]) for p in players))
        self.receive(until=lambda: self._vehicle_batch_complete(players, allow_zero_hints=allow_zero_hints))
        # Keep correlation while native16/17 warm-up can deliver late fields.
