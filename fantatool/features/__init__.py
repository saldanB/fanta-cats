"""Feature encoders on top of dataset.snapshot() / dataset.xy() output.

dataset.py decides what is *known* at a matchday (leak-free raw data);
modules here only turn that into model inputs, so they never read
anything dataset.py didn't hand them - apart from finished seasons.

Every tabular encoder has transform(X, X_ts) -> DataFrame with
player_id + its own prefixed columns, one row per X row in X's order:

    team.TeamEncoder        team_*, league_*
    role.RoleEncoder        role_classic_*, role_mantra_*
    player.PlayerEncoder    player_*
    squad.SquadEncoder      squad_*
    form.FormEncoder        form_*

sequence.SequenceEncoder returns arrays instead, for sequence models.

    features = encode_all(X, X_ts)                     # all of them
    features = encode_all(X, X_ts, ["team", "form"])   # a subset
"""

import pandas as pd

from .form import FormEncoder
from .player import PlayerEncoder
from .role import RoleEncoder
from .squad import SquadEncoder
from .team import TeamEncoder

ENCODERS = {
    "team": TeamEncoder,
    "role": RoleEncoder,
    "player": PlayerEncoder,
    "squad": SquadEncoder,
    "form": FormEncoder,
}

# Raw X columns that the encoders replace (labels, not model inputs).
RAW_LABELS = ["team", "role_classic", "role_mantra", "prev_league", "prev_team"]
IDENTIFIERS = ["player_id", "season", "current_matchday", "name"]


def encode_all(X, X_ts, encoders=None, keep_raw=True):
    """X's numeric columns + the chosen encoders' columns, one row per X
    row in X's order. `encoders`: names from ENCODERS (default all), or
    already-built encoder instances. keep_raw=False drops X's own numeric
    columns (prev_*, so_far_*, price_initial) and keeps only encodings."""
    if encoders is None:
        encoders = list(ENCODERS)
    encoders = [ENCODERS[e]() if isinstance(e, str) else e for e in encoders]

    X = X.reset_index(drop=True)
    base = X.drop(columns=RAW_LABELS)
    if not keep_raw:
        base = base[IDENTIFIERS]

    team_prev = None
    out = base
    for enc in encoders:
        if isinstance(enc, PlayerEncoder):
            if team_prev is None:
                team_prev = TeamEncoder().load_previous(X)
            part = enc.transform(X, X_ts, team_prev=team_prev)
        elif isinstance(enc, TeamEncoder):
            if team_prev is None:
                team_prev = enc.load_previous(X)
            part = enc.transform(X, X_ts, prev=team_prev)
        else:
            part = enc.transform(X, X_ts)
        out = out.merge(part, on="player_id", how="left", validate="one_to_one")
    return out


__all__ = [
    "ENCODERS", "IDENTIFIERS", "RAW_LABELS", "encode_all",
    "FormEncoder", "PlayerEncoder", "RoleEncoder", "SquadEncoder", "TeamEncoder",
]
