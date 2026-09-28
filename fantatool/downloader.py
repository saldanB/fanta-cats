"""Step 2: download the raw Excel files listed in ids.json (produced and
confirmed via `fanta.py probe`)."""

import csv
import io
import json
import re
import sys
from datetime import datetime, timezone

import pandas as pd

from . import config
from .http_client import AuthError, RateLimitedClient

MIN_MATCHDAY_ROWS = 20  # a real matchday sheet has ~340 rows; "not yet
# available" placeholders and "voting not compiled yet" banners have <5

SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text):
    return SLUG_RE.sub("_", text.lower()).strip("_")


def _load_ids(file_type):
    if not config.IDS_FILE.exists():
        sys.exit(f"{config.IDS_FILE} not found. Run `fanta.py probe --file-type {file_type}` first.")
    all_ids = json.loads(config.IDS_FILE.read_text())
    ids = all_ids.get(file_type)
    if not ids:
        sys.exit(
            f"{config.IDS_FILE} has no mapping for file_type={file_type!r}. "
            f"Run `fanta.py probe --file-type {file_type}` first."
        )
    for kind in ("seasons", "competitions"):
        for k, v in ids.get(kind, {}).items():
            if v == "UNKNOWN":
                sys.exit(
                    f"{config.IDS_FILE}[{file_type!r}] has an UNKNOWN {kind[:-1]} label for id {k}. "
                    f"Fix it by hand before downloading (see logs/ids_probe_raw_{file_type}.json)."
                )
    return ids


def _log_row(row):
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    is_new = not config.DOWNLOAD_LOG_FILE.exists()
    with open(config.DOWNLOAD_LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def _looks_like_real_matchday_file(content):
    """A future/unplayed matchday returns a small 'come back later' Excel
    file (single cell: 'File ancora non disponibile...'), and a matchday
    whose votes aren't compiled yet returns just the banner rows with no
    player data - both pass a simple byte-count check but have almost no
    rows. A real matchday sheet has ~340 rows (20 teams x ~17 players)."""
    try:
        xls = pd.ExcelFile(io.BytesIO(content))
        df = xls.parse(xls.sheet_names[0], header=None)
    except Exception:
        return False
    return len(df) >= MIN_MATCHDAY_ROWS


def _fetch_and_save(client, url, out_path, file_type, league_label, season_label, season_id, axis_id, content_check=None):
    """GET url, log the attempt, save to out_path if it looks like a real
    file. Returns True on success, False on empty/error/placeholder
    response (caller decides whether that's fatal or just "stop, nothing
    more here")."""
    print(f"[download] GET {url} -> {out_path}")
    try:
        resp = client.get(url)
    except AuthError as exc:
        sys.exit(str(exc))

    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "file_type": file_type,
        "league": league_label,
        "season": season_label,
        "season_id": season_id,
        "competition_id": axis_id,  # matchday number, for votes/votes_euroleghe
        "url": url,
        "status": resp.status_code,
        "bytes": len(resp.content),
        "saved_path": "",
    }

    invalid_reason = None
    if resp.status_code != 200 or len(resp.content) < 2000:
        invalid_reason = f"status={resp.status_code} bytes={len(resp.content)}"
    elif content_check is not None and not content_check(resp.content):
        invalid_reason = "content check failed (looks like a placeholder/not-yet-available file)"

    if invalid_reason:
        print(f"[download] WARNING: {url} - {invalid_reason} - not saved")
        _log_row(row)
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(resp.content)
    row["saved_path"] = str(out_path)
    _log_row(row)
    print(f"[download] saved {out_path} ({len(resp.content)} bytes)")
    return True


def _download_competition_axis(client, file_type, seasons, leagues, update_current):
    template = config.ENDPOINTS[file_type]
    ids = _load_ids(file_type)
    season_items = list(ids["seasons"].items())  # (season_id_str, label)
    competition_items = list(ids["competitions"].items())

    if seasons and seasons != ["all"]:
        season_items = [(sid, lbl) for sid, lbl in season_items if lbl in seasons]
    if leagues and leagues != ["all"]:
        competition_items = [(cid, lbl) for cid, lbl in competition_items if lbl in leagues]

    current_season_id = max(int(sid) for sid, _ in ids["seasons"].items())

    for season_id_str, season_label in season_items:
        season_id = int(season_id_str)
        is_current = season_id == current_season_id
        for competition_id_str, league_label in competition_items:
            competition_id = int(competition_id_str)
            league_dir = config.DATA_DIR / _slug(league_label)
            out_path = league_dir / f"{file_type}_{_slug(season_label)}.xlsx"

            if out_path.exists() and not (is_current and update_current):
                print(f"[download] skip (already have): {out_path}")
                continue

            url = template.format(season_id=season_id, competition_id=competition_id)
            _fetch_and_save(client, url, out_path, file_type, league_label, season_label, season_id, competition_id)


# votes/votes_euroleghe don't have their own season_id mapping - the numbering
# is confirmed identical to the file_type below, so reuse its season labels.
MATCHDAY_SEASON_SOURCE = {"votes": "stats", "votes_euroleghe": "prices"}
MATCHDAY_LEAGUE_LABEL = {"votes": "Serie A", "votes_euroleghe": "Euroleghe"}


def _download_matchday_axis(client, file_type, seasons, matchdays, update_current):
    template = config.ENDPOINTS[file_type]
    season_source = MATCHDAY_SEASON_SOURCE[file_type]
    ids = _load_ids(season_source)
    season_items = list(ids["seasons"].items())
    if seasons and seasons != ["all"]:
        season_items = [(sid, lbl) for sid, lbl in season_items if lbl in seasons]

    current_season_id = max(int(sid) for sid, _ in ids["seasons"].items())
    league_label = MATCHDAY_LEAGUE_LABEL[file_type]
    league_dir = config.DATA_DIR / _slug(league_label)

    for season_id_str, season_label in season_items:
        season_id = int(season_id_str)
        is_current = season_id == current_season_id
        for matchday in matchdays:
            out_path = league_dir / f"{file_type}_{_slug(season_label)}_g{matchday:02d}.xlsx"

            if out_path.exists() and not (is_current and update_current):
                print(f"[download] skip (already have): {out_path}")
                continue

            url = template.format(season_id=season_id, matchday=matchday)
            ok = _fetch_and_save(
                client, url, out_path, file_type, league_label, season_label, season_id, matchday,
                content_check=_looks_like_real_matchday_file,
            )
            if not ok:
                print(
                    f"[download] stopping matchday scan for {season_label} at "
                    f"g{matchday} (season not that far yet, or doesn't exist)"
                )
                break


def run_download(seasons, leagues, file_types, update_current, matchdays=None):
    client = RateLimitedClient()
    matchdays = matchdays or config.DEFAULT_MATCHDAYS

    for file_type in file_types:
        template = config.ENDPOINTS.get(file_type)
        if not template:
            print(f"[download] skipping file_type={file_type}: no URL template configured")
            continue

        if file_type in config.MATCHDAY_FILE_TYPES:
            _download_matchday_axis(client, file_type, seasons, matchdays, update_current)
        else:
            _download_competition_axis(client, file_type, seasons, leagues, update_current)
