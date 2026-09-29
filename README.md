<p align="center">
  <img src="logo_dark.png" width="200" alt="Fanta Cats logo">
</p>

# Fanta Cats

Downloads official Excel exports from fantacalcio.it (your logged-in
session, no HTML scraping) and merges them into one dataset.

## Layout

```
fanta.py, fantatool/     the tool
ids.json                 id -> season/league mapping (you review/edit this)
data/{league}/*.xlsx     raw downloads, one subfolder per league
output/                  merged deliverables: fanta.db, CSVs, fanta_summary.xlsx
logs/                    download_log.csv + probe diagnostic dumps
notebooks/               explore_fanta_db.ipynb - worked examples for querying fanta.db
```

`data/`, `output/`, `logs/` and `.env` are all gitignored - only the tool
itself and `ids.json` are meant to be committed.

## Setup

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Refreshing the auth cookie

fantacalcio.it authenticates by session cookie, not an API token.

1. Log into fantacalcio.it in your browser.
2. Open DevTools -> Network tab.
3. Click a download button (e.g. "Statistiche") to trigger a request to
   `/api/v1/Excel/...`.
4. Click that request -> Headers -> Request Headers -> copy the full
   `Cookie:` value (no surrounding quotes).
5. Paste it into `.env` as `FANTACALCIO_AUTH=<cookie string>`.

The cookie expires periodically. If any command exits with "Session
expired or cookie invalid", repeat these steps.

### Endpoints

| file_type | confirmed | notes |
|---|---|---|
| `stats` | yes | Serie A season stats. `competition_id=2` and above return empty - this endpoint only ever serves Serie A. |
| `prices` | yes | Euro Leghe quotazioni (75-team pool across Serie A + Premier League + La Liga + Bundesliga + Ligue 1 etc). Its `season_id` numbering is unrelated to `stats`' - e.g. `season_id=103` on `/prices/` is the same real season as `season_id=15` on `/stats/`. |
| `votes` | yes | Serie A matchday votes + bonus/malus events. Different shape from the other two: `/votes/{season_id}/{matchday}` - the second slot is a matchday number (1-38), not a competition_id. `season_id` numbering matches `stats` exactly. |
| `votes_euroleghe` | **not yet** | Euro Leghe matchday votes. Capture the URL from the Euro Leghe "Voti" download button (DevTools -> Network) and set `FANTACALCIO_EUROLEGHE_VOTES_URL_TEMPLATE` in `.env`, using the literal placeholders `{season_id}` and `{matchday}` (assumed same shape as `votes`, unconfirmed). |

`stats` and `prices` are keyed by (season_id, competition_id); `votes` and
`votes_euroleghe` are keyed by (season_id, matchday) instead - see
`config.MATCHDAY_FILE_TYPES`. Every file_type has its own season_id
numbering except votes, which reuses `stats`' directly (no separate probe
needed for it).

## Workflow

### 1. Probe the ids (once per file_type, or whenever you suspect the mapping changed)

```
python fanta.py probe --file-type stats  --season-id 17  --competition-id 1
python fanta.py probe --file-type prices --season-id 103 --competition-id 1
```

This does exactly three things per run, one request every 3 seconds, and
stops at the first failure in each direction:

- downloads the one file at your seed id and prints its columns, so you
  can confirm the format
- walks competition_id up from 1 to find every competition available at
  that season (for `stats` and `prices` this stops immediately at 2 - only
  one competition exists per file_type)
- walks season_id down, then up, from your seed to find the season range
  (capped at 30 steps down / 5 steps up - if you suspect more seasons
  exist beyond that, rerun with a further-out `--season-id` seed)

It writes:

- `logs/ids_probe_raw_<file_type>.json` - raw metadata (filename, sheet
  names, columns) for every id it tried, for your own inspection
- `ids.json` - merges in its best guess at season/league labels for that
  file_type, built by matching keywords in the filename against
  `LEAGUE_KEYWORDS` in `fantatool/config.py`. Existing entries for other
  file_types are left untouched.

**Check `ids.json` before downloading.** Anything labeled `UNKNOWN`, or any
guess that looks wrong, edit by hand using the matching
`logs/ids_probe_raw_<file_type>.json` as reference (it has the actual
filename/columns fantacalcio.it sent back). Also worth a spot check: open
one of the raw probe files and confirm the season range actually reaches
as far back/forward as you expect - the up/down step caps mean a very long
history could get truncated silently otherwise.

