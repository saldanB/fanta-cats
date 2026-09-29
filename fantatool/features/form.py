"""Form: tabular summaries of a player's matchday time series (X_ts), for
models that don't read sequences.

    X, X_ts = dataset.snapshot("2023-24", 10)
    form_X = FormEncoder(windows=(3, 5)).transform(X, X_ts)   # one row per X row

Columns (all prefixed form_). For each window W in `windows` - the last W
matchdays up to current_matchday (all of them if fewer) - and for "all":

    <W>_played_rate         share of matchdays rated
    <W>_sv_rate             share of matchdays on as S.V.
    <W>_vote_mean           mean vote over rated matches (NaN if none)
    <W>_fantavote_mean      mean fantavote over rated matches
    <W>_bonus_mean          mean (fantavote - vote): bonus/malus per match
    <W>_goals               goals incl. penalties (0 if none)
    <W>_assists             assists

Plus, over the whole series:

    last_played             rated at current_matchday
    last_vote, last_fantavote   at his most recent rated match
    since_last_played       matchdays since his last rated match (0 = rated
                            at current_matchday; NaN = never rated)
    streak_played           consecutive rated matchdays ending now
    streak_missed           consecutive unrated matchdays ending now
    fantavote_std/min/max   spread of his fantavotes (std needs >= 2)
    fantavote_trend         least-squares slope of fantavote per matchday
                            over rated matches (needs >= 2)
    fantavote_ewm           exponentially weighted mean over rated matches,
                            half-life `halflife` matches (recent weigh more)
    cards_rate              yellow + red cards per match on the pitch
"""

import warnings

import numpy as np
import pandas as pd

PREFIX = "form_"


def _matrix(ts, order, values):
    m = ts.pivot(index="player_id", columns="matchday", values=values)
    return m.reindex(order).to_numpy(dtype=float)


def _nanmean(a):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(a, axis=1)


def _last_true_index(mask):
    """Column index of the last True per row, -1 if none."""
    rev = mask[:, ::-1]
    has = rev.any(axis=1)
    return np.where(has, mask.shape[1] - 1 - rev.argmax(axis=1), -1)


def _trailing_run(mask):
    """Length of the run of True ending at the last column, per row."""
    rev = mask[:, ::-1]
    return np.where(rev.all(axis=1), mask.shape[1], (~rev).argmax(axis=1))


class FormEncoder:
    def __init__(self, windows=(3, 5), halflife=3):
        self.windows = tuple(windows)
        self.halflife = halflife

    def transform(self, X, X_ts):
        """One row per row of X (same order): player_id + form_* columns."""
        order = X["player_id"].to_numpy()
        ts = X_ts.copy()
        ts["gf"] = ts["goals"] + ts["penalties_scored"]
        ts["cards"] = ts["yellow_cards"] + ts["red_cards"]
        played = _matrix(ts, order, "played") == 1
        sv = _matrix(ts, order, "sv") == 1
        vote, fv = _matrix(ts, order, "vote"), _matrix(ts, order, "fantavote")
        gf, assists, cards = (_matrix(ts, order, c) for c in ("gf", "assists", "cards"))
        n_md = played.shape[1]
        f = {}

        for label, w in [(f"last{w}", w) for w in self.windows] + [("all", n_md)]:
            s = slice(max(0, n_md - w), n_md)
            f[f"{label}_played_rate"] = played[:, s].mean(axis=1)
            f[f"{label}_sv_rate"] = sv[:, s].mean(axis=1)
            f[f"{label}_vote_mean"] = _nanmean(vote[:, s])
            f[f"{label}_fantavote_mean"] = _nanmean(fv[:, s])
            f[f"{label}_bonus_mean"] = _nanmean(fv[:, s] - vote[:, s])
            f[f"{label}_goals"] = np.nansum(gf[:, s], axis=1)
            f[f"{label}_assists"] = np.nansum(assists[:, s], axis=1)

        last = _last_true_index(played)
        rows = np.arange(len(order))
        ever = last >= 0
        f["last_played"] = played[:, -1].astype(int)
        f["last_vote"] = np.where(ever, vote[rows, last], np.nan)
        f["last_fantavote"] = np.where(ever, fv[rows, last], np.nan)
        f["since_last_played"] = np.where(ever, n_md - 1 - last, np.nan)
        f["streak_played"] = _trailing_run(played)
        f["streak_missed"] = _trailing_run(~played)

        n_rated = played.sum(axis=1)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            f["fantavote_std"] = np.where(n_rated >= 2, np.nanstd(fv, axis=1), np.nan)
            f["fantavote_min"] = np.nanmin(fv, axis=1)
            f["fantavote_max"] = np.nanmax(fv, axis=1)
        f["fantavote_trend"] = self._trend(fv, n_rated)
        f["fantavote_ewm"] = self._ewm(fv, played)

        on_pitch = (played | sv).sum(axis=1)
        f["cards_rate"] = np.where(on_pitch > 0, np.nansum(cards, axis=1) / np.maximum(on_pitch, 1), np.nan)

        out = pd.DataFrame(f).add_prefix(PREFIX)
        out.insert(0, "player_id", order)
        return out

    @staticmethod
    def _trend(fv, n_rated):
        x = np.arange(1, fv.shape[1] + 1, dtype=float)
        mask = ~np.isnan(fv)
        xm = np.where(mask, x, np.nan)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            dx = xm - np.nanmean(xm, axis=1, keepdims=True)
            dy = fv - np.nanmean(fv, axis=1, keepdims=True)
            num, den = np.nansum(dx * dy, axis=1), np.nansum(dx * dx, axis=1)
        return np.where((n_rated >= 2) & (den > 0), num / np.where(den > 0, den, 1), np.nan)

    def _ewm(self, fv, played):
        # weight of a rated match = 0.5 ** (rated matches after it / halflife)
        after = np.cumsum(played[:, ::-1], axis=1)[:, ::-1] - played
        w = np.where(played, 0.5 ** (after / self.halflife), 0.0)
        tot = w.sum(axis=1)
        return np.where(tot > 0, np.nansum(w * np.nan_to_num(fv), axis=1) / np.where(tot > 0, tot, 1), np.nan)
