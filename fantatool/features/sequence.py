"""Sequence encoding of X_ts for sequence models (recurrent nets,
transformers, 1-D CNNs): fixed-length numeric arrays plus masks.

    X, X_ts = dataset.snapshot("2023-24", 10)
    seq = SequenceEncoder().transform(X, X_ts)
    seq["values"]    # (n_players, 38, n_features) float32
    seq["mask"]      # (n_players, 38) 1 = on the pitch (rated or S.V.)
    seq["observed"]  # (n_players, 38) 1 = matchday <= current_matchday
    seq["player_id"] # row order, same as X

Step t is matchday t+1. Matchdays after current_matchday are padding
(observed = 0, values = 0) - a model must use `observed`, never peek
at them. NaN (not rated, absent) is also stored as 0, with `played`,
`sv` and `mask` telling the model which zeros are real.
"""

import numpy as np

from .. import dataset

DEFAULT_FEATURES = ["played", "sv", "vote", "fantavote"] + dataset.EVENT_COLS


class SequenceEncoder:
    def __init__(self, features=DEFAULT_FEATURES, length=dataset.LAST_MATCHDAY):
        self.features = list(features)
        self.length = length

    def transform(self, X, X_ts):
        order = X["player_id"].to_numpy()
        n_md = int(X_ts["matchday"].max()) if len(X_ts) else 0
        if n_md > self.length:
            raise ValueError(f"X_ts reaches matchday {n_md} > length {self.length}")

        values = np.zeros((len(order), self.length, len(self.features)), dtype=np.float32)
        for k, col in enumerate(self.features):
            m = X_ts.pivot(index="player_id", columns="matchday", values=col).reindex(order)
            values[:, :n_md, k] = np.nan_to_num(m.to_numpy(dtype=float), nan=0.0)

        on = X_ts.assign(on=(X_ts["played"] == 1) | (X_ts["sv"] == 1))
        on = on.pivot(index="player_id", columns="matchday", values="on").reindex(order)
        mask = np.zeros((len(order), self.length), dtype=np.int8)
        mask[:, :n_md] = on.to_numpy(dtype=bool)
        observed = np.zeros((len(order), self.length), dtype=np.int8)
        observed[:, :n_md] = 1

        return {
            "player_id": order,
            "values": values,
            "mask": mask,
            "observed": observed,
            "features": self.features,
        }
