"""Step 3: read every downloaded .xlsx, tag it with league/season, resolve
player identity across leagues and seasons, and write fanta.db + CSV
exports + fanta_summary.xlsx.

Column names in the source files aren't fully known until you've actually
probed/downloaded (see README) - COLUMN_ALIASES below is deliberately
generous. Any source column that doesn't match a known alias is kept
as-is (prefixed raw__) rather than dropped, so nothing silently disappears.
"""

import csv
import re
import sqlite3

import pandas as pd

from . import config
from .names import best_match, initial_form_matches, is_initial_form, normalize, similarity

FILENAME_RE = re.compile(r"^(stats|prices|votes_euroleghe|votes)_(.+?)(?:_g\d+)?\.xlsx$")
MATCHDAY_SUFFIX_RE = re.compile(r"_g(\d+)\.xlsx$")

COLUMN_ALIASES = {
    # "Id" (stats/prices) and "Cod." (votes) are the SAME internal
    # fantacalcio.it player id (confirmed: e.g. Gosens = 2160 in all three).
    # This is the primary key player identity resolution uses - see
    # _resolve_players below.
    "source_id": ["id", "cod."],
    "name": ["nome", "giocatore", "calciatore"],
    "team": ["squadra", "team"],
    "role": ["r", "ruolo"],
    "role_detail": ["rm"],
    "appearances": ["pv", "presenze"],
    "avg_vote": ["mv", "media voto", "media"],
    "fantamedia": ["fm", "fantamedia"],
    "goals": ["gf", "gol fatti", "reti fatte"],
    "goals_conceded": ["gs", "gol subiti", "reti subite"],
    "assists": ["ass", "assist"],
    "yellow_cards": ["amm", "ammonizioni"],
    "red_cards": ["esp", "espulsioni"],
    "own_goals": ["au", "autogol", "autogoal"],
    "penalties_scored": ["r+", "rigori segnati"],
    "penalties_missed": ["r-", "rigori sbagliati", "rs"],
    "penalties_saved": ["rp", "rigori parati"],
    "penalties_taken": ["rc", "rigori calciati"],
    "vote": ["voto"],
    "fantavoto": ["fantavoto"],
    "price_current": ["qt.a"],
    "price_initial": ["qt.i"],
    "price_diff": ["diff."],
    "price_current_mantra": ["qt.a m"],
    "price_initial_mantra": ["qt.i m"],
    "price_diff_mantra": ["diff.m"],
    "market_value": ["fvm"],
    "market_value_mantra": ["fvm m"],
    # "Rf" and "Gdv"/"Gdp" (votes files) aren't confidently identified -
    # left unmapped (kept as raw__rf / raw__gdv / raw__gdp) rather than
    # risk a wrong label. Gdv/Gdp are probably "gol decisivo vittoria/
    # pareggio" (goal that decided a win/draw) but that's a guess.
}

# Columns _load_all()/_parse_votes_workbook() inject directly (not read
# from a raw header) - already using their final name, never renamed.
INJECTED_COLUMNS = {"team", "matchday", "vote_provisional", "vote_source"}


def _canonical_columns(df):
    lookup = {}
    for col in df.columns:
        if col in INJECTED_COLUMNS:
            continue
        key = str(col).strip().lower()
        for canonical, aliases in COLUMN_ALIASES.items():
            if key in aliases:
                lookup[col] = canonical
                break
        else:
            lookup[col] = f"raw__{key}"
    return df.rename(columns=lookup)


def _discover_raw_files():
    files = []
    log_by_path = {}
    if config.DOWNLOAD_LOG_FILE.exists():
        with open(config.DOWNLOAD_LOG_FILE, newline="") as f:
            for row in csv.DictReader(f):
                if row.get("saved_path"):
                    log_by_path[row["saved_path"]] = row

    for path in sorted(config.DATA_DIR.glob("*/*.xlsx")):
        key = str(path)
        if key in log_by_path:
            row = log_by_path[key]
            files.append(
                {
                    "path": path,
                    "file_type": row["file_type"],
                    "league": row["league"],
                    "season": row["season"],
                }
            )
            continue
        m = FILENAME_RE.match(path.name)
        if not m:
            print(f"[merge] WARNING: can't parse {path.name}, skipping")
            continue
        file_type, season_slug = m.groups()
        league_slug = path.parent.name
        print(
            f"[merge] WARNING: {path.name} not in {config.DOWNLOAD_LOG_FILE}, "
            "guessing league/season from path - check the merged output"
        )
        files.append(
            {"path": path, "file_type": file_type, "league": league_slug, "season": season_slug}
        )
    return files


