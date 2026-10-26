# Operations

## Scheduled collection

`.github/workflows/publish.yml` runs daily at **04:17 UTC**, which is **06:17
Europe/Paris in summer** and **05:17 in winter**. The first attempt of a scheduled
run contacts the game servers. To collect manually, open **Actions → Refresh
rankings and publish → Run workflow**, select `main`, check **Fetch fresh rankings
from the game servers before publishing** (`fetch_rankings`), and start a new run.
The `collection_scope` input defaults to `all` active backends. Choose
`affected-mobile` to check Korea (`KOREA`) while the temporary pause below is in
effect. Scheduled runs and manual `all` requests select the eight active backends.
Each backend is attempted at most once per collection run; a failure retains its
previous snapshot until the next scheduled or opted-in manual attempt.
Manual runs leave this checkbox unchecked by default and publish stored data.
Pushes to `main` and workflow reruns also publish stored data without a game login,
even when the original run requested collection. Start a new manual run to fetch
again. The collection step checks GitHub's event, input and attempt
contexts ([GitHub context reference](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#github-context)).
Use **Run workflow** on current `main` for manual refresh or publication: rerunning a
historical run from before this policy can execute that older collection logic.

GitHub runs this on its own Linux machines. Schedules use the default branch;
they may be delayed or dropped during high load. Public repositories can have
scheduled workflows disabled after 60 days without repository activity. Check
the Actions tab when data looks stale, and re-enable the workflow if needed.
[GitHub schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

The site displays each backend's successful collection time and refresh status.
A fresh manifest time does not make a retained snapshot fresh. An unavailable
name is also distinct from a failed scan: a complete ranking may have explicit
nickname failures from the game server.

Each fresh mobile backend scan also requests public vehicle profiles for every
ranking entry. Complete rankings can publish with `unresolved` vehicle profiles
when the server returns keyed zero-field descriptors after bounded recovery.
Those profiles have a null list and display “Vehicle data unresolved”; actual
empty lists and explicit unavailable responses remain distinct. Late observed
fields can still resolve a profile before publication. Silent timeouts, malformed
responses and incomplete rankings stop that backend and retain its last complete
snapshot. JSON exports preserve each observation, and successful collection logs
report separate counts for empty, nonempty, unavailable, unresolved and uncollected
vehicle profiles.
Steam collection is configured for rankings and names with vehicle lists explicitly
marked `not_collected`: native profile probes reached incomplete transactions.
Steam USA's first complete snapshot is verified: 10,054 ranking rows, 10,054
resolved names and no missing names. It was collected from 21:57:07.588015 to
22:05:57.924638 UTC on 23 October 2026, taking 530.336623 seconds.
Steam Europe then completed with 37,913 rows, all 37,913 names resolved and none
missing, from 22:05:58.073179 to 22:25:49.113775 UTC, taking 1191.040596 seconds.
Steam Asia / Hong Kong completed with 98,516 rows and all 98,516 names resolved,
from 22:25:49.743622 UTC on 23 October to 00:21:46.314406 UTC on 24 October,
taking 6956.570784 seconds. All three Steam backends are now verified, with
146,483 rows and names and no missing names. Independent publication and Node
browser validators passed for all three snapshots; live browser previews passed
for Steam USA and Steam Asia / Hong Kong. The [Steam evidence](research/steam-client-2026-10-23.json)
records the exact byte count, hash and independent checks. Steam's optional
profile reads must be verified separately before enabling them.

Portrait assets are generated once from the pinned Maiden Waltz reference data
and committed with their catalog and hashes. Daily GitHub collection uses those
static assets and needs no desktop renderer or Live2D SDK. Regenerate and review
the assets separately when updating game data or presentation sources.

Mobile ranking, primary-name and vehicle batch requests share a minimum **250 ms**
start-to-start gap. Mobile ranking batches contain up to 200 entries; primary-name
and vehicle batches allow up to 200 names and 50 profiles respectively.

The collector first enumerates and validates every ranking page, then retrieves
names and vehicles in cohorts of at most 200 identities. Slow profile responses
therefore do not leave minutes between ranking offsets. It keeps the observed
ranking order and scores; it does not silently remove duplicates or sort a moving
ranking into apparent consistency. A rank change during the shorter enumeration
can still fail its duplicate, overlap or score-order checks. Progress row counts
refer to completed profile cohorts; failure diagnostics distinguish ranking rows
fetched from rows whose profile processing completed.

Steam ranking batches ask for **up to 200 new rows**, advancing by the actual
returned count. Steam ranking and primary-name batches share a minimum **one-second**
start-to-start gap; the same gap applies to public-vehicle batches if those reads
are enabled in a separately verified change. The ordinary game interface shows
six rows and tests one lookahead row. It asks for prefixes of 7, 13, 19 and so on
from offset zero. The selected 200-row collector batches use the native ceiling
and require fewer requests for a full scan than seven-row pages; advancing the
offset also avoids repeatedly reading earlier prefixes.
Its 500 ms page-button debounce is an interface setting. The collector's one-second
gap is a chosen pacing policy, and no server rate limit is established.
This paging policy retains bounded reply waits; an incomplete scan cannot replace
a snapshot.

Nickname fallback remains groups of 10, fully drained before a 250 ms pause and
the next group. Heartbeat acknowledgements remain immediate.

Mobile collection also follows the native receive-idle keep-alive sequence:
empty probes at the 30-, 40- and 50-second thresholds, followed by the 60-second
idle-failure stage. Any positive socket receive resets this sequence, including
an incomplete frame. A timer check advances at most one stage; it never sends a
catch-up burst. Existing reply, session and whole-run deadlines take precedence,
so a required reply can time out before all idle stages are reached. Pings do not
extend a read deadline or trigger a replay. This change enables proactive probes
for mobile collection only; Steam retains its existing server-ping acknowledgements.

Offline checks run once before any game login. Each selected backend then has its
own collection job. The matrix uses `max-parallel: 1` and `fail-fast: false`, so
sessions never overlap and a failed server does not cancel the remaining jobs.
Each job restores the latest durable index, collects exactly one backend with
`--max-duration-seconds 19800`, validates and saves the result, then reports any
collection failure. Matrix execution order is not guaranteed.

Session ceilings are **19,800 seconds (5½ hours) for Bonn**, **10,800 seconds
(three hours) for BANGKOK and Steam**, **5,400 seconds (90 minutes) for KOREA**,
and **3,600 seconds (one hour) for the other mobile backends**. A session also
obeys its job's remaining collector budget, which starts before snapshot loading.
These are capacity ceilings, not expected running times or completion guarantees.
Some observed zero-field vehicle batches took about five seconds for the initial
response and another five seconds for bounded recovery; longer jobs permit a
full scan without increasing request frequency or skipping profiles.

The local CLI still defaults to a five-hour shared budget when multiple servers
are selected. On budget expiry, the current and unvisited local backends receive
`collection_deadline` without further logins. Completed refreshes and previous
good snapshots are retained, and the manifest is written before failure exit.
Hosted jobs select one backend, so its deadline cannot consume another server's
budget. Connection and reply waits check the same deadline, subject to a short
socket polling allowance.
Required replies allow up to 30 seconds; the initial social-name lookup keeps its
12-second wait before the reviewed nickname fallback. No read is automatically
replayed after a timeout. Failed collection logs include a fixed `readPhase` label
to help distinguish connection, login and ranking/name failures.
The capacity extension adds no retries, registrations or commander creation, and
does not change Steam's 200-row batches or one-second gap.

## Temporary mobile collection pause

As of **26 October 2026**, fresh collection for mobile Europe №1 (`Bonn`) and
Thailand (`BANGKOK`) is paused as a precaution following repeated outages during
scans. The observed disconnects and player reports do not establish that the
collector caused the server outages.

The shared `PAUSED_BACKENDS` policy in `collector/configuration.py` excludes both
from every scheduled and manual collection plan and from local CLI `all` requests.
Explicit local CLI requests for a paused key or alias fail before credentials are
read or a network connection is opened. There is no command-line override.
The paused-only Actions choices are removed; legacy `bonn` and `europe-thailand`
planner inputs skip fresh collection with a clear notice.

All ten backends remain in the public directory. Publication continues to validate
and serve their last-good snapshots, keeping the original successful timestamps
and attempt metadata. The other eight backends, including Steam Europe, remain
enabled. Resumption requires a reviewed policy change; there is no automatic expiry.

## Credentials and permissions

Only the collection step receives `MW_COLLECTOR_USERNAME` and
`MW_COLLECTOR_PASSWORD` from repository Actions secrets. They identify the
dedicated collector account, including its regional registrations. Account and
player identifiers stay in process memory. No raw packet, password, session or
credential files are published. Do not enable shell tracing or add packet dumps
to a workflow. Never commit an `.env`, DPAPI store or game capture.

Pull requests run offline tests and seeded site validation without game secrets.
Publication runs only on `main` in `AlexValliere/MetalWaltzRanking`. Collection and
build jobs can write the data branch; the separate deployment job has `pages: write` and
`id-token: write` in the `github-pages` environment. Official GitHub actions are
pinned to full commit hashes. Dependabot proposes weekly dependency updates.

All collector and deployment runs share one concurrency group with cancellation
disabled for the active run. The collection matrix allows only one active child;
the local collector also processes servers sequentially.
Use another account for other game clients; concurrent logins can disrupt a scan.

The same dedicated credentials cover mobile and Steam. Steam uses its own native
transport profile and three logical backends: `SteamJP` (also the `SteamTEST`
Hong Kong route), `SteamBonn` and `SteamUSAVA`. Each needs exactly one existing
commander. Account registration and initial commander setup are separate,
explicitly authorized operations; the scheduled collector contains neither.
For a separately authorized local Steam-only refresh, use `--server steam`.

## Last-good storage

The `main` branch contains the reviewed initial seed and application source.
The dedicated **`snapshots`** branch stores only:

```text
.metalwaltz-snapshots.json
data/index.json
data/<backend>.rankings.json
```

It is intentionally a **latest-only branch** with one root commit. Each refresh
replaces its head after validating its ownership marker, data-only file tree,
JSON schema, hashes, counts and ranking order. A force-with-lease protects the
observed remote head against concurrent writes. An unrelated or malformed
existing branch is refused. Old commits are not kept reachable by this branch;
GitHub's removal of unreachable objects is outside this project's control.

Every deployment first restores this data branch. If it does not exist, the
validated seed is used. A successful server refresh replaces that server's
complete snapshot. A failed refresh preserves its previous file, count, hash and
successful timestamp while recording the attempt and a sanitized error code.
The combined data is validated again before storage and publication. Each server
job saves its result before reporting collection failure. After every child has
finished, the build restores the latest durable data and can deploy it even if a
child failed. Failed validation or storage in a child leaves the durable branch
unchanged for that child; its job remains failed. A failure in the final build's
restore, validation or storage blocks deployment. The run reports collection
failures separately from successful publication of available data.

The first Steam deployment can restore the exact legacy seven-backend data
branch. It validates that entire branch, retains every mobile snapshot and its
refresh metadata, adds only the three validated Steam seed entries, and validates
the resulting ten-backend set before replacement. The ordinary publisher requires
all ten entries. Unknown or partial backend sets are refused. The first save then
replaces the legacy branch under the same force-with-lease check.

Do not merge `snapshots` into `main`, commit manually to it, or protect it against
the workflow's intended latest-only update. To keep historical snapshots, download
the public JSON separately; this repository does not promise a historical archive.

## Troubleshooting

| Symptom                                                  | Action                                                                                                                                               |
| -------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| All backends report `credentials_missing`                | Add both repository Actions secrets before the next scheduled or opted-in manual collection. |
| A backend rejects login                                  | Verify the dedicated account still works in that authentication region. This collector cannot register or repair accounts.                           |
| A commander is missing or ambiguous                      | Check the account's server setup. This collector requires exactly one existing commander and never creates or selects a guessed account.             |
| Connect timeout                                          | Retain the previous snapshot until the next scheduled or opted-in manual attempt. Check game availability and endpoint configuration if failures persist. |
| Offset, ordering, framing or name batch validation fails | Keep the retained snapshot and investigate protocol or moving-ranking behavior. Do not disable the validator.                                        |
| `collection_deadline`                                    | The shared collection budget expired. Retained data remains available; review the budget if it recurs before the next scheduled or opted-in manual attempt. |
| Some names are unavailable                               | These are explicit completed nickname failures. Rank and Grandeur remain valid; a later refresh may resolve them.                                    |
| Snapshot storage refuses the branch                      | Check its marker/tree and whether another writer changed it. Preserve unexpected contents before any repair.                                         |
| Pages setup/deployment fails                             | Select GitHub Actions as the Pages publishing source and check plan eligibility and the `github-pages` environment.                                  |
| Schedule stops or becomes late                           | Check GitHub Actions status, repository workflow state and the schedule notes above. Start a new manual run with **Fetch fresh rankings from the game servers before publishing** checked to collect now. |

Logs intentionally contain aggregate progress and fixed error codes. They do not
identify players or include server packet bodies. Never paste collector secrets
into an issue or workflow dispatch field.

Failure diagnostics separate a server logout notification, socket EOF, reset,
broken pipe and other socket failures where observed. They also report heartbeat
counts, successfully sent acknowledgement counts, elapsed time and recent I/O ages.
Heartbeat receive counts and ages refer to decoded-message dispatch, which can
follow socket arrival; `lastReceiveAgeSeconds` measures the last positive socket
receive. These values alone do not measure time spent waiting in the decoded queue.
`attemptRowsFetched` and `attemptRowsCompleted` describe the failed attempt;
the existing `rows` field describes the retained public snapshot. Diagnostic
fields stay in logs and do not alter public snapshot or manifest schemas.

Failures before a protocol session exists also report `connectAttempts`, fixed
counts for refused, unreachable, timed-out, name-resolution, reset and other
connection errors, plus the last failure's endpoint ordinal and setup operation.
No address, port or arbitrary exception text is logged. Each reviewed endpoint
is tried at most once, and a successful TCP connection ends that fallback sequence.
A connection refusal before login cannot demonstrate a keep-alive problem; the
server must first accept a connection for live keep-alive validation to be useful.

## Updating the application

Run the offline Python tests, publication/storage tests, Node interface tests and
`python scripts/build_site.py`. Review a protocol dependency change against the
recorded native evidence before enabling live collection. Changes to game source
data, endpoint configuration or transport constants need explicit provenance.

CI uses Python 3.13 and Node 24; the collector pins its sole runtime dependency
in `requirements.txt`. A successful local seed build does not establish cloud
connectivity. Confirm collection and Pages deployment in the GitHub run before
claiming the scheduled service is live.

GitHub's current Pages site limit is 1 GB, comfortably above this seed. Hosted
Actions have execution and account usage limits; inspect billing/usage if the
repository's plan or visibility changes. Each collection job's timeout is **350 minutes**,
below the current six-hour GitHub-hosted job limit. [Actions limits](https://docs.github.com/en/actions/reference/limits).
The collector's maximum 330-minute budget leaves approximately 20 minutes for
setup, validation and durable storage. Setup time is part of that reserve, so
this is not a guaranteed allowance after collection finishes. Build/upload runs
separately with a 30-minute timeout and Pages deployment with a 10-minute timeout.
Publication artifacts expire
after one day; durable public data is the `snapshots` branch, not the temporary
artifact.
[Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits),
[Actions limits](https://docs.github.com/en/actions/reference/limits).
