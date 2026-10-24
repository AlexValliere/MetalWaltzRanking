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
| `SteamJP`     | Steam Asia; `SteamTEST` is its Hong Kong connection route       |
| `SteamBonn`   | Steam Europe                                                  |
| `SteamUSAVA`  | Steam USA                                                     |

The Steam directory and installed `Sherman.exe` were inspected on **23 October
2026**. Its SHA-256 is
`15e063b555565cd35e3b465cdd0a8dc005e26b552b2317c8cd56cd958e9c4724`.
The [Steam client evidence](research/steam-client-2026-10-23.json) records the
public directory, native login/ranking functions and live observations. The
[transport evidence](research/steam-transport-evidence.json) records both
621-byte directional masks, their exact PE locations and native cipher fixtures.
Steam keeps its own profile; mobile masks are never sent to a Steam route.

Steam Asia and Hong Kong returned the same newly created collector commander
and exactly matching first 200 ranking records. They are one collection target.
The alternate gateway is tried only when no connection has been established;
login failures, malformed replies and timeouts never replay a session there.

Bounded checks on 23 October 2026 used a one-second batch gap on all three Steam
backends. Each returned seven initial ranking rows and seven at the next offset,
resolved all seven initial names, and had no primary-name timeouts or nickname
fallbacks. Separate bounded ranking probes estimated 98,516 exposed rows for
Steam Asia, 37,913 for Steam Europe and 10,054 for Steam USA, using 18, 18 and 16
ranking requests respectively. Those bounded probes established estimates, not
completed enumerations or verified complete snapshots. Timings and probe counts are recorded in the
[Steam client evidence](research/steam-client-2026-10-23.json).

A subsequent complete Steam USA scan returned 10,054 ranking rows and resolved
all 10,054 names, with no missing names. It started at
`2026-10-23T21:57:07.588015Z` and completed at `2026-10-23T22:05:57.924638Z`,
taking 530.336623 seconds. Its 1,207,315-byte export has SHA-256
`db2e2748236c16447fb61a95f44edb2b16952ef4ebc7eb659ff8591040e33c8d`.
Independent Python publication validation, Node browser validation and the live
browser preview passed. Every vehicle list remains `not_collected` with null
vehicles.

Steam Europe subsequently completed with 37,913 ranking rows and all 37,913 names
resolved, with none missing. It started at `2026-10-23T22:05:58.073179Z` and
completed at `2026-10-23T22:25:49.113775Z`, taking 1191.040596 seconds. Its
4,606,796-byte export has SHA-256
`66c6f655b6b2be02a0e85e29e8e540f9a3d17e4cdeeb0187bdd761e4081aec12`.
Independent Python publication and Node browser validation passed; every vehicle
list remains `not_collected` with null vehicles.

Steam Asia / Hong Kong completed with 98,516 ranking rows and all 98,516 names
resolved, with none missing. It started at `2026-10-23T22:25:49.743622Z` and
completed at `2026-10-24T00:21:46.314406Z`, taking 6956.570784 seconds. Its
12,050,900-byte export has SHA-256
`5bd0b2739a825ee8a69324d5e28a4cbc6a229a28983f8e9b16fd3a23db5234ff`.
Independent Python publication and Node browser validation passed all three
Steam files against the complete ten-backend index; the actual Asia browser
preview also passed. All 146,483 Steam names are resolved. The archive now has
299,193 rows and 297,740 resolved names across ten complete snapshots captured at
different times. All Steam vehicle lists remain `not_collected` with null
vehicles. The earlier bounded estimates remain separate observations.

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
| 20 / 19, 18          | Read public vehicle/capsule details and consume tactics fields, mask 12 |

Account registration 141 and commander creation 313 are not allowed outgoing
messages. No battles, purchases, tutorial advancement, claims or automatic
rewards are implemented. Names, account IDs and session IDs are not taken from
local saved game logins. Request limits, row limits, time limits and a strict
outgoing-message allowlist bound the session.

On mobile backends, ranking (137), primary-name (15) and public-vehicle (20) batch
requests share a minimum 250 ms start-to-start gap. Mobile ranking batches allow
200 rows; primary-name and public-vehicle batch ceilings remain 200 and 50.
Steam ranking batches request at most 200 new rows, and Steam batch requests
137, 15 and 20 share a minimum one-second start-to-start gap. Steam public-vehicle
collection is currently disabled, so the 20 pacing rule applies only if those
reads are enabled in a separately verified change. Keepalive replies are not
delayed by batch pacing.

