"""Modelling dataset: predict a player's full-season performance from
(a) his previous-season summary and (b) the time series of his first
`current_matchday` matchdays of the current season.

One row per player in Serie A as of `current_matchday` (see `roster()`).
Only data available at that matchday goes into the features, so a past
season at matchday N looks the same as a live season at matchday N.
Three column groups:

    prev_*          previous Serie A season summary (from `stats`), NaN if
                    the player wasn't in Serie A that season (other leagues
                    only have prices, no performance data)
    mdXX_*          matchday XX of the current season (from `votes`), one
                    block per matchday 1..current_matchday. NaN vote /
                    fantavote when the player wasn't rated (didn't play,
                    unused sub, injured, not yet at the club, ...)
    target_*        official full-season summary of the current season
                    (from `stats`), NaN when the season isn't finished yet
                    or the player isn't in the end-of-season Serie A list

Missing values are always explicit: every player gets every mdXX_* column.

Notes on the raw votes (verified against stats.fantamedia, ~97% of players
match to 0.02):
- a vote like "6*" (vote_provisional=1) is S.V. - the player entered but
  wasn't rated. fantacalcio.it excludes those from appearances and
  averages, so here vote/fantavote are NaN and `mdXX_sv` = 1.
- `raw__rf` is penalties scored (+3), counted separately from `goals`.
"""

import numpy as np
import pandas as pd

from . import loader

VOTE_SOURCE = "Fantacalcio"
LAST_MATCHDAY = 38

# Official classic fantacalcio bonus/malus, applied on top of the vote.
FANTAVOTE_WEIGHTS = {
    "goals": 3,
    "penalties_scored": 3,
    "goals_conceded": -1,
    "penalties_saved": 3,
    "penalties_missed": -3,
    "own_goals": -2,
    "yellow_cards": -0.5,
    "red_cards": -1,
    "assists": 1,
}
EVENT_COLS = list(FANTAVOTE_WEIGHTS)

SUMMARY_COLS = [
    "appearances", "avg_vote", "fantamedia", "goals", "goals_conceded",
    "assists", "yellow_cards", "red_cards", "own_goals", "penalties_taken",
    "penalties_scored", "penalties_missed", "penalties_saved",
]


def previous_season(season):
    """'2023-24' -> '2022-23'."""
    start = int(season[:4]) - 1
    return f"{start}-{str(start + 1)[-2:]}"


def _season_stats(conno, season):
    df = pd.read_sql(
        f"SELECT player_id, name, role, role_detail AS role_mantra, team, {', '.join(SUMMARY_COLS)} "
        "FROM stats WHERE season = ?",
        conno,
        params=[season],
    )
    return df.drop_duplicates("player_id").set_index("player_id")


def _season_prices(conno, season):
    """Euroleghe prices: league (the file's "Nazione" column is the league,
    not nationality) and starting price Qt.I. Only the Euroleghe pool
    (top clubs of 5 leagues). Qt.A / FVM are deliberately not read: the
    file is an end-of-season snapshot, so they'd leak."""
    try:
        df = pd.read_sql(
            "SELECT player_id, raw__nazione AS league, price_initial FROM prices WHERE season = ?",
            conno,
            params=[season],
        )
    except pd.errors.DatabaseError:  # no prices downloaded at all
        df = pd.DataFrame(columns=["player_id", "league", "price_initial"])
    return df.drop_duplicates("player_id").set_index("player_id")


def _season_votes(conno, season, current_matchday):
    df = pd.read_sql(
        """
        SELECT player_id, name, role, team, CAST(matchday AS INTEGER) AS matchday,
               vote, vote_provisional, goals, raw__rf AS penalties_scored,
               goals_conceded, penalties_saved, penalties_missed, own_goals,
               yellow_cards, red_cards, assists
        FROM votes
        WHERE season = ? AND vote_source = ? AND role != 'ALL'
          AND CAST(matchday AS INTEGER) <= ?
        """,
        conno,
        params=[season, VOTE_SOURCE, current_matchday],
    )
    df[EVENT_COLS] = df[EVENT_COLS].fillna(0)
    df["sv"] = df["vote_provisional"].fillna(0).astype(int)
    df.loc[df["sv"] == 1, "vote"] = np.nan
    df["fantavote"] = df["vote"] + sum(w * df[c] for c, w in FANTAVOTE_WEIGHTS.items())
    df["played"] = df["vote"].notna().astype(int)
    return df.drop(columns="vote_provisional")


def last_matchday(conno, season):
    """Highest matchday downloaded for `season` (0 if none)."""
    row = conno.execute(
        "SELECT MAX(CAST(matchday AS INTEGER)) FROM votes WHERE season = ?", [season]
    ).fetchone()
    return row[0] or 0


def season_is_complete(conno, season):
    return last_matchday(conno, season) >= LAST_MATCHDAY


