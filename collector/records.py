"""Reviewed native records, adapted from MaidenWaltz ranking research.

Sources: bridge/maiden_waltz/{independent,rankings}.py and the verified
local-data/collector_{eu_probe,all_servers}.py experiments. IDs are temporary
correlation keys and must never be serialized into public output or logs.
"""

import hashlib
import struct

from .binary import Reader


class ServerRejection(Exception):
    def __init__(self, phase, status):
        self.phase, self.status = phase, status
        super().__init__("Server rejected a read")


def require_zero(phase, status):
    if status:
        raise ServerRejection(phase, status)


def login_payload(username, password):
    def encode(value, maximum):
        if not isinstance(value, str) or not value or any(c in value for c in "\0\r\n"):
            raise ValueError("Invalid credentials")
        data = value.encode("utf-8", errors="strict")
        if len(data) > maximum:
            raise ValueError("Invalid credentials")
        return data
    account, secret = encode(username, 128), encode(password, 512)
    digest = hashlib.md5(secret, usedforsecurity=False).hexdigest().encode("ascii")
    return b"\0" + account + b"\0" + digest + b"\0\0"


def login_reply(payload):
    r = Reader(payload)
    status, account = r.u16(), None
    if status == 0:
        account = r.u32()
        r.string(128)
        r.u8()
        r.u32()
        if not account:
            raise ValueError("Invalid authenticated identity")
    r.finish()
    return status, account


def player_list(payload):
    r = Reader(payload)
    status, players = r.u16(), []
    if status == 0:
        for _ in range(r.u8()):
            identity, name, selector, extra = r.u32(), r.string(48), r.u8(), r.u32()
            if not identity or any(item[0] == identity for item in players):
                raise ValueError("Invalid commander identity")
            players.append((identity, name, selector, extra))
    r.finish()
    return status, players


def ranking_records(payload, requested):
    r = Reader(payload)
    status, rows = r.u16(), []
    if status == 0:
        count = r.u8()
        if count > requested:
            raise ValueError("Ranking count exceeded request")
        rows = [(r.u32(), r.read("i")) for _ in range(count)]
        r.take(4)  # Native client leaves this unused; not a population count.
        if not all(player for player, _ in rows) or len({p for p, _ in rows}) != len(rows):
            raise ValueError("Invalid ranking identities")
    r.finish()
    return status, rows


def social_records(payload):
    # PlayerSocialInfoDesc native reader 0x007fd81c; handler 0x007f7df8.
    r, rows = Reader(payload), []
    for _ in range(r.u8()):
        player, mask, name = r.u32(), r.u8(), None
        if mask & 0x18:
            raise ValueError("Unsupported social record fields")
        if mask & 1:
            r.u8()
        if mask & 2:
            r.u8()
        if mask & 4:
            name = r.string(48)
            r.u8()
        if mask & 0x20:
            r.u32()
        if mask & 0x40:
            r.u16()
        if mask & 0x80:
            r.u32()
        rows.append((player, mask, name))
    r.finish()
    return rows


def detail_records(payload):
    """Native public capsules/tactics, not an owned roster or combat formation.

    PlayerDetailInfoDesc 0x007fdb5c, capsule reader 0x007fde80 and tactics
    reader 0x007fe2e0. The native ranking inspection asks mask 0x0c. Generation
    is already the public phase; carried faculty levels need no XP conversion.
    Tactics are consumed for framing only and never interpreted as squad slots.
    """
    r, rows, seen_masks = Reader(payload), [], {}
    for _ in range(r.u8()):
        player, mask, vehicles = r.u32(), r.u8(), None
        if not player or mask & ~0x0c or seen_masks.get(player, 0) & mask:
            raise ValueError("Invalid public vehicle descriptor")
        # Native19 dispatches each descriptor by its mask. Capsule and tactics
        # fields for one identity may share a packet, as well as span packets.
        seen_masks[player] = seen_masks.get(player, 0) | mask
        if mask & 4:
            vehicles, types = [], set()
            for _ in range(r.u8()):
                capsule, phase, faculties = r.u32(), r.u8(), {}
                if not capsule or capsule in types:
                    raise ValueError("Duplicate or invalid public vehicle type")
                types.add(capsule)
                for _ in range(r.u8()):
                    category, trend, level = r.u8(), r.u16(), r.read("i")
                    if category in faculties:
                        raise ValueError("Duplicate public vehicle faculty")
                    faculties[category] = level
                level = faculties.get(1)
                vehicles.append({"capsuleTypeId": capsule, "phase": phase,
                                 "level": level if level is not None and level >= 0 else None,
                                 "fate": faculties.get(20) == 1})
        if mask & 8:
            for _ in range(r.u8()):
                r.u32()
                r.u32()
        rows.append((player, mask, vehicles))
    r.finish()
    return rows


def validate_outgoing(opcode, payload, *, expected_login, selected_player, requested_names, fallback_sent,
                      requested_vehicles=()):
    """Narrow the opcode allowlist to verified, current-session read payloads."""
    if not isinstance(payload, bytes):
        raise ValueError("Invalid outgoing payload")
    if opcode in (396, 357, 374, 436, 65531):
        if payload:
            raise ValueError("Unexpected nonempty read")
    elif opcode == 324:
        if payload != expected_login:
            raise ValueError("Unexpected login request")
    elif opcode == 316:
        if not selected_player or payload != struct.pack("<I", selected_player):
            raise ValueError("Unexpected commander selection")
    elif opcode == 137:
        fund, offset, count = struct.unpack("<HIB", payload)
        if fund != 5 or not 0 <= offset <= 1_000_000 or not 1 <= count <= 200:
            raise ValueError("Invalid ranking range")
    elif opcode == 15:
        r = Reader(payload)
        count, seen = r.u8(), set()
        if not 1 <= count <= 200:
            raise ValueError("Invalid name batch")
        for _ in range(count):
            player, mask = r.u32(), r.u8()
            if player not in requested_names or player in seen or mask != 5:
                raise ValueError("Unexpected name lookup")
            seen.add(player)
        r.finish()
    elif opcode == 16:
        player, = struct.unpack("<I", payload)
        if player not in requested_names or player in fallback_sent:
            raise ValueError("Unexpected or repeated nickname fallback")
    elif opcode == 20:
        r = Reader(payload)
        count, seen = r.u8(), set()
        # The native public detail sender chunks at 50 (0x007f8ed0).
        if not 1 <= count <= 50:
            raise ValueError("Invalid public vehicle batch")
        for _ in range(count):
            player, mask = r.u32(), r.u8()
            if (player not in requested_vehicles or player in seen or mask not in (4, 8, 0x0c)
                    or mask != requested_vehicles[player]):
                raise ValueError("Unexpected public vehicle lookup")
            seen.add(player)
        r.finish()
    else:
        raise ValueError("Request outside read-only collector allowlist")
