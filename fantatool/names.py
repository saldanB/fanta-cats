"""Name normalization and fuzzy matching for cross-league/cross-season
player identity. These files never carry a birth date or a stable id, so
matching is name-based only - keep it conservative and route anything
ambiguous to needs_review.csv instead of guessing.
"""

import re
from difflib import SequenceMatcher

from unidecode import unidecode

_PUNCT_RE = re.compile(r"[^a-z0-9\s.]")
_SPACE_RE = re.compile(r"\s+")

AUTO_MATCH_THRESHOLD = 0.92
AUTO_MATCH_MARGIN = 0.05


def normalize(raw_name):
    s = unidecode(str(raw_name)).lower()
    s = _PUNCT_RE.sub("", s)
    s = _SPACE_RE.sub(" ", s).strip()
    return s


def tokens(normalized):
    return normalized.replace(".", " ").split()


def is_initial_form(normalized):
    toks = tokens(normalized)
    return len(toks) >= 2 and len(toks[0]) == 1


def similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()


def initial_form_matches(initial_name, full_name):
    """True if 'l martinez' plausibly refers to 'lautaro martinez'."""
    i_toks, f_toks = tokens(initial_name), tokens(full_name)
    if not i_toks or not f_toks:
        return False
    if i_toks[-1] != f_toks[-1]:
        return False
    return i_toks[0][0] == f_toks[0][0]


def best_match(normalized_name, registry):
    """registry: list of (player_id, set_of_known_normalized_variants).
    Returns (player_id, score, ambiguous_candidates) where
    ambiguous_candidates is a list of (player_id, score) for anything else
    close enough to be confusable with the winner.
    """
    scored = []
    for player_id, variants in registry:
        if normalized_name in variants:
            return player_id, 1.0, []
        best_for_player = 0.0
        for variant in variants:
            score = similarity(normalized_name, variant)
            if is_initial_form(normalized_name) and initial_form_matches(normalized_name, variant):
                score = max(score, 0.95)
            elif is_initial_form(variant) and initial_form_matches(variant, normalized_name):
                score = max(score, 0.95)
            best_for_player = max(best_for_player, score)
        scored.append((player_id, best_for_player))

    if not scored:
        return None, 0.0, []

    scored.sort(key=lambda x: x[1], reverse=True)
    top_id, top_score = scored[0]
    if top_score < AUTO_MATCH_THRESHOLD:
        return None, top_score, []

    close = [(pid, s) for pid, s in scored[1:] if top_score - s < AUTO_MATCH_MARGIN]
    return top_id, top_score, close
