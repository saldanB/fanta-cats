"""Player-level encoding of the columns snapshot() leaves raw: previous
league and club, missing-data flags, per-match rates, this season vs last.

    X, X_ts = dataset.snapshot("2023-24", 10)
    player_X = PlayerEncoder().transform(X, X_ts)     # one row per X row

Columns (all prefixed player_):

    prev_league_*           one-hot of prev_league over team.LEAGUES, plus
    prev_league_unknown     1 when prev_league is NaN (youth / minor league)
    new_to_serie_a          not in Serie A last season
    changed_club            in Serie A last season, at a different club
    prev_missing            no previous-season stats (NaN prev_*)
    price_missing           no starting price (outside the Euroleghe pool)
    so_far_missing          not rated yet this season (NaN so_far averages)
    prev_app_rate           prev_appearances / 38
    prev_<event>_pg         last season per appearance: goals (incl.
                            penalties), assists, cards, penalties taken,
                            goals conceded (keepers)
    prev_bonus_pg           prev_fantamedia - prev_avg_vote: bonus/malus
                            per match on top of the vote
    so_far_app_rate         so_far_appearances / current_matchday
    so_far_<event>_pg       same events per match on the pitch so far
                            (rated + S.V. matches)
    so_far_bonus_pg         so_far_fantamedia - so_far_avg_vote
    delta_avg_vote, delta_fantamedia, delta_app_rate
                            this season so far minus last season
    old_club_*              last season's strength of last season's club
                            (team.TeamEncoder's team_prev_* metrics), NaN
                            if he wasn't in Serie A
    club_step_*             current club's last-season strength minus the
                            old club's: > 0 = moved to a stronger team
                            (0 if he stayed, NaN if either is unknown)

Rates are NaN when their denominator is 0; the *_missing flags say why.
"""

import pandas as pd

from .. import dataset
from .team import LEAGUES, METRICS, TeamEncoder, _slug

PREFIX = "player_"
# (output name, numerator column(s) in X)
PREV_EVENTS = {
    "goals": ["prev_goals"],  # stats goals already include penalties
    "assists": ["prev_assists"],
    "yellow_cards": ["prev_yellow_cards"],
    "red_cards": ["prev_red_cards"],
    "penalties_taken": ["prev_penalties_taken"],
    "goals_conceded": ["prev_goals_conceded"],
}
SO_FAR_EVENTS = {
    "goals": ["so_far_goals", "so_far_penalties_scored"],  # votes goals exclude penalties
    "assists": ["so_far_assists"],
    "yellow_cards": ["so_far_yellow_cards"],
    "red_cards": ["so_far_red_cards"],
    "penalties_taken": ["so_far_penalties_scored", "so_far_penalties_missed"],
    "goals_conceded": ["so_far_goals_conceded"],
}


def _ratio(num, den):
    return (num / den.where(den > 0)).astype(float)


class PlayerEncoder:
    def transform(self, X, X_ts=None, team_prev=None):
        """One row per row of X (same order): player_id + player_* columns.
        `team_prev`: TeamEncoder().load_previous(X), loaded when None."""
        if team_prev is None:
            team_prev = TeamEncoder().load_previous(X)
        X = X.reset_index(drop=True)
        f = pd.DataFrame(index=X.index)

        for league in LEAGUES:
            f[f"prev_league_{_slug(league)}"] = (X["prev_league"] == league).astype(int)
        f["prev_league_unknown"] = X["prev_league"].isna().astype(int)
        f["new_to_serie_a"] = (X["prev_league"] != "Serie A").astype(int)
        f["changed_club"] = (X["prev_team"].notna() & (X["prev_team"] != X["team"])).astype(int)

        f["prev_missing"] = X["prev_appearances"].isna().astype(int)
        f["price_missing"] = X["price_initial"].isna().astype(int)
        f["so_far_missing"] = (X["so_far_appearances"] == 0).astype(int)

        f["prev_app_rate"] = X["prev_appearances"] / dataset.LAST_MATCHDAY
        for name, cols in PREV_EVENTS.items():
            f[f"prev_{name}_pg"] = _ratio(X[cols].sum(axis=1, min_count=1), X["prev_appearances"])
        f["prev_bonus_pg"] = X["prev_fantamedia"] - X["prev_avg_vote"]

        on_pitch = X["so_far_appearances"] + X["so_far_sv"]
        f["so_far_app_rate"] = X["so_far_appearances"] / X["current_matchday"]
        for name, cols in SO_FAR_EVENTS.items():
            f[f"so_far_{name}_pg"] = _ratio(X[cols].sum(axis=1), on_pitch)
        f["so_far_bonus_pg"] = X["so_far_fantamedia"] - X["so_far_avg_vote"]

        f["delta_avg_vote"] = X["so_far_avg_vote"] - X["prev_avg_vote"]
        f["delta_fantamedia"] = X["so_far_fantamedia"] - X["prev_fantamedia"]
        f["delta_app_rate"] = f["so_far_app_rate"] - f["prev_app_rate"]

        stayed = X["prev_team"] == X["team"]
        for m in METRICS:
            col = f"team_prev_{m}"
            strength = team_prev[col] if col in team_prev else pd.Series(dtype=float)
            old = X["prev_team"].map(strength).astype(float)
            new = X["team"].map(strength).astype(float)
            f[f"old_club_{m}"] = old
            f[f"club_step_{m}"] = (new - old).where(~stayed, 0.0)

        f = f.add_prefix(PREFIX)
        return pd.concat([X[["player_id"]], f], axis=1)

