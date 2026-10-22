# Collector protocol and public data

This collector adapts the verified Metal Waltz research from Maiden Waltz. It is
a small, read-only protocol client for existing dedicated commanders. Research
registration and commander-creation helpers are deliberately absent from this
repository's runtime.

## Provenance and observed scope

The seven-backend live verification on **22 October 2026** reached 152,710
ranking rows and resolved 151,257 names. Every commander had 0 Clash wins and
ranking-unlock flag 0. Six backends resolved every name; Columbus returned native
nickname failure status 48 for 1,453 entries. The localized error text does not
establish the cause of those failures.

The Android source data was pinned at commit
`fee198aee089d57ab4184f4ab0cad5f3ccf67e35`. The inspected ARM64 `libasgame.so`
SHA-256 is `fa43e196953cd594e54a01a2e315311618966b82744dc29e086ea0c3fed1bd43`.
The [native evidence](research/native-evidence.json) records additional source and
function fingerprints; [live verification](research/verification-2026-10-22.json)
records aggregate checks and public export hashes.

| Backend key   | Server                                                        |
| ------------- | ------------------------------------------------------------- |
| `Bonn`        | Europe №1                                                     |
| `YaleAndroid` | Harrisburg / Android                                          |
| `YaleIOS`     | Columbus / iOS                                                |
| `JP`          | Japan №1                                                      |
| `KOREA`       | Korea                                                         |
| `JKMIX`       | Shared Japanese/Korean №2; `MIXKJ` is the other visible alias |
| `BANGKOK`     | Thailand                                                      |

Europe registration was observed to work for the US and Thai logins. Japan
registration made the same credentials usable on Korea and the shared №2
backend. This describes observed credential acceptance and does not establish
the service's internal account architecture. The runtime uses one configured
credential pair and reviewed endpoints, and refuses missing or ambiguous
commander lists.

## Connection and requests

The portable implementation reproduces the verified DES compatibility transform
and stateful framing in Python. It keeps independent send/receive cursors,
preserves frame boundaries, and closes the connection after malformed data or an
uncertain unkeyed reply. Unsupported incoming frames are consumed without a
speculative response. Incoming heartbeat 65530 receives empty 65531.

| Request / reply      | Purpose                                                               |
| -------------------- | --------------------------------------------------------------------- |
| 324 / 331            | Login using the configured collector credentials                      |
| 396 / 310            | Read the existing commander list                                      |
| 316 / 312            | Enter the sole existing commander; verify acknowledged identifiers    |
| 357 / 368, 374 / 371 | Enter town using the verified handshake                               |
| 436 / 434, 438       | Read achievements through its completion marker                       |
| 137 / 131            | Enumerate Grandeur, fund 5, using explicit offset and count           |
| 15 / 380, 14         | Request public social names, mask 5; 14 denotes primary lookup misses |
| 16 / 17              | Native nickname fallback for unresolved public names                  |

Account registration 141 and commander creation 313 are not allowed outgoing
messages. No battles, purchases, tutorial advancement, claims or automatic
rewards are implemented. Names, account IDs and session IDs are not taken from
local saved game logins. Request limits, row limits, time limits and a strict
outgoing-message allowlist bound the session.

Ranking request 137 contains `u16 fund`, `u32 offset`, `u8 count` with native
count limit 200. Reply 131 contains `u16 status`, then `u8 count` and that many
`u32 playerID, i32 grandeur` pairs on success. Its four trailing bytes are left
unclassified. They are not used as a population count. Replies carry no request
offset, so requests are sequential and a ranking timeout ends the connection.

Enumeration advances by the actual returned count and continues after short
pages until a successful empty reply. It rejects duplicate identifiers,
non-descending scores and an inconsistent overlap sample at offset 199/count 2.
Only a completed enumeration can replace an exported snapshot. Rankings may move
during the scan; a complete sequential scan is not an atomic server snapshot or
proof that every registered commander appears.

## Name fallback

Public name request 15 contains up to 200 `u32 playerID, u8 mask` records. Reply
380 can split requested masks across records. A nonempty UTF-8 name from mask
bit 4 establishes resolution. Online-state bit 1 is not required for publication.
A 14 response is a primary lookup miss, not proof that the name is unavailable.

The native 14 handler at `0x007f8090` calls the nickname lookup path. Its sender at
`0x007fa2f0` sends request 16 with one `u32 playerID`. Reply 17 begins with
`u16 status`; success adds `u32 playerID`, a name C string bounded to 48 bytes,
and one uninterpreted byte. Failure has no player ID. The collector sends a
bounded batch and drains exactly the same number of 17 replies before another
ranking phase. Success identifiers must be expected and unique. Unkeyed failures
can be assigned to remaining requests only after the batch completes. Late 380
responses can still resolve a name. An incomplete/malformed fallback closes the
connection and cannot produce a new partial snapshot.

One bounded recovery is allowed after a primary social-name timeout: identity-
bearing late 380/14 responses remain observable while request 16 is sent at most
once for each unresolved identity. This is not a retry of request 15. Recovery
does not apply to ranking timeouts, expired connections or malformed/incomplete
nickname batches. Explicit completed nickname failures export `name: null` and
`nameStatus: "unavailable"`; names are not inferred from old snapshots or scores.

## Public export contract

`site/data/index.json` has schema `metal-waltz.rankings-index.v1`,
`generatedAtUtc`, and seven `servers` entries. Each entry has:

```text
key, name, aliases, file, rows, namesResolved, namesMissing,
startedAtUtc, completedAtUtc, refreshStatus, sha256
```

An attempted entry may also have `lastAttemptAtUtc`; a failed refresh records a
sanitized `errorCode`. `refreshStatus` is `seed`, `ok` or `failed`. The SHA-256
covers the exact referenced file bytes. A failure preserves the previous good
file and successful timestamps. Without any previous snapshot, `file`/hash/
snapshot times are null and counts are zero.

`<key>.rankings.json` has schema `maiden-waltz.public-ranking-snapshot.v1` and:

```text
serverKey, aliases, serverNames, fund: 5, startedAtUtc, completedAtUtc,
rankingEnumerationComplete: true, namesComplete, scope, rows
```

Each row contains exactly `rank`, `name`, `grandeur` and `nameStatus`. Ranks start
at 1, remain contiguous, and retain server order. A resolved name is a nonempty
string; an unavailable name is null. No protocol player ID, account ID,
credential, session value or raw response is part of this contract.

The publication script rejects unknown fields, path changes, links, hash/count
mismatches, invalid names, incomplete enumerations and unexpected files. It
copies only reviewed static assets and validated public JSON into `_site`.
The browser loads one selected backend snapshot at a time and renders names as
text. It never connects to game servers or receives the collector secrets.