### 2. Download

```
python fanta.py download --seasons 2023-24 2022-23 2021-22 --leagues "Serie A" --file-types stats
python fanta.py download --seasons all --leagues all --file-types prices
python fanta.py download --seasons 2023-24 --file-types votes --matchdays 1-38
```

- `--seasons` / `--leagues` take the labels from `ids.json` (or `all`) -
  `--leagues` is ignored for `votes`/`votes_euroleghe` (always Serie A /
  Euroleghe respectively)
- `--file-types` selects which endpoints to hit (`stats`, `prices`,
  `votes`, `votes_euroleghe` - default is all four, skipping any without
  a configured URL)
- `--matchdays` (votes only): individual numbers and/or `A-B` ranges, e.g.
  `1-10 15 20-22`. Default is `1-38`, stopping early per season on the
  first matchday that isn't real data yet (checked by content, not just
  file size - a future/unplayed matchday returns a small "come back
  later" placeholder that a byte-count check alone wouldn't catch)
- files are saved to `data/{league}/{file_type}_{season}.xlsx` (or
  `..._{season}_g{matchday}.xlsx` for votes), one subfolder per league
  (e.g. `data/serie_a/stats_2026_27.xlsx`,
  `data/euroleghe/prices_2020_21.xlsx`, `data/serie_a/votes_2023_24_g18.xlsx`)
- already-downloaded files are skipped, except the current season (highest
  season_id in `ids.json` for that file_type) when `--update-current` is
  passed
- every request is logged to `logs/download_log.csv`

### 3. Merge

```
python fanta.py merge
```

Reads every file under `data/*/*.xlsx` (votes files get a dedicated parser
for their per-team-block sheet layout - see
`fantatool/merge.py::_parse_votes_workbook`), tags rows with
`league`/`season`, resolves player identity across leagues, seasons and
granularities, and writes to `output/`:

- `fanta.db` (SQLite: `players` table, plus `stats`/`prices`/`votes`
  tables for whichever file_types you've actually downloaded)
- matching CSVs: `players.csv`, `stats.csv`, `prices.csv`, `votes.csv`
- `needs_review.csv` - identity matches worth a human glance (deduped: the
  same conflict repeating across many matchday/season files collapses
  into one row with an `occurrences` count)
- `fanta_summary.xlsx` - one row per player currently in a Serie A squad
  (latest season in `stats`), with their last 3 seasons of stats side by
  side, whatever league those seasons were played in

Player identity is resolved primarily by **fantacalcio.it's own internal
player id** (`Id` in stats/prices, `Cod.` in votes - confirmed identical
across all three file types), not by guessing from the name string. Since
that id is deterministic, `player_id` stays the same across repeated
`merge` runs as you download more data - safe to reference elsewhere.
Name-based fuzzy/initial-form matching (`fantatool/names.py`) is only a
fallback for the rare row with no usable id, and those get a `NM<n>`
id instead of `FC<n>` (the ids from that fallback *can* shift between
runs). A re-merge across the full Serie A (2020-21 to now) + Euro Leghe
(2017-18 to now) + Serie A votes (2020-21 to now, ~270 matchday files)
dataset takes well under a minute.

### 4. Modelling dataset

```
python fanta.py dataset --seasons 2025-26 --matchday 5
python fanta.py dataset --seasons all --matchday 5
```

Builds `output/dataset_<seasons>_md<N>.csv` for predicting a player's
full-season performance from his previous season and his first N
matchdays (`fantatool/dataset.py`, or `dataset.build(season, matchday)`
from code). One row per (player, season):

- `prev_*` - previous Serie A season summary from `stats` (appearances,
  avg_vote, fantamedia, goals, assists, cards, ...). NaN if the player
  wasn't in Serie A that season. Age, nationality, height and foot are
  not in any fantacalcio.it export we download.
- `mdXX_*` - one block per matchday 1..N: `played`, `sv`, `vote`,
  `fantavote`, bonus/malus events. Every player gets every block; `vote`
  and `fantavote` are NaN when he wasn't rated. `sv=1` means he came on
  but got S.V. ("6*" in the raw file) - excluded from averages exactly
  like fantacalcio.it does. `fantavote` uses the classic bonus/malus
  (`FANTAVOTE_WEIGHTS`); `raw__rf` in votes is penalties scored.
- `target_*` - the official full-season summary from `stats`. NaN for a
  season that isn't finished yet (no matchday 38 in `votes`), and for
  players who left Serie A mid-season.

For "what do we know about each player right after matchday N" - no
targets, nothing from later matchdays - use `snapshot` instead:

```python
from fantatool import dataset
players, ts = dataset.snapshot("2023-24", 10)
```

`players` is one row per player: identity (`name`, `role_classic` (P/D/C/A), `role_mantra`,
`team`), `price_initial` (Euroleghe starting price Qt.I, set pre-season -
NaN outside the Euroleghe pool), `prev_league` (Serie A, or the league from
last season's Euroleghe prices, else NaN), `prev_*`, and `so_far_*`
(this season's appearances, S.V. count, avg vote, fantamedia, goals, ...
over matchdays 1..N only, same definitions as `stats`). `ts` is the long
time series, one row per (player, matchday 1..N). The same call on a past
season gives exactly what you'd have had live (tested: adding future data
to the DB leaves the snapshot unchanged).

To get features and outcomes for a model in one call, use `xy`:

```python
X, X_ts, y = dataset.xy("2023-24", 10)
```

`X`, `X_ts` are exactly `snapshot()`. `y` is one row per player, same
order, holding what's still unseen at matchday N: `next_*` (matchday N+1:
played, sv, vote, fantavote, events - NaN if not downloaded yet or N=38)
and `season_*` (official full-season stats - NaN while the season is
running, or for players who left Serie A mid-season). Never use `y`
columns as inputs.

