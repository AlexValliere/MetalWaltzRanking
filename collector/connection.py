"""A single read-only native session with bounded, sequential transactions.

Adapted from the live-verified MaidenWaltz collector experiments. No account or
commander creation, battle, reward, tutorial, purchase or claim commands exist.
Unkeyed ranking/17 replies are drained before a new transaction is started.
"""

from collections import Counter, deque
import socket
import struct
import time

from .binary import Reader
from .records import ranking_records, require_zero, social_records, validate_outgoing
from .transport import TransportDecoder, TransportEncoder

MAX_ROWS = 1_000_000
LIMITS = {324: 1, 396: 1, 316: 1, 357: 1, 374: 1, 436: 1,
          137: 10_000, 15: 10_000, 16: MAX_ROWS + 200, 65531: 1000}
REPLY_SECONDS = 12
SESSION_SECONDS = 3600


class CollectorConnection:
    def __init__(self, sock, masks, cipher, expected_login, *, clock=time.monotonic, sleeper=time.sleep):
        self.socket, self.clock, self.sleeper = sock, clock, sleeper
        self.encoder = TransportEncoder(masks["c2g"], cipher.encrypt)
        self.decoder = TransportDecoder(masks["g2c"], cipher.decrypt)
        self.messages = deque()
        self.sent, self.received = Counter(), Counter()
        self.expected_login, self.selected_player = expected_login, None
        self.base_notification = None
        self.name_masks, self.names, self.name_failures = {}, {}, set()
        self.fallback_sent, self.fallback_failures = set(), set()
        self.fallback_status_counts = Counter()
        self.primary_name_timeouts = 0
        self.achievements = None
        self.incoming_bytes = 0
        self.started, self.last_request_at = clock(), 0.0

    def send(self, opcode, payload=b""):
        if opcode not in LIMITS or self.sent[opcode] >= LIMITS[opcode]:
            raise ValueError("Request outside read-only collector allowlist")
        validate_outgoing(opcode, payload, expected_login=self.expected_login,
                          selected_player=self.selected_player, requested_names=self.name_masks,
                          fallback_sent=self.fallback_sent)
        if self.clock() - self.started >= SESSION_SECONDS:
            raise TimeoutError("Collector session deadline")
        if opcode == 16:
            self.fallback_sent.add(struct.unpack("<I", payload)[0])
        if opcode in (137, 15):
            delay = 0.10 - (self.clock() - self.last_request_at)
            if delay > 0:
                self.sleeper(delay)
            self.last_request_at = self.clock()
        self.sent[opcode] += 1
        self.socket.sendall(self.encoder.encode(opcode, payload))

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

    def receive(self, target=None, until=None, timeout=REPLY_SECONDS):
        if target == 362 and self.base_notification is not None:
            payload, self.base_notification = self.base_notification, None
            return payload
        deadline = self.clock() + timeout
        while self.clock() < deadline and self.clock() - self.started < SESSION_SECONDS:
            if until is not None and until():
                return b""
            if self.messages:
                opcode, payload = self.messages.popleft()
                self.received[opcode] += 1
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
                    raise ConnectionError("Session ended")
                elif opcode in (131, 17) and opcode != target:
                    raise ValueError("Uncorrelated read reply")
                elif opcode == target:
                    return payload
                continue
            try:
                data = self.socket.recv(65536)
            except socket.timeout:
                continue
            if not data:
                raise ConnectionError("Connection closed")
            self.incoming_bytes += len(data)
            if self.incoming_bytes > 128 * 1024 * 1024:
                raise ValueError("Incoming byte limit")
            self.messages.extend((opcode, payload) for _, opcode, payload in self.decoder.feed(data))
        raise TimeoutError("Expected reply missing")

    def fetch_rank_page(self, offset, count=200):
        self.send(137, struct.pack("<HIB", 5, offset, count))
        status, rows = ranking_records(self.receive(131), count)
        require_zero("ranking", status)
        return rows

    def name_resolved(self, player):
        return bool(self.name_masks.get(player, 0) & 4) and bool(self.names.get(player))

    def fetch_names(self, players):
        for player in players:
            self.name_masks.setdefault(player, 0)
        self.name_failures.difference_update(players)
        self.send(15, bytes([len(players)]) + b"".join(struct.pack("<IB", player, 5) for player in players))
        try:
            self.receive(until=lambda: all(self.name_resolved(p) or p in self.name_failures for p in players))
        except TimeoutError:
            # Primary 380/14 are keyed. A bounded, separate native 16/17 batch
            # may recover names while observing late primary packets. No 15
            # retry, transport/layout recovery or repeated fallback is allowed.
            self.primary_name_timeouts += 1
            if self.clock() - self.started >= SESSION_SECONDS:
                raise
        missing = [p for p in players if not self.name_resolved(p) and p not in self.fallback_sent]
        if missing:
            self.fetch_nickname_fallback(missing)

    def fetch_nickname_fallback(self, players):
        if not 1 <= len(players) <= 200 or len(set(players)) != len(players):
            raise ValueError("Invalid nickname fallback batch")
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