def _pick_sheet(xls):
    for name in xls.sheet_names:
        if name.strip().lower() == "tutti":
            return name
    return xls.sheet_names[0]


def _detect_header_row(xls, sheet, max_scan=5):
    """fantacalcio.it exports start with a title banner row above the real
    header - find the row that actually contains 'Nome' and use that."""
    preview = xls.parse(sheet, header=None, nrows=max_scan)
    for i, row in preview.iterrows():
        cells = [str(c).strip().lower() for c in row.tolist()]
        if "nome" in cells:
            return i
    return 0


def _parse_vote_value(raw):
    """'6*' -> (6.0, True); '7' -> (7.0, False); 'S.V.'/blank -> (None, False)."""
    if pd.isna(raw):
        return None, False
    s = str(raw).strip()
    provisional = s.endswith("*")
    s = s.rstrip("*").strip()
    if not s or s.upper() in ("S.V.", "SV"):
        return None, provisional
    try:
        return float(s.replace(",", ".")), provisional
    except ValueError:
        return None, provisional


def _parse_votes_workbook(path):
    """Votes files aren't a flat table: each sheet is a sequence of
    per-team blocks (team-name row, then a 'Cod.' header row, then that
    team's player rows, repeating for every club) with a few banner rows
    on top. Also has 3 sheets (Fantacalcio/Statistico/Italia = different
    vote sources for the same matchday) - all three are kept, tagged by
    `vote_source`, since it's not obvious which one is "the" vote without
    asking fantacalcio.it."""
    xls = pd.ExcelFile(path)
    records = []
    for sheet_name in xls.sheet_names:
        raw = xls.parse(sheet_name, header=None)
        pending_team = None
        current_team = None
        col_names = None
        for row in raw.itertuples(index=False, name=None):
            first = row[0]
            if pd.isna(first):
                continue
            first_str = str(first).strip()
            rest_is_na = all(pd.isna(v) for v in row[1:])

            if first_str == "Cod.":
                col_names = [str(v).strip() for v in row]
                current_team = pending_team
                continue

            if rest_is_na:
                # Either a team-name row or one of the banner/disclaimer
                # rows at the top of the sheet - can't tell until we see
                # whether a 'Cod.' header immediately follows it.
                pending_team = first_str
                continue

            if col_names is not None and current_team is not None:
                rec = dict(zip(col_names, row))
                rec["team"] = current_team
                rec["vote_source"] = sheet_name
                records.append(rec)

    df = pd.DataFrame(records)
    if df.empty:
        return df

    vote_col = "Voto" if "Voto" in df.columns else None
    if vote_col:
        parsed = df[vote_col].apply(_parse_vote_value)
        df["Voto"] = parsed.apply(lambda t: t[0])
        df["vote_provisional"] = parsed.apply(lambda t: t[1])

    matchday_match = MATCHDAY_SUFFIX_RE.search(path.name)
    df["matchday"] = int(matchday_match.group(1)) if matchday_match else None
    return df