def roster(conno, current_season, current_matchday):
    """Players in Serie A as of `current_matchday`, as a sorted list of
    player_id. There's no per-matchday squad list in the data, so:

    - anyone with a vote row (rated or S.V.) in matchdays 1..current_matchday
    - `current_matchday` is the latest downloaded matchday of a running
      season: plus the whole `stats` list, which is a snapshot of today's
      squads
    - otherwise (past season, or an earlier matchday of the running one)
      `stats` was downloaded later and would leak later signings, so only
      add players from it who were at the same club the previous season
      (there all along, just not fielded yet - injured, third keeper, ...).
      Summer signings who haven't played by current_matchday are missed.
    """
    voted = set(_season_votes(conno, current_season, current_matchday)["player_id"])
    stats = _season_stats(conno, current_season)
    latest = last_matchday(conno, current_season)
    if latest < LAST_MATCHDAY and current_matchday >= latest:
        return sorted(voted | set(stats.index))
    prev = _season_stats(conno, previous_season(current_season))
    same_club = stats.join(prev["team"].rename("prev_team"), how="inner")
    stayed = same_club.index[same_club["team"] == same_club["prev_team"]]
    return sorted(voted | set(stayed))


def season_timeseries(conno, current_season, current_matchday):
    """Long format: one row per (player, matchday 1..current_matchday),
    fully expanded - a player with no vote row that matchday still gets a
    row, with played=0, sv=0 and NaN vote/fantavote/events. Players are
    those in `roster()`."""
    votes = _season_votes(conno, current_season, current_matchday)
    players = roster(conno, current_season, current_matchday)

    grid = pd.MultiIndex.from_product(
        [players, range(1, current_matchday + 1)], names=["player_id", "matchday"]
    ).to_frame(index=False)
    ts = grid.merge(
        votes.drop(columns=["name", "role", "team"]), on=["player_id", "matchday"], how="left"
    )
    ts[["played", "sv"]] = ts[["played", "sv"]].fillna(0).astype(int)
    # Team at each matchday: only known when the player is in that
    # matchday's file; carried forward/back within the season otherwise.
    # Never fielded yet -> `stats` team, which roster() guarantees is
    # either today's squad (live season) or last season's club (past).
    team = votes.set_index(["player_id", "matchday"])["team"]
    ts["team"] = team.reindex(pd.MultiIndex.from_frame(ts[["player_id", "matchday"]])).values
    ts["team"] = ts.groupby("player_id")["team"].transform(lambda s: s.ffill().bfill())
    stats_team = _season_stats(conno, current_season)["team"]
    ts["team"] = ts["team"].fillna(ts["player_id"].map(stats_team))
    ts.insert(0, "season", current_season)
    return ts


def _known_players(conno, current_season, current_matchday, ts):
    """One row per player in `ts`: identity + previous-season summary.
    Everything here was known at `current_matchday`."""
    votes = _season_votes(conno, current_season, current_matchday)
    stats = _season_stats(conno, current_season)
    prev = _season_stats(conno, previous_season(current_season))
    if prev.empty:
        print(f"[dataset] WARNING: no stats for {previous_season(current_season)} - prev_* all NaN")

    out = pd.DataFrame(index=pd.Index(ts["player_id"].unique(), name="player_id"))
    # name/role: fixed for the season before it starts. Team = latest team
    # seen up to current_matchday (NOT the end-of-season team, which
    # would leak January transfers); for players not fielded yet, the
    # `stats` team - safe given how roster() picks them.
    latest = votes.sort_values("matchday").groupby("player_id").last()
    out["name"] = latest["name"].combine_first(stats["name"])
    out["role_classic"] = latest["role"].combine_first(stats["role"])
    out["role_mantra"] = stats["role_mantra"].reindex(out.index)  # e.g. "Dc", "M;C" - ';' = several
    out["team"] = latest["team"].combine_first(stats["team"])
    # Starting price: set before the season, so known at any matchday.
    out["price_initial"] = _season_prices(conno, current_season)["price_initial"].reindex(out.index)
    out.insert(0, "season", current_season)
    out.insert(1, "current_matchday", current_matchday)

    # Previous league: Serie A if in last season's stats, else the league
    # from last season's Euroleghe prices, else unknown.
    prev_prices = _season_prices(conno, previous_season(current_season))
    out["prev_league"] = pd.Series("Serie A", index=prev.index).combine_first(prev_prices["league"])
    return out.join(prev[["team"] + SUMMARY_COLS].add_prefix("prev_"))


def _so_far(ts):
    """Current-season summary over matchdays 1..current_matchday only,
    same definitions as `stats`: averages over rated matches (S.V.
    excluded), event counts over every match played incl. S.V."""
    g = ts.groupby("player_id")
    out = pd.DataFrame({
        "so_far_appearances": g["played"].sum(),
        "so_far_sv": g["sv"].sum(),
        "so_far_avg_vote": g["vote"].mean(),
        "so_far_fantamedia": g["fantavote"].mean(),
    })
    for col in EVENT_COLS:
        out[f"so_far_{col}"] = g[col].sum()  # sum of all-NaN -> 0: none so far
    return out


def _sorted(df):
    return df.reset_index().sort_values(["role_classic", "name"], key=_role_key).reset_index(drop=True)


