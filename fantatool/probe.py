"""Step 1: work out which season_id / competition_id map to which real
season and league, without guessing blind. Strategy:

1. Download the one URL you already confirmed in the browser and print its
   columns, so we know the file format.
2. Holding season_id fixed, walk competition_id = 1, 2, 3, ... and stop at
   the first request that errors or comes back empty. Each successful id
   is a distinct league available for that season.
3. Holding competition_id fixed at the first working league (normally
   Serie A / 1), walk season_id downward then upward from the seed value
   and stop at the first failure each direction. That brackets which
   seasons exist.

Every probe is a single GET, 3s apart, and stops immediately on the first
bad response - it never keeps hammering ids once one fails.
"""

import io
import json
import re
import sys

import pandas as pd

from . import config
from .http_client import AuthError, RateLimitedClient

MIN_VALID_BYTES = 2000  # a real .xlsx is well above this; empty/error bodies aren't
SEASON_SCAN_MAX_STEPS = 30
COMPETITION_SCAN_MAX_STEPS = 12
SEASON_LOOKAHEAD_STEPS = 5

SEASON_PATTERN = re.compile(r"(20\d{2})[-_/]?(\d{2,4})")


def _looks_like_valid_excel(resp):
    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}"
    content_type = resp.headers.get("Content-Type", "")
    if "html" in content_type.lower():
        return False, "got HTML back (likely a login/error page, not a file)"
    if len(resp.content) < MIN_VALID_BYTES:
        return False, f"body only {len(resp.content)} bytes"
    try:
        pd.ExcelFile(io.BytesIO(resp.content))
    except Exception as exc:
        return False, f"not a parseable excel file: {exc}"
    return True, None


def _guess_filename(resp):
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
    return m.group(1) if m else None


def _guess_season_label(*texts):
    blob = " ".join(t for t in texts if t)
    m = SEASON_PATTERN.search(blob)
    if not m:
        return None
    year, suffix = m.groups()
    if len(suffix) == 4:
        return f"{year}-{suffix[2:]}"
    return f"{year}-{suffix}"


def _guess_league_label(*texts):
    blob = " ".join(t for t in texts if t).lower()
    for label, keywords in config.LEAGUE_KEYWORDS.items():
        if any(kw in blob for kw in keywords):
            return label
    return None


def _inspect(resp, season_id, competition_id):
    filename = _guess_filename(resp)
    entry = {
        "season_id": season_id,
        "competition_id": competition_id,
        "filename": filename,
        "bytes": len(resp.content),
    }
    try:
        xls = pd.ExcelFile(io.BytesIO(resp.content))
        entry["sheet_names"] = xls.sheet_names
        first_df = xls.parse(xls.sheet_names[0], nrows=5)
        entry["columns"] = list(map(str, first_df.columns))
    except Exception as exc:
        entry["parse_error"] = str(exc)
        entry["columns"] = []
        entry["sheet_names"] = []
    entry["guessed_season"] = _guess_season_label(
        filename, " ".join(entry.get("sheet_names", []))
    )
    entry["guessed_league"] = _guess_league_label(
        filename, " ".join(entry.get("sheet_names", [])), " ".join(entry.get("columns", []))
    )
    return entry