def _load_all():
    frames = []
    for f in _discover_raw_files():
        is_votes = f["file_type"] in config.MATCHDAY_FILE_TYPES
        try:
            if is_votes:
                df = _parse_votes_workbook(f["path"])
            else:
                xls = pd.ExcelFile(f["path"])
                sheet = _pick_sheet(xls)
                header_row = _detect_header_row(xls, sheet)
                df = xls.parse(sheet, header=header_row)
        except Exception as exc:
            print(f"[merge] WARNING: failed to read {f['path']}: {exc}")
            continue
        if df.empty:
            print(f"[merge] WARNING: no rows parsed from {f['path']}")
            continue
        df = _canonical_columns(df)
        df["file_type"] = f["file_type"]
        df["league"] = f["league"]
        df["season"] = f["season"]
        df["source_file"] = f["path"].name
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def _resolve_players(df, needs_review_rows):
    """Player identity, in order of preference:

    1. `source_id` (fantacalcio.it's own internal player id - "Id" in
       stats/prices, "Cod." in votes - confirmed identical across all
       three). Exact match, no fuzzy logic needed, and it's what fantacalcio
       itself uses so it can't be wrong the way name-guessing can.
    2. Name-based fuzzy/initial-form matching (fantatool/names.py), only
       for the rare row that has no usable source_id.
    """
    if "name" not in df.columns:
        print("[merge] WARNING: no 'name' column found anywhere - can't resolve player identity")
        df["player_id"] = None
        return df

    df = df.reset_index(drop=True)
    canonical_names = {}
    flagged = {}  # dedup key -> row dict (same real conflict repeats once per matchday/season file)

    def _flag(key, row):
        if key in flagged:
            flagged[key]["occurrences"] += 1
            return
        row["occurrences"] = 1
        flagged[key] = row
        needs_review_rows.append(row)

    def _maybe_upgrade_canonical(pid, raw_name, norm):
        current = canonical_names.get(pid, "")
        if is_initial_form(norm) and not is_initial_form(normalize(current)):
            return  # keep the fuller name we already have
        if not is_initial_form(norm) and is_initial_form(normalize(current)):
            canonical_names[pid] = str(raw_name).strip()
            return
        if len(str(raw_name).strip()) > len(current):
            canonical_names[pid] = str(raw_name).strip()

    def _names_plausibly_match(a, b):
        if similarity(a, b) >= 0.5:
            return True
        return (is_initial_form(a) and initial_form_matches(a, b)) or (
            is_initial_form(b) and initial_form_matches(b, a)
        )

    has_source_id = "source_id" in df.columns
    id_mask = df["source_id"].notna() if has_source_id else pd.Series(False, index=df.index)

    player_ids = pd.Series([None] * len(df), index=df.index, dtype=object)

    # --- primary path: exact source_id match ---
    for i in df.index[id_mask]:
        raw_name = df.at[i, "name"]
        try:
            sid = int(df.at[i, "source_id"])
        except (ValueError, TypeError):
            id_mask.at[i] = False
            continue
        pid = f"FC{sid}"
        player_ids.at[i] = pid
        if pd.isna(raw_name) or not str(raw_name).strip():
            continue
        norm = normalize(raw_name)
        if pid in canonical_names and not _names_plausibly_match(norm, normalize(canonical_names[pid])):
            _flag(
                (pid, raw_name, "source_id_name_mismatch"),
                {
                    "source_file": df.at[i, "source_file"] if "source_file" in df.columns else None,
                    "raw_name": raw_name,
                    "normalized_name": norm,
                    "assigned_player_id": pid,
                    "match_score": round(similarity(norm, normalize(canonical_names[pid])), 3),
                    "ambiguous_with": f"existing_canonical_name:{canonical_names[pid]}",
                    "reason": "source_id_name_mismatch",
                },
            )
        _maybe_upgrade_canonical(pid, raw_name, norm)

    # --- fallback path: old fuzzy name matching, for rows with no id ---
    registry = []  # list of [player_id, set(variants)]
    next_id = 1
    for i in df.index[~id_mask]:
        raw_name = df.at[i, "name"]
        if pd.isna(raw_name) or not str(raw_name).strip():
            continue
        norm = normalize(raw_name)
        pid, score, ambiguous = best_match(norm, [(r[0], r[1]) for r in registry])

        if pid is not None:
            for r in registry:
                if r[0] == pid:
                    r[1].add(norm)
            _maybe_upgrade_canonical(pid, raw_name, norm)
            player_ids.at[i] = pid
            if ambiguous:
                _flag(
                    (pid, raw_name, "close_alternative_candidate_no_source_id"),
                    {
                        "source_file": df.at[i, "source_file"] if "source_file" in df.columns else None,
                        "raw_name": raw_name,
                        "normalized_name": norm,
                        "assigned_player_id": pid,
                        "match_score": round(score, 3),
                        "ambiguous_with": ";".join(f"{c}:{round(s,3)}" for c, s in ambiguous),
                        "reason": "close_alternative_candidate_no_source_id",
                    },
                )
            continue

        new_id = f"NM{next_id:05d}"  # NM = name-matched (no source_id available)
        next_id += 1
        registry.append([new_id, {norm}])
        canonical_names[new_id] = str(raw_name).strip()
        player_ids.at[i] = new_id

    df["player_id"] = player_ids
    df.attrs["canonical_names"] = canonical_names
    return df


