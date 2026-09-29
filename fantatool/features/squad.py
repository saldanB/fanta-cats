"""Squad context: where a player stands inside his current team - the
competition for his place and his share of the team's output.

    X, X_ts = dataset.snapshot("2023-24", 10)
    squad_X = SquadEncoder().transform(X, X_ts)     # one row per X row

Columns (all prefixed squad_):

    role_mates              teammates with the same classic role
    rank_<key>              rank among those teammates + himself (1 = best)
    rank_pct_<key>          same, as a 0..1 fraction (0 = best), comparable
                            across squads of different sizes
                            key in: prev_fantamedia, price_initial,
                            so_far_fantamedia, so_far_appearances
                            (unknown values rank last)
    share_team_matches      matches on the pitch (rated + S.V.) / team
                            matches so far
    share_team_goals        his goals (incl. penalties) / team goals so far
    share_team_assists      his assists / team assists so far
    share_team_penalties    penalties he took / team penalties taken so far
    prev_pen_share_old_club last season: penalties he took / all penalties
                            taken at his club (official stats)
    prev_pen_share_squad    last season's penalties taken / the sum over his
                            CURRENT teammates: who in this squad is the
                            likely taker, from what's known pre-season
    prev_pen_top_squad      1 if he's that squad's top prior penalty taker

Shares are NaN when the team total is 0.
"""

import pandas as pd

from .. import dataset, loader
from .team import single_season

PREFIX = "squad_"
RANK_KEYS = ["prev_fantamedia", "price_initial", "so_far_fantamedia", "so_far_appearances"]


def _share(num, den):
    return (num / den.where(den > 0)).astype(float)


class SquadEncoder:
    def transform(self, X, X_ts, prev_stats=None):
        """One row per row of X (same order): player_id + squad_* columns.
        `prev_stats`: dataset.season_stats of last season, loaded when None."""
        if prev_stats is None:
            with loader.connect() as conno:
                prev_stats = dataset.season_stats(conno, dataset.previous_season(single_season(X)))
        X = X.reset_index(drop=True)
        f = pd.DataFrame(index=X.index)

        group = [X["team"], X["role_classic"]]
        f["role_mates"] = X.groupby(group)["player_id"].transform("size") - 1
        for key in RANK_KEYS:
            rank = X[key].groupby(group).rank(ascending=False, method="min", na_option="bottom")
            size = f["role_mates"] + 1
            f[f"rank_{key}"] = rank
            f[f"rank_pct_{key}"] = ((rank - 1) / (size - 1).where(size > 1)).fillna(0.0)

        f = f.join(self._shares(X, X_ts))

        pens = prev_stats.groupby("team")["penalties_taken"].sum() if len(prev_stats) else pd.Series(dtype=float)
        f["prev_pen_share_old_club"] = _share(X["prev_penalties_taken"], X["prev_team"].map(pens))
        taken = X["prev_penalties_taken"].fillna(0)
        squad_total = taken.groupby(X["team"]).transform("sum")
        f["prev_pen_share_squad"] = _share(taken, squad_total)
        top = taken.groupby(X["team"]).transform("max")
        f["prev_pen_top_squad"] = ((taken == top) & (top > 0)).astype(int)

        f = f.add_prefix(PREFIX)
        return pd.concat([X[["player_id"]], f], axis=1)

    @staticmethod
    def _shares(X, X_ts):
        rows = X_ts[X_ts["goals"].notna()].copy()  # in that matchday's file
        rows["gf"] = rows["goals"] + rows["penalties_scored"]
        rows["pens"] = rows["penalties_scored"] + rows["penalties_missed"]
        # team totals by the club of each matchday; the player's own by his
        # rows at his CURRENT club (X.team) so the two are comparable
        team_tot = rows.groupby("team").agg(
            matches=("matchday", "nunique"), gf=("gf", "sum"), assists=("assists", "sum"), pens=("pens", "sum")
        )
        at_club = rows.merge(X[["player_id", "team"]], on=["player_id", "team"])
        own = at_club.groupby("player_id").agg(
            matches=("matchday", "nunique"), gf=("gf", "sum"), assists=("assists", "sum"), pens=("pens", "sum")
        )
        own = own.reindex(X["player_id"]).fillna(0).set_index(X.index)
        tot = team_tot.reindex(X["team"]).set_index(X.index)
        return pd.DataFrame({
            "share_team_matches": _share(own["matches"], tot["matches"]),
            "share_team_goals": _share(own["gf"], tot["gf"]),
            "share_team_assists": _share(own["assists"], tot["assists"]),
            "share_team_penalties": _share(own["pens"], tot["pens"]),
        })