def build_dataset(conno, current_season, current_matchday):
    """Wide TRAINING table, one row per player - see module docstring.
    Includes target_* (full-season outcome), so don't feed it to a model
    as-is at prediction time; for "what do we know so far" use snapshot()."""
    if not 1 <= current_matchday <= LAST_MATCHDAY:
        raise ValueError(f"current_matchday must be in 1..{LAST_MATCHDAY}, got {current_matchday}")

    ts = season_timeseries(conno, current_season, current_matchday)
    out = _known_players(conno, current_season, current_matchday, ts)

    md_cols = ["played", "sv", "vote", "fantavote"] + EVENT_COLS
    wide = ts.pivot(index="player_id", columns="matchday", values=md_cols)
    wide.columns = [f"md{md:02d}_{col}" for col, md in wide.columns]
    wide = wide[[f"md{md:02d}_{c}" for md in range(1, current_matchday + 1) for c in md_cols]]
    out = out.join(wide)

    target = _season_stats(conno, current_season)[SUMMARY_COLS].add_prefix("target_")
    if not season_is_complete(conno, current_season):
        target = target.iloc[0:0]  # partial-season stats are not a target
    out = out.join(target)
    for col in (f"target_{c}" for c in SUMMARY_COLS):
        if col not in out.columns:
            out[col] = np.nan

    return _sorted(out)


def snapshot(current_season, current_matchday):
    """What do we know about every player right after `current_matchday`?
    Opens and closes the DB itself. Returns (players, timeseries):

    players     one row per player in Serie A at that point (roster()):
                season, current_matchday, name, role, team, prev_* (last
                season, NaN if not in Serie A), so_far_* (this season,
                matchdays 1..current_matchday only)
    timeseries  one row per (player, matchday 1..current_matchday), see
                season_timeseries()

    No target_* and nothing from after current_matchday - the same call
    on a past season gives exactly what you'd have had live."""
    with loader.connect() as conno:
        return _snapshot(conno, current_season, current_matchday)


def _check_matchday(conno, current_season, current_matchday):
    if not 1 <= current_matchday <= LAST_MATCHDAY:
        raise ValueError(f"current_matchday must be in 1..{LAST_MATCHDAY}, got {current_matchday}")
    latest = last_matchday(conno, current_season)
    if current_matchday > latest:
        raise ValueError(
            f"{current_season}: only matchdays 1..{latest} downloaded, asked for {current_matchday}"
        )


def _snapshot(conno, current_season, current_matchday):
    _check_matchday(conno, current_season, current_matchday)
    ts = season_timeseries(conno, current_season, current_matchday)
    players = _sorted(_known_players(conno, current_season, current_matchday, ts).join(_so_far(ts)))
    order = {pid: i for i, pid in enumerate(players["player_id"])}
    ts = ts.sort_values(["player_id", "matchday"], key=lambda s: s.map(order) if s.name == "player_id" else s)
    return players, ts.reset_index(drop=True)


NEXT_COLS = ["played", "sv", "vote", "fantavote"] + EVENT_COLS


def _unseen(conno, current_season, current_matchday, player_ids):
    """Outcomes for `player_ids` after current_matchday, one row each in
    the same order:

    next_*      matchday current_matchday+1, same definitions as the time
                series (played=0 + NaN vote if he didn't play). All NaN
                if that matchday isn't downloaded yet or N = 38.
    season_*    official full-season summary from `stats`. All NaN if the
                season isn't finished, or the player isn't in the
                end-of-season list (left Serie A mid-season).
    """
    y = pd.DataFrame(index=pd.Index(player_ids, name="player_id"))

    next_md = current_matchday + 1
    if next_md <= last_matchday(conno, current_season):
        votes = _season_votes(conno, current_season, next_md)
        nxt = votes[votes["matchday"] == next_md].set_index("player_id")[NEXT_COLS]
        y = y.join(nxt.add_prefix("next_"))
        y[["next_played", "next_sv"]] = y[["next_played", "next_sv"]].fillna(0).astype(int)
    else:
        for col in NEXT_COLS:
            y[f"next_{col}"] = np.nan

    if season_is_complete(conno, current_season):
        y = y.join(_season_stats(conno, current_season)[SUMMARY_COLS].add_prefix("season_"))
    else:
        for col in SUMMARY_COLS:
            y[f"season_{col}"] = np.nan
    y.insert(0, "current_matchday", current_matchday)
    y.insert(0, "season", current_season)
    return y.reset_index()


def xy(current_season, current_matchday):
    """X and Y for a prediction model at `current_matchday`. Returns
    (players, timeseries, y):

    players, timeseries   exactly snapshot() - the features, nothing
                          unseen
    y                     the unseen outcomes (see _unseen()), one row per
                          player, same order as `players`

    Keep y separate from the features: nothing in it may be used as input.
    """
    with loader.connect() as conno:
        players, ts = _snapshot(conno, current_season, current_matchday)
        y = _unseen(conno, current_season, current_matchday, players["player_id"])
    return players, ts, y


def _role_key(s):
    if s.name == "role_classic":
        return s.map({"P": 0, "D": 1, "C": 2, "A": 3})
    return s


def build(current_season, current_matchday):
    with loader.connect() as conno:
        return build_dataset(conno, current_season, current_matchday)
