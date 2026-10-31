# Metal Waltz Ranking

A public Grandeur ranking browser for every visible Metal Waltz server. It shows
server order, player names, Grandeur, public vehicles, collection time and missing-name counts.
Search and pagination run in your browser; the game login stays in GitHub Actions.
The appearance follows your system's light or dark preference, with dark mode as
the fallback. Use the header toggle to switch modes and remember your choice.

[Open the rankings](https://alexvalliere.github.io/MetalWaltzRanking/) ·
[Refresh / deployment runs](https://github.com/AlexValliere/MetalWaltzRanking/actions/workflows/publish.yml)

**All fresh collection is paused as of 31 October 2026** while disconnects and
collector workload are investigated. The GitHub workflow was also disabled by the
maintainer. Existing rankings and vehicle observations remain available on the
site. The observed failures do not establish that the collector crashed a game
backend.

The repository's shared pause policy blocks every scheduled/manual collection
scope and local CLI selection before credentials or a game connection are used.
It has no automatic expiry or command-line override. Resuming collection needs a
reviewed policy change and an explicit decision to enable it. The configured daily
schedule remains **04:17 UTC** (06:17 Paris in summer, 05:17 in winter), but is
inactive while the workflow is disabled. Publication code still supports building
and serving validated stored snapshots without a game login.

## Data coverage

The mobile seed was verified on **22 October 2026 (UTC)**. The first complete
Steam USA and Steam Europe snapshots were verified on **23 October 2026 (UTC)**.
Steam Asia / Hong Kong completed on **24 October 2026 (UTC)**. The collector supports
the Steam directory reviewed on **23 October 2026**: Steam Asia / Hong Kong,
Steam Europe and Steam USA. Twelve visible server entries map to ten backends;
the Japanese/Korean №2 entries and Steam Asia/Hong Kong routes each share a ranking.

| Server                | Ranking entries | Names resolved |
| --------------------- | --------------: | -------------: |
| Europe №1             |          76,797 |         76,797 |
| Harrisburg / Android  |          14,061 |         14,061 |
| Columbus / iOS        |           3,529 |          2,076 |
| Japan №1              |           1,065 |          1,065 |
| Korea                 |          13,765 |         13,765 |
| Japan/Korea №2 shared |           8,324 |          8,324 |
| Thailand              |          35,169 |         35,169 |
| Steam Asia / Hong Kong |          98,516 |         98,516 |
| Steam Europe           |          37,913 |         37,913 |
| Steam USA              |          10,054 |         10,054 |
| **Total available**     |     **299,193** |    **297,740** |

Available totals combine mobile snapshots from 22 October, Steam USA and Europe
from 23 October, and Steam Asia / Hong Kong completed on 24 October, all in UTC.
These ten complete snapshots were collected at different times. Steam uses a separately
fingerprinted native transport profile and the same dedicated collector
credentials. A Steam backend with no complete snapshot shows **Not collected**;
it does not appear as an empty leaderboard. Steam Asia's Hong Kong route is a
connection alternative, so its shared ranking is collected once.
Fresh public vehicle collection is disabled for **all backends**. A future
reviewed rankings/name refresh would mark vehicle lists `not_collected`; it would
not invent empty vehicle lists. Existing mobile vehicle observations remain in
their retained snapshots. Bounded Steam profile probes reached incomplete
responses. All three Steam backends have complete snapshots with all
146,483 names resolved, passing independent publication and browser validation. See the
[Steam verification record](docs/research/steam-client-2026-10-23.json)
for its timestamps, file hash and checks.

Ranking collection requests **up to 200 new rows at a time**, with at least
**one second between ranking, name and detail requests** on both mobile and Steam.
Nickname fallback has one outstanding request at a time and uses that same timer
across calls. Primary-name batches contain at most 50 identities. Heartbeat
acknowledgements remain immediate. These are precautionary
load controls, not an established safe server rate or a proven crash remedy.
The Steam game interface shows six rows plus one
lookahead and requests growing prefixes of 7, 13, 19 and so on. The collector
uses the native 200-record batch ceiling and advances its offset, so a full scan
needs fewer requests than seven-row pages or repeated prefixes. The one-second
gap is the selected collector policy, not an established server rate limit.

The mobile totals cover all rows exposed during each original sequential scan.
They are not an atomic snapshot or a census of every registered commander. The
initial Columbus scan has 1,453 unavailable names: its native nickname query
returned status 48. Those entries retain their rank and Grandeur; names are never guessed.

The mobile verification used fresh dedicated commanders with **0 Clash wins**. The
game client's 460-win interface unlock was not required for the verified reads.

Vehicle portraits appear beside commanders on desktop and beneath their names
on mobile. Hover, focus or select a portrait for its vehicle and Maiden names;
selecting it also shows the returned level and Fate Pin. Earlier mobile collections
requested actual public vehicle lists. Actual empty
lists, explicit unavailable results, unresolved field replies and not-yet-collected
data remain distinct, and longer lists remain accessible. A bounded keyed-zero
reply can leave vehicle data **unresolved** while a complete ranking and its actual
vehicle observations are published. Unresolved data carries null and is never
presented as an empty list or an explicit unavailable profile. Silent or malformed
responses still fail the refresh and retain its last-good snapshot.
These are public profiles in server order; a current Attack/Defense formation
or equipped costume is not established by this response.

Portraits follow the native phase's default model. Live2D models use generated,
face-focused static portraits from Maiden Waltz's existing rendering pipeline;
the website ships images rather than model runtimes. The reference catalog,
source pins and portrait hashes are checked before publication.

## Deployment configuration

1. Keep this repository public for GitHub Pages on GitHub Free, or use a plan
   supporting Pages for a private repository. In **Settings → Pages → Build and
   deployment**, select **GitHub Actions**. [GitHub Pages availability and setup](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).
2. Set repository Actions secrets `MW_COLLECTOR_USERNAME` and
   `MW_COLLECTOR_PASSWORD` to the dedicated collector credentials. The same
   credentials must already work on each regional authentication group, with
   exactly one existing commander per backend. [Creating repository secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).
3. Keep the workflow disabled during the collection pause. Once publication is
   separately enabled, **Refresh rankings and publish** from current `main` can
   publish the seed / last stored data without a game login. The current pause
   policy skips fresh collection even if `fetch_rankings` is checked. Review each
   server's successful collection time rather than treating publication as a refresh.

The workflow has a single concurrency group. Do not run another collector with
these credentials at the same time. It serializes the ten backend sessions and
never creates an account or commander, battles, claims rewards or purchases items.

Fresh collection runs one backend per job, with only one collection job active
at a time. Each job saves its validated results before the next server starts;
a failed job does not cancel the remaining servers. Europe №1 has a **5½-hour**
session ceiling, Thailand and Steam **three hours**, Korea **90 minutes**, and
the other mobile servers **one hour**. Every job has a **350-minute** limit,
including setup and storage; the collector's maximum budget is **330 minutes**.
These are capacity ceilings, not expected durations or guarantees of completion.
These ceilings do not authorize collection while the pause is in effect.
Build and deployment run separately after all selected collection jobs finish.

Validated last-good snapshots live on the dedicated `snapshots` branch. It has
one latest root commit and an ownership marker; collection updates replace that
branch with `--force-with-lease`. Source history stays on `main`. Failed server
refreshes retain their previous complete data and show a failure status. A run
with any failed refresh is marked failed after publishing the available data.

## Local preview and checks

Python 3.11 or later and Node.js 24 are sufficient. GitHub Actions uses Python 3.13.

```sh
python -m venv .venv
# Activate the virtual environment for your shell.
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p 'test_*.py'
python -m unittest discover -s scripts -p 'test_*.py'
node --test tests/site.test.mjs tests/vehicle-art.test.mjs
python scripts/build_site.py
python -m http.server 8000 --directory _site
```

Open `http://localhost:8000/` to view the seed. No login is needed for these checks.
The local collection CLI is blocked by the same all-backend pause. The following
invocation documents its normal form for a future reviewed resumption; it currently
fails before reading credentials or opening a game connection:

```sh
python -m collector collect --output site/data --max-duration-seconds 18000
python scripts/build_site.py
```

`--server YaleAndroid` normally selects one backend; `--server steam` selects the
three Steam backends, and `--server SteamTEST` selects the shared Steam Asia
backend. All of these choices, explicit keys/aliases, and default `all` currently
fail under the pause policy. The
collector uses the reviewed game protocol directly; it needs no Android emulator,
Windows DLL, game installation, Ghidra session or laptop hosting.

See [operations and recovery](docs/OPERATIONS.md), the
[protocol and public-data contract](docs/PROTOCOL.md), and the
[initial verification record](docs/research/verification-2026-10-22.json).

This is an independent fan project and is not affiliated with Metal Waltz's
developer or publisher. Player names and Grandeur originate from the game's
public ranking/social responses.

Maintained by [Alex Vallière](https://github.com/AlexValliere), also behind
[Metal Maiden Depot](https://www.metalmaidendepot.ovh/).
[Support the project on Ko-fi](https://ko-fi.com/alexvalliere).
