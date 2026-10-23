# Metal Waltz Ranking

A public Grandeur ranking browser for every visible Metal Waltz server. It shows
server order, player names, Grandeur, public vehicles, collection time and missing-name counts.
Search and pagination run in your browser; the game login stays in GitHub Actions.
The appearance follows your system's light or dark preference, with dark mode as
the fallback. Use the header toggle to switch modes and remember your choice.

[Open the rankings](https://alexvalliere.github.io/MetalWaltzRanking/) ·
[Refresh / deployment runs](https://github.com/AlexValliere/MetalWaltzRanking/actions/workflows/publish.yml)

The collector runs on a GitHub-hosted Linux runner **daily at 04:17 UTC**
(06:17 in Paris during summer time, 05:17 during winter time). Your laptop does
not need to be running. Each server gets at most one collection attempt per run;
failed servers retain their previous data until the next attempt.
To refresh manually, open **Actions → Refresh rankings and publish → Run workflow**,
select `main`, check **Fetch fresh rankings from the game servers before publishing**,
and start a new run. Leave it unchecked to publish stored data. Pushes to `main`
and reruns of current workflows also
publish stored data without a game login; use a new dispatch for a fresh attempt.

## Data coverage

The included seed was verified on **22 October 2026**. Eight server entries map to
seven distinct backends; the Japanese and Korean №2 entries share a ranking.

| Server                | Ranking entries | Names resolved |
| --------------------- | --------------: | -------------: |
| Europe №1             |          76,797 |         76,797 |
| Harrisburg / Android  |          14,061 |         14,061 |
| Columbus / iOS        |           3,529 |          2,076 |
| Japan №1              |           1,065 |          1,065 |
| Korea                 |          13,765 |         13,765 |
| Japan/Korea №2 shared |           8,324 |          8,324 |
| Thailand              |          35,169 |         35,169 |
| **Total**             |     **152,710** |    **151,257** |

These are all rows exposed by each server during its sequential scan. They are
not an atomic snapshot or a census of every registered commander. The initial
Columbus scan has 1,453 unavailable names: its native nickname query returned
status 48. Those entries retain their rank and Grandeur; names are never guessed.

Fresh dedicated commanders retrieved these rankings with **0 Clash wins**. The
game client's 460-win interface unlock was not required for the verified reads.

Vehicle portraits appear beside commanders on desktop and beneath their names
on mobile. Hover, focus or select a portrait for its vehicle and Maiden names;
selecting it also shows the returned level and Fate Pin. The collector requests
the actual public vehicle list for every ranked commander. Empty, unavailable
and not-yet-collected lists remain distinct, and longer lists remain accessible.
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
3. Run **Refresh rankings and publish** from `main` to publish the seed / last
   stored data, or check **Fetch fresh rankings from the game servers before publishing**
   (`fetch_rankings`) to collect fresh data first. Fresh collection also runs on
   the daily schedule. Review the run and each server's
   collection time on the site.

The workflow has a single concurrency group. Do not run another collector with
these credentials at the same time. It serializes the seven backend sessions and
never creates an account or commander, battles, claims rewards or purchases items.

Each collection has a shared **9,000-second (150-minute) budget** across all
requested backends. When that budget expires, completed refreshes are retained;
unfinished backends keep their last-good snapshots and show a failed refresh.
The 180-minute build job reserves its remaining time for setup, validation,
snapshot storage and publication upload.

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
The local CLI is available for separately authorized development diagnostics;
production refreshes use scheduled or opted-in manual workflow runs. For such
diagnostics, set the two collector environment variables in your shell without
writing them into tracked files, then run:

```sh
python -m collector collect --output site/data --max-duration-seconds 9000
python scripts/build_site.py
```

`--server Bonn` (or another backend key) limits a refresh to one server. The
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
