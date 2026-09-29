"""Team encoding: replaces the raw team name with numbers describing the
team, all known at the snapshot's matchday.

    X, X_ts = dataset.snapshot("2023-24", 10)
    team_X = TeamEncoder().transform(X, X_ts)     # one row per X row

Columns (per player, from his current team):

    league_*                one-hot league of the team, fixed list LEAGUES
    team_matches            matchdays the team has played so far
    team_gf_pg              goals scored per match so far (goals + penalties)
    team_ga_pg              goals conceded per match so far (keepers' Gs)
    team_avg_vote           mean vote of the team's rated players so far
    team_avg_fantavote      same, fantavote
    team_prev_*             the same four over the whole of last season.
                            NaN if promoted
    team_promoted           1 if the team wasn't in Serie A last season
    team_blend_*            so-far value shrunk towards last season:
                            (n * so_far + k * prev) / (n + k), n = matches
                            played, k = prior_weight. Promoted teams use the
                            average of last season's 3 worst teams (by goal
                            difference) as their prior.

Everything "so far" comes from X_ts (matchdays 1..N only). Last season
is computed the same way from last season's matchday votes, so every
goal counts for the club the player was at that day. Only if last
season's votes aren't complete does it fall back on the official
`stats`, which credit a January signing's whole season to his final club
(e.g. Vlahovic 2021-22: 17 Fiorentina goals counted for Juventus).
"""

import numpy as np
import pandas as pd

from .. import dataset, loader

LEAGUES = ["Serie A", "Premier League", "Liga", "Bundesliga", "Ligue 1"]
METRICS = ["gf_pg", "ga_pg", "avg_vote", "avg_fantavote"]
N_PROMOTED_PROXY = 3  # promoted teams' prior = mean of last season's bottom N


def single_season(X):
    seasons = X["season"].unique()
    if len(seasons) != 1:
        raise ValueError("X holds several seasons - encode one snapshot at a time")
    return seasons[0]


def _slug(league):
    return league.lower().replace(" ", "_")


def _weighted_mean(values, weights):
    w = weights.where(values.notna(), 0)
    return (values.fillna(0) * w).sum() / w.sum() if w.sum() > 0 else np.nan


class TeamEncoder:
    def __init__(self, prior_weight=5):
        self.prior_weight = prior_weight

    def so_far(self, X, X_ts):
        """Per-team strength over the snapshot's matchdays, indexed by team."""
        role = X.set_index("player_id")["role_classic"]
        rows = X_ts[X_ts["goals"].notna()].copy()  # player was in that matchday's file
        rows["is_keeper"] = rows["player_id"].map(role) == "P"
        rows["gf"] = rows["goals"] + rows["penalties_scored"]
        rows["ga"] = rows["goals_conceded"].where(rows["is_keeper"], 0)
        g = rows.groupby("team")
        matches = g["matchday"].nunique()
        return pd.DataFrame({
            "team_matches": matches,
            "team_gf_pg": g["gf"].sum() / matches,
            "team_ga_pg": g["ga"].sum() / matches,
            "team_avg_vote": g["vote"].mean(),
            "team_avg_fantavote": g["fantavote"].mean(),
        })

    def previous_from_snapshot(self, prev_X, prev_ts):
        """Last season's per-team strength from its full snapshot
        (dataset.snapshot(prev_season, 38)) - the preferred source."""
        return self.so_far(prev_X, prev_ts).drop(columns="team_matches").add_prefix("prev_").rename(
            columns=lambda c: c.replace("prev_team_", "team_prev_")
        )

    def previous_from_stats(self, prev_stats):
        """Fallback: per-team strength from official season stats
        (dataset.season_stats output), grouped by end-of-season team."""
        if prev_stats.empty:
            return pd.DataFrame(columns=[f"team_prev_{m}" for m in METRICS])
        rows = []
        for team, t in prev_stats.groupby("team"):
            keepers = t[t["role"] == "P"]
            rows.append({
                "team": team,
                "team_prev_gf_pg": t["goals"].sum() / dataset.LAST_MATCHDAY,
                "team_prev_ga_pg": keepers["goals_conceded"].sum() / dataset.LAST_MATCHDAY,
                "team_prev_avg_vote": _weighted_mean(t["avg_vote"], t["appearances"]),
                "team_prev_avg_fantavote": _weighted_mean(t["fantamedia"], t["appearances"]),
            })
        return pd.DataFrame(rows).set_index("team")

    def team_table(self, X, X_ts, prev=None):
        """One row per team in X: every team_* column plus league.
        `prev`: last season per team (previous_from_snapshot/_from_stats
        output); loaded from the DB when None."""
        if prev is None:
            prev = self.load_previous(X)
        teams = pd.Index(sorted(X["team"].dropna().unique()), name="team")
        table = pd.DataFrame(index=teams)
        table["league"] = "Serie A"  # X only ever holds Serie A squads
        table = table.join(self.so_far(X, X_ts)).join(prev)
        table["team_matches"] = table["team_matches"].fillna(0).astype(int)
        table["team_promoted"] = table["team_prev_gf_pg"].isna().astype(int)

        if len(prev):
            gd = prev["team_prev_gf_pg"] - prev["team_prev_ga_pg"]
            promoted_prior = prev.loc[gd.nsmallest(N_PROMOTED_PROXY).index].mean()
        else:
            promoted_prior = pd.Series(np.nan, index=[f"team_prev_{m}" for m in METRICS])
        n, k = table["team_matches"], self.prior_weight
        for m in METRICS:
            prior = table[f"team_prev_{m}"].fillna(promoted_prior[f"team_prev_{m}"])
            now = table[f"team_{m}"]
            table[f"team_blend_{m}"] = ((n * now.fillna(0) + k * prior) / (n + k)).where(n + k > 0)
            table.loc[now.isna(), f"team_blend_{m}"] = prior  # no match yet -> prior only

        for league in LEAGUES:
            table[f"league_{_slug(league)}"] = (table["league"] == league).astype(int)
        return table

    def transform(self, X, X_ts, prev=None):
        """One row per row of X (same order): player_id + team features."""
        table = self.team_table(X, X_ts, prev).drop(columns="league")
        out = X[["player_id", "team"]].join(table, on="team").drop(columns="team")
        return out.reset_index(drop=True)

    def load_previous(self, X):
        """Last season's per-team table for X's season, from the DB:
        votes-based when last season is complete, else official stats."""
        prev_season = dataset.previous_season(single_season(X))
        with loader.connect() as conno:
            if dataset.season_is_complete(conno, prev_season):
                return self.previous_from_snapshot(*dataset._snapshot(conno, prev_season, dataset.LAST_MATCHDAY))
            return self.previous_from_stats(dataset.season_stats(conno, prev_season))