def run_probe(file_type, seed_season_id, seed_competition_id):
    template = config.ENDPOINTS.get(file_type)
    if not template:
        sys.exit(
            f"No URL template configured for file_type={file_type!r}. "
            "Set it in .env (see .env.example) or fantatool/config.py."
        )

    client = RateLimitedClient()
    raw = {"file_type": file_type, "competitions": [], "seasons": []}

    # 1. One confirmed file, show columns.
    url = template.format(season_id=seed_season_id, competition_id=seed_competition_id)
    print(f"[probe] GET {url}")
    resp = client.get(url)
    ok, reason = _looks_like_valid_excel(resp)
    if not ok:
        sys.exit(f"Seed request failed ({reason}). Fix the URL/cookie before probing further.")
    seed_entry = _inspect(resp, seed_season_id, seed_competition_id)
    print(f"[probe] seed file columns: {seed_entry['columns']}")
    print(f"[probe] seed file sheets:  {seed_entry['sheet_names']}")
    raw["competitions"].append(seed_entry)
    raw["seasons"].append(seed_entry)

    # 2. Walk competition_id at fixed season_id.
    for competition_id in range(1, COMPETITION_SCAN_MAX_STEPS + 1):
        if competition_id == seed_competition_id:
            continue
        url = template.format(season_id=seed_season_id, competition_id=competition_id)
        print(f"[probe] GET {url}")
        try:
            resp = client.get(url)
        except AuthError as exc:
            sys.exit(str(exc))
        ok, reason = _looks_like_valid_excel(resp)
        if not ok:
            print(f"[probe] competition_id={competition_id} stopped: {reason}")
            break
        entry = _inspect(resp, seed_season_id, competition_id)
        print(
            f"[probe] competition_id={competition_id} -> "
            f"guessed_league={entry['guessed_league']} filename={entry['filename']}"
        )
        raw["competitions"].append(entry)

    # 3. Walk season_id downward from the seed, at the seed competition_id.
    for step in range(1, SEASON_SCAN_MAX_STEPS + 1):
        season_id = seed_season_id - step
        if season_id < 1:
            break
        url = template.format(season_id=season_id, competition_id=seed_competition_id)
        print(f"[probe] GET {url}")
        try:
            resp = client.get(url)
        except AuthError as exc:
            sys.exit(str(exc))
        ok, reason = _looks_like_valid_excel(resp)
        if not ok:
            print(f"[probe] season_id={season_id} (going down) stopped: {reason}")
            break
        entry = _inspect(resp, season_id, seed_competition_id)
        print(
            f"[probe] season_id={season_id} -> "
            f"guessed_season={entry['guessed_season']} filename={entry['filename']}"
        )
        raw["seasons"].append(entry)

    # 4. Walk season_id upward from the seed (in case seed isn't the latest).
    for step in range(1, SEASON_LOOKAHEAD_STEPS + 1):
        season_id = seed_season_id + step
        url = template.format(season_id=season_id, competition_id=seed_competition_id)
        print(f"[probe] GET {url}")
        try:
            resp = client.get(url)
        except AuthError as exc:
            sys.exit(str(exc))
        ok, reason = _looks_like_valid_excel(resp)
        if not ok:
            print(f"[probe] season_id={season_id} (going up) stopped: {reason}")
            break
        entry = _inspect(resp, season_id, seed_competition_id)
        print(
            f"[probe] season_id={season_id} -> "
            f"guessed_season={entry['guessed_season']} filename={entry['filename']}"
        )
        raw["seasons"].append(entry)

    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    raw_file = config.LOGS_DIR / f"ids_probe_raw_{file_type}.json"
    raw_file.write_text(json.dumps(raw, indent=2, ensure_ascii=False))
    print(f"\n[probe] raw metadata for every probed id written to {raw_file}")

    guess = {
        "seasons": {
            str(e["season_id"]): e["guessed_season"] or "UNKNOWN"
            for e in raw["seasons"]
        },
        "competitions": {
            str(e["competition_id"]): e["guessed_league"] or "UNKNOWN"
            for e in raw["competitions"]
        },
    }

    ids = json.loads(config.IDS_FILE.read_text()) if config.IDS_FILE.exists() else {}
    ids[file_type] = guess
    config.IDS_FILE.write_text(json.dumps(ids, indent=2, ensure_ascii=False))

    print(f"\n[probe] best-guess mapping for file_type={file_type!r} written to {config.IDS_FILE} - REVIEW BEFORE DOWNLOADING:")
    print(json.dumps(guess, indent=2, ensure_ascii=False))
    print(
        f"\nAny 'UNKNOWN' above: open {raw_file}, look at the filename/columns "
        f"for that id, and fix the label by hand in {config.IDS_FILE} under {file_type!r}."
    )