The Steam Lua interface displays six ranking rows and checks one extra row to
enable its next-page button. It passes ranges 1–7, 7–13, 13–19 and so on to the
native adapter. The adapter subtracts one from the start and passes the end
unchanged, while the native sender requests growing prefixes from offset zero:
counts 7, 13, 19 and so on, split at the native 200-record ceiling. For full scans,
the collector uses that 200-record ceiling and advances the wire offset by each
returned count. This needs fewer requests than seven-row pages and avoids
repeatedly reading earlier prefixes. The Lua page buttons use a 500 ms debounce;
that is not a proven network interval or server rate limit. The collector's
200-row batches and one-second gap are the selected policy. Source paths, lines
and hashes are recorded in the [Steam client evidence](research/steam-client-2026-10-23.json).

Production collection runs at most once per backend in the first attempt of each
daily schedule or manual dispatch with `fetch_rankings` enabled. Other manual
runs, pushes and reruns publish stored data. Failed backends retain their last-good
snapshot until the next scheduled or opted-in manual attempt. All three Steam
backends have complete verified snapshots under this policy.

Steam sessions have a 10,800-second capacity ceiling; mobile sessions retain
3,600 seconds. The collector default and workflow allow 18,000 seconds across all
requested backends, so the remaining shared budget can stop a session earlier.
These are capacity ceilings, not completion promises or expected running times.
A measured 5,000-row Steam interval exceeding 6.5 minutes showed that the old
one-hour session ceiling was insufficient for the estimated Asia ranking at that
pace. The extension changes time allowances only: 200-row Steam batches,
one-second batch gaps, bounded reply waits and the outgoing allowlist remain in
force. It adds no retries or account-setup actions. After shared-budget expiry,
unfinished and unvisited backends retain last-good data and receive failure
status; only completed enumerations can replace snapshots.

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
Nonempty means the literal returned string has content; it is never trimmed or
replaced with a synthesized name. The completed Steam Asia response includes a
resolved name consisting of the single character U+3000 IDEOGRAPHIC SPACE
(three UTF-8 bytes). It remains resolved and unchanged in the export and browser.
Browser validation follows the same nonempty-string contract as Python; a
whitespace-only returned value is not converted to null or marked unavailable.
A 14 response is a primary lookup miss, not proof that the name is unavailable.
An explicit empty mask-4 name completes the primary field without resolving it;
the collector starts its single nickname fallback immediately. A missing name
field still waits for the original bounded primary deadline.

The native 14 handler at `0x007f8090` calls the nickname lookup path. Its sender at
`0x007fa2f0` sends request 16 with one `u32 playerID`. Reply 17 begins with
`u16 status`; success adds `u32 playerID`, a name C string bounded to 48 bytes,
and one uninterpreted byte. Failure has no player ID. The collector sends a
bounded group of at most 10 and drains exactly the same number of 17 replies
before another nickname group or ranking phase. Success identifiers must be
expected and unique. Unkeyed failures can be assigned to remaining requests only
after the group completes. Late 380
responses can still resolve a name. An incomplete/malformed fallback closes the
connection and cannot produce a new partial snapshot.

After draining a nickname group, the collector waits 250 milliseconds before
the next group. Session and whole-run deadlines are checked before and after the
wait, which cannot extend either budget. This reduces bursts of public database
reads. It does not establish a server rate limit or prove a fix for observed
timeouts and disconnects.

One bounded recovery is allowed after a primary social-name timeout: identity-
bearing late 380/14 responses remain observable while request 16 is sent at most
once for each unresolved identity. This is not a retry of request 15. Recovery
does not apply to ranking timeouts, expired connections or malformed/incomplete
nickname batches. Explicit completed nickname failures export `name: null` and
`nameStatus: "unavailable"`; names are not inferred from old snapshots or scores.

## Public export contract

`site/data/index.json` has schema `metal-waltz.rankings-index.v1`,
`generatedAtUtc`, and ten `servers` entries. Each entry has:

```text
key, name, aliases, file, rows, namesResolved, namesMissing,
startedAtUtc, completedAtUtc, refreshStatus, sha256
```

