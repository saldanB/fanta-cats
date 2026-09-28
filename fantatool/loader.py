"""Read-only query helpers against output/fanta.db, meant as the first
layer of a data-loading pipeline (fanta.py only downloads/merges - this is
for code that then *consumes* the merged data, e.g. for analysis or ML).
"""

import sqlite3

import pandas as pd

from . import config


def connect():
    return sqlite3.connect(config.DB_FILE)


def find_player_id(conn, player):
    """Resolve `player` to a player_id. Accepts an existing player_id
    (passed through as-is) or a (partial) name to look up in `players`.
    Raises ValueError if the name matches zero or more than one player."""
    if isinstance(player, str) and (player.startswith("FC") or player.startswith("NM")):
        return player

    matches = pd.read_sql(
        "SELECT player_id, canonical_name FROM players WHERE canonical_name LIKE ?",
        conn,
        params=[f"%{player}%"],
    )
    if matches.empty:
        raise ValueError(f"no player found matching {player!r}")
    if len(matches) > 1:
        names = ", ".join(f"{r.canonical_name} ({r.player_id})" for r in matches.itertuples())
        raise ValueError(f"{player!r} is ambiguous, matches: {names} - pass one of those player_ids instead")
    return matches.iloc[0]["player_id"]


def player_matches(conn, player, vote_source="Fantacalcio"):
    """All matchday-level rows for one player: season, league, matchday,
    team, role, vote (+ bonus/malus events), one row per matchday.

    `vote` is NULL for a matchday where the player was an unused
    substitute / not rated - those rows are still included, since they're
    still "in the squad that matchday".

    fantacalcio.it publishes 3 parallel vote sources per matchday
    (Fantacalcio/Statistico/Italia); vote_source='Fantacalcio' (the one
    used for official scoring) is the default so you don't get 3x rows.
    Pass vote_source=None to get all of them (e.g. to compare sources).
    """
    player_id = find_player_id(conn, player)

    query = """
        SELECT season, league, CAST(matchday AS INTEGER) AS matchday, team, role,
               vote, vote_provisional, goals, assists, yellow_cards, red_cards,
               own_goals, penalties_scored, penalties_missed, penalties_saved
        FROM votes
        WHERE player_id = ? AND role != 'ALL'
    """
    params = [player_id]
    if vote_source is not None:
        query += " AND vote_source = ?"
        params.append(vote_source)

    df = pd.read_sql(query, conn, params=params)
    return df.sort_values(["season", "matchday"]).reset_index(drop=True)