Rows are the players in Serie A as of matchday N (`dataset.roster()`),
using only what was known then, so a past season at matchday N looks like
a live one. There's no per-matchday squad list, so this is: anyone in a
votes file for matchdays 1..N, plus - for a live season - the whole
`stats` list (today's squads), or - for a past season - players at the
same club as the season before (their end-of-season `stats` list would
include January signings). Summer signings not yet fielded by matchday N
are missed for past seasons. `team` is the latest team seen up to
matchday N, falling back to that same `stats` team.

## Weekly update

```
python fanta.py download --update-current
python fanta.py merge
```

If this fails with an auth error, refresh the cookie (see above) and
re-run.

## Exploring the data

```
jupyter lab notebooks/explore_fanta_db.ipynb
```

`notebooks/explore_fanta_db.ipynb` has worked examples against `fanta.db`:
schema overview, basic queries, looking up a `player_id` by name, a
cross-season transfer timeline, linking the same player across `stats`
(Serie A), `prices` (Euro Leghe) and `votes` (matchday-level) despite
those coming from entirely different files, and a sanity check comparing
season-level `stats.avg_vote` against a value recomputed from raw
per-matchday votes.

For code (not notebook exploration), `fantatool/loader.py` has the
reusable query layer - e.g. `player_matches(conn, "Piccoli")` returns one
row per matchday (season, league, matchday, team, vote, goals, assists,
cards, ...) for a given player, by name or `player_id`. This is meant to
be the first building block of a data-loading pipeline on top of
`fanta.db`; add further loaders there rather than inlining SQL elsewhere.

## Notes

- Column names in `fantatool/merge.py` (`COLUMN_ALIASES`) are mapped from
  the real headers fantacalcio.it sends (confirmed for `stats`, `prices`
  and most of `votes`). Three votes columns (`Rf`, `Gdv`, `Gdp`) aren't
  confidently identified and are kept as `raw__rf`/`raw__gdv`/`raw__gdp`
  rather than guessed wrong - rename them in `COLUMN_ALIASES` once
  confirmed. Any other unrecognised column shows up the same way.
- `ids.json` and `logs/download_log.csv` are the source of truth for what
  `merge` considers a valid file - don't hand-edit filenames under
  `data/` without updating `logs/download_log.csv` too, or the
  league/season tags will be guessed from the file path instead (and
  logged as a warning).
- `votes_euroleghe` isn't wired up yet (see the endpoints table above) -
  Euro Leghe currently only has season-level data via `prices`, no
  matchday-level votes.