An attempted entry may also have `lastAttemptAtUtc`; a failed refresh records a
sanitized `errorCode`. `refreshStatus` is `seed`, `ok` or `failed`. The SHA-256
covers the exact referenced file bytes. A failure preserves the previous good
file and successful timestamps. Without any previous snapshot, `file`/hash/
snapshot times are null and counts are zero.

The browser accepts this absent-snapshot state only for an explicitly failed
backend with all snapshot metadata null and all counts zero. It clears previously
displayed standings and offers a fresh index check without fetching a null path.

`<key>.rankings.json` has schema `maiden-waltz.public-ranking-snapshot.v1` and:

```text
serverKey, aliases, serverNames, fund: 5, startedAtUtc, completedAtUtc,
rankingEnumerationComplete: true, namesComplete, scope, rows
```

Each row contains `rank`, `name`, `grandeur` and `nameStatus`. Ranks start
at 1, remain contiguous, and retain server order. A resolved name is a nonempty
string; an unavailable name is null. No protocol player ID, account ID,
credential, session value or raw response is part of this contract.

New observations add the paired fields `vehiclesStatus` and `vehicles`:

```text
vehiclesStatus: resolved | unavailable | not_collected
vehicles: null | [{capsuleTypeId, phase, level, fate}, ...]
```

`resolved` carries the ordered list actually returned by the server, including
an empty list. The other states carry null; legacy rows without either field
mean not collected. Capsule type is a public reference-data identifier, never
an owned-instance ID. Phase is the native generation byte; level is a carried
nonnegative value or null, and Fate Pin follows the observed category-20 flag.
The complete list is retained even when the row shows only its first six images.
No combat slot, private inventory, equipment or equipped costume is inferred.

Request 20 uses at most 50 `(u32 playerID, u8 mask)` records per transaction,
with initial mask 12. Keyed response 19 may split masks across records. Bit 4 carries
capsule type, phase and a faculty vector; bit 8 carries tactics pairs, consumed
without converting them into a guessed formation. Unknown masks, conflicting
duplicates and incomplete transactions stop the backend rather than publishing
a fabricated or partial list. Native evidence and read-only pilot aggregates are
recorded in [the public-vehicle trace](research/public-vehicles-protocol.json).

Disjoint mask-4 and mask-8 records for one player may occur inside the same 19
packet, as well as in separate packets. The Steam native handler dispatches each
record's fields independently. The collector accepts that grouping while rejecting
any repeated field, invalid identity or unsupported mask.
A correlated mask-zero record updates no fields and does not complete the
transaction or establish an empty/unavailable profile. The original deadline
still applies while actual fields or an explicit failure remain outstanding.

Response 18 is a keyed primary-detail miss and may follow a partial response 19.
The native client tracks capsule and tactics requirements independently. Identity
completion can reissue only the still-pending fields through 20, with mask 4, 8
or 12. The public vehicle view reads the capsule list; tactics are separate data.

The collector keeps one bounded recovery after a primary-detail miss: one 16 per
identity across the session, then one 20 requery of only the still-missing fields
after a successful 17. Previously observed fields remain intact. An explicit
capsule failure stays unavailable; an actually returned capsule list remains
resolved when only tactics are explicitly unavailable. A prior name-fallback 16
is never repeated. Timeouts and malformed replies are not retried; the backend
stops and retains its previous complete snapshot.

This bounded collector policy is narrower than the native client. Native 18 can
leave requirements pending when an identity lookup is throttled, and both a
successful 17 and a nickname field in 380 can release those requirements. The
native throttle is not an automatic retry timer. Static evidence for the
per-field lifecycle is recorded in [the Steam vehicle investigation](research/steam-vehicles-2026-10-24.json);
it does not establish reliable full Steam vehicle collection. Steam vehicle
collection remains disabled pending bounded live verification.

The separate `assets/vehicles/catalog.json` maps public types/phases to names and
default presentation models, then to hash-addressed PNG portraits. Live2D entries
use static face-focused renderings from the existing Maiden Waltz viewer; no Core,
SDK, model, texture bundle or account data is delivered to the public browser.
The publisher validates the data pin, exact catalog fields, paths, PNG dimensions,
byte counts and hashes before copying the images.

The publication script rejects unknown fields, path changes, links, hash/count
mismatches, invalid names, incomplete enumerations and unexpected files. It
copies only reviewed static assets and validated public JSON into `_site`.
The browser loads one selected backend snapshot at a time and renders names as
text. It never connects to game servers or receives the collector secrets.