def _write_needs_review(rows):
    if not rows:
        return
    with open(config.NEEDS_REVIEW_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"[merge] {len(rows)} uncertain matches written to {config.NEEDS_REVIEW_FILE} - review by hand")


def _build_summary(stats_df):
    if stats_df.empty or "player_id" not in stats_df.columns:
        return pd.DataFrame()

    current_season = max(stats_df["season"].dropna().unique(), default=None)
    if current_season is None:
        return pd.DataFrame()

    current_squad = stats_df[
        (stats_df["league"] == "Serie A") & (stats_df["season"] == current_season)
    ]
    if current_squad.empty:
        print("[merge] WARNING: no Serie A rows for the latest season - can't build summary")
        return pd.DataFrame()

    metric_cols = [
        c
        for c in ["league", "team", "appearances", "avg_vote", "fantamedia", "goals", "assists"]
        if c in stats_df.columns
    ]

    rows = []
    for _, current_row in current_squad.drop_duplicates("player_id").iterrows():
        pid = current_row["player_id"]
        history = (
            stats_df[stats_df["player_id"] == pid]
            .drop_duplicates(subset=["season", "league"])
            .sort_values("season", ascending=False)
            .head(3)
        )
        row = {
            "player_id": pid,
            "name": current_row.get("name"),
            "current_team": current_row.get("team"),
            "role": current_row.get("role"),
        }
        for i, (_, h) in enumerate(history.iterrows(), start=1):
            row[f"season_{i}"] = h.get("season")
            for col in metric_cols:
                row[f"season_{i}_{col}"] = h.get(col)
        rows.append(row)

    return pd.DataFrame(rows)


def run_merge():
    all_df = _load_all()
    if all_df.empty:
        print("[merge] no files found in data/raw - nothing to do")
        return

    needs_review_rows = []
    all_df = _resolve_players(all_df, needs_review_rows)
    canonical_names = all_df.attrs.get("canonical_names", {})

    stats_df = all_df[all_df["file_type"] == "stats"].copy()
    votes_df = all_df[all_df["file_type"].isin(["votes", "votes_euroleghe"])].copy()
    prices_df = all_df[all_df["file_type"] == "prices"].copy()

    players_df = pd.DataFrame(
        {"player_id": list(canonical_names.keys()), "canonical_name": list(canonical_names.values())}
    )

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    tables_written = ["players"]
    with sqlite3.connect(config.DB_FILE) as conn:
        players_df.to_sql("players", conn, if_exists="replace", index=False)
        if not stats_df.empty:
            stats_df.to_sql("stats", conn, if_exists="replace", index=False)
            tables_written.append("stats")
        if not votes_df.empty:
            votes_df.to_sql("votes", conn, if_exists="replace", index=False)
            tables_written.append("votes")
        if not prices_df.empty:
            prices_df.to_sql("prices", conn, if_exists="replace", index=False)
            tables_written.append("prices")
    print(f"[merge] wrote {config.DB_FILE} (tables: {', '.join(tables_written)})")

    players_df.to_csv(config.PLAYERS_CSV, index=False)
    csvs_written = [str(config.PLAYERS_CSV)]
    if not stats_df.empty:
        stats_df.to_csv(config.STATS_CSV, index=False)
        csvs_written.append(str(config.STATS_CSV))
    if not votes_df.empty:
        votes_df.to_csv(config.VOTES_CSV, index=False)
        csvs_written.append(str(config.VOTES_CSV))
    if not prices_df.empty:
        prices_df.to_csv(config.PRICES_CSV, index=False)
        csvs_written.append(str(config.PRICES_CSV))
    print(f"[merge] wrote {', '.join(csvs_written)}")

    _write_needs_review(needs_review_rows)

    summary_df = _build_summary(stats_df)
    if not summary_df.empty:
        summary_df.to_excel(config.SUMMARY_FILE, index=False)
        print(f"[merge] wrote {config.SUMMARY_FILE} ({len(summary_df)} current Serie A players)")
    else:
        print("[merge] summary not written (see warnings above)")
