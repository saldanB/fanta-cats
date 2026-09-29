"""Role encoding: the classic role and the Mantra role as fixed-width 0/1
blocks. Both are set before the season starts, so known at any matchday.

    X, X_ts = dataset.snapshot("2023-24", 10)
    role_X = RoleEncoder().transform(X)          # one row per X row

Columns:

    role_classic_*      one-hot of role_classic (P, D, C, A)
    role_mantra_*       MULTI-hot of role_mantra: a player holds 1-3 Mantra
                        roles ("M;C", "Ds;Dd;E"), each gets a 1

The two blocks are independent rather than nested: Mantra roles don't
map to one classic role (E is held by D and C players, W by A, C and D,
T and A by A and C). A player with no Mantra role (not in the season's
stats list) gets all zeros in that block. An unknown label raises, so a
change in fantacalcio.it's role set doesn't go unnoticed.
"""

import pandas as pd

CLASSIC = ["P", "D", "C", "A"]
# Goalkeeper -> defence -> midfield -> attack.
MANTRA = ["Por", "Dc", "Dd", "Ds", "B", "E", "M", "C", "T", "W", "A", "Pc"]
MANTRA_SEP = ";"


def _slug(label):
    return label.lower()


class RoleEncoder:
    def classic(self, roles):
        """One-hot of a Series of classic roles, same index."""
        unknown = set(roles.dropna()) - set(CLASSIC)
        if unknown:
            raise ValueError(f"unknown classic role(s): {sorted(unknown)}")
        return pd.DataFrame(
            {f"role_classic_{_slug(r)}": (roles == r).astype(int) for r in CLASSIC}, index=roles.index
        )

    def mantra(self, roles):
        """Multi-hot of a Series of ';'-joined Mantra roles, same index."""
        sets = roles.fillna("").map(lambda s: {r.strip() for r in s.split(MANTRA_SEP) if r.strip()})
        unknown = set().union(*sets) - set(MANTRA) if len(sets) else set()
        if unknown:
            raise ValueError(f"unknown Mantra role(s): {sorted(unknown)}")
        return pd.DataFrame(
            {f"role_mantra_{_slug(r)}": sets.map(lambda s, r=r: int(r in s)) for r in MANTRA}, index=roles.index
        )

    def transform(self, X, X_ts=None):
        """One row per row of X (same order): player_id + role features.
        X_ts is accepted for symmetry with the other encoders, unused."""
        out = pd.concat(
            [X[["player_id"]], self.classic(X["role_classic"]), self.mantra(X["role_mantra"])], axis=1
        )
        return out.reset_index(drop=True)
