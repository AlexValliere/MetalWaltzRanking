# Operations

## Scheduled collection

`.github/workflows/publish.yml` runs daily at **04:17 UTC**, which is **06:17
Europe/Paris in summer** and **05:17 in winter**. Only the first attempt of a
scheduled run contacts the game servers. Each backend is attempted at most once; a failure
retains its previous snapshot and waits for the next daily scheduled run.
Manual runs from `main`, pushes to `main` and workflow reruns publish stored data
without a game login. The collection step checks GitHub's event and attempt
contexts ([GitHub context reference](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#github-context)).
Use **Run workflow** on current `main` for manual publication: rerunning a
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

Each fresh backend scan also loads public vehicle profiles for every ranking
entry. A completed profile may be empty or explicitly unavailable. A profile
timeout or malformed response stops that backend and retains its last complete
snapshot. The website distinguishes missing profile data from an observed empty
list, and JSON exports preserve the full returned public list.

Portrait assets are generated once from the pinned Maiden Waltz reference data
and committed with their catalog and hashes. Daily GitHub collection uses those
static assets and needs no desktop renderer or Live2D SDK. Regenerate and review
the assets separately when updating game data or presentation sources.

Ranking, primary-name and vehicle batch requests share a minimum **250 ms**
start-to-start gap. Batch sizes remain 200 ranking entries, up to 200 primary
names and up to 50 vehicle profiles. Nickname fallback remains groups of 10,
fully drained before a 250 ms pause and the next group. Heartbeat acknowledgements
remain immediate. This pacing reduces load; a server rate limit is not established.

The workflow passes `--max-duration-seconds 9000`: one **150-minute collection
budget** shared across all requested backends, starting before snapshot loading.
It is not a separate allowance for every server. After expiry, the current and
remaining unvisited backends receive `collection_deadline` failure status without
further logins. Completed refreshes and previous good snapshots are retained, and
the manifest is written before the collector exits with failure. Connection and
reply waits check the same deadline, subject to a short socket polling allowance.
Required replies allow up to 30 seconds; the initial social-name lookup keeps its
12-second wait before the reviewed nickname fallback. No read is automatically
replayed after a timeout. Failed collection logs include a fixed `readPhase` label
to help distinguish connection, login and ranking/name failures.

## Credentials and permissions

Only the collection step receives `MW_COLLECTOR_USERNAME` and
`MW_COLLECTOR_PASSWORD` from repository Actions secrets. They identify the
dedicated collector account, including its regional registrations. Account and
player identifiers stay in process memory. No raw packet, password, session or
credential files are published. Do not enable shell tracing or add packet dumps
to a workflow. Never commit an `.env`, DPAPI store or game capture.

Pull requests run offline tests and seeded site validation without game secrets.
Publication runs only on `main` in `AlexValliere/MetalWaltzRanking`. The build job
can write the data branch; the separate deployment job has `pages: write` and
`id-token: write` in the `github-pages` environment. Official GitHub actions are
pinned to full commit hashes. Dependabot proposes weekly dependency updates.

All collector and deployment runs share one concurrency group with cancellation
disabled for the active run. The collector itself processes servers sequentially.
Use another account for other game clients; concurrent logins can disrupt a scan.

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
The combined data is validated again before storage and publication. Collection
failures are reported after the site deployment so that available last-good data
remains accessible. Invalid data or failed storage blocks the new deployment.

Do not merge `snapshots` into `main`, commit manually to it, or protect it against
the workflow's intended latest-only update. To keep historical snapshots, download
the public JSON separately; this repository does not promise a historical archive.

## Troubleshooting

| Symptom                                                  | Action                                                                                                                                               |
| -------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| All backends report `credentials_missing`                | Add both repository Actions secrets before the next scheduled collection. |
| A backend rejects login                                  | Verify the dedicated account still works in that authentication region. This collector cannot register or repair accounts.                           |
| A commander is missing or ambiguous                      | Check the account's server setup. This collector requires exactly one existing commander and never creates or selects a guessed account.             |
| Connect timeout                                          | Retain the previous snapshot and wait for the next daily attempt. Check game availability and endpoint configuration if failures persist. |
| Offset, ordering, framing or name batch validation fails | Keep the retained snapshot and investigate protocol or moving-ranking behavior. Do not disable the validator.                                        |
| `collection_deadline`                                    | The shared collection budget expired. Retained data remains available; review the budget if it recurs and let the next daily run attempt collection. |
| Some names are unavailable                               | These are explicit completed nickname failures. Rank and Grandeur remain valid; a later refresh may resolve them.                                    |
| Snapshot storage refuses the branch                      | Check its marker/tree and whether another writer changed it. Preserve unexpected contents before any repair.                                         |
| Pages setup/deployment fails                             | Select GitHub Actions as the Pages publishing source and check plan eligibility and the `github-pages` environment.                                  |
| Schedule stops or becomes late                           | Check GitHub Actions status, repository workflow state and the schedule notes above. Manual dispatch republishes stored data; collection waits for a scheduled run. |

Logs intentionally contain aggregate progress and fixed error codes. They do not
identify players or include server packet bodies. Never paste collector secrets
into an issue or workflow dispatch field.

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
repository's plan or visibility changes. The build job's timeout is 180 minutes;
the collector's 150-minute budget leaves approximately 30 minutes for setup,
validation, durable storage and upload. Setup time is part of that reserve, so
this is not a guaranteed 30-minute allowance after collection finishes. Pages
deployment runs separately with a 10-minute timeout. Publication artifacts expire
after one day; durable public data is the `snapshots` branch, not the temporary
artifact.
[Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits),
[Actions limits](https://docs.github.com/en/actions/reference/limits).
