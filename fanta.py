#!/usr/bin/env python3
"""CLI for downloading and merging fantacalcio.it Excel exports.

    fanta.py probe    [--file-type stats] [--season-id 17] [--competition-id 1]
    fanta.py download [--seasons 2023-24 2022-23 | all] [--leagues "Serie A" | all]
                       [--file-types stats prices votes votes_euroleghe]
                       [--matchdays 1-38] [--update-current]
    fanta.py merge
    fanta.py dataset  --seasons 2024-25 2025-26 | all  --matchday 5

See README.md for the full workflow.
"""

import argparse

import pandas as pd

from fantatool import config, dataset, downloader, loader, merge, probe


def _parse_matchdays(values):
    """Accepts individual ints and/or 'A-B' ranges, e.g. ['1-10', '15', '20-22']."""
    result = []
    for v in values:
        if "-" in v:
            lo, hi = v.split("-", 1)
            result.extend(range(int(lo), int(hi) + 1))
        else:
            result.append(int(v))
    return sorted(set(result))


def _run_dataset(seasons, matchday):
    with loader.connect() as conn:
        if seasons == ["all"]:
            seasons = [r[0] for r in conn.execute("SELECT DISTINCT season FROM stats ORDER BY season")]
        frames = [dataset.build_dataset(conn, s, matchday) for s in seasons]
    df = pd.concat(frames, ignore_index=True)
    tag = seasons[0] if len(seasons) == 1 else f"{seasons[0]}_to_{seasons[-1]}"
    path = config.OUTPUT_DIR / f"dataset_{tag}_md{matchday:02d}.csv"
    df.to_csv(path, index=False)
    has_target = df["target_fantamedia"].notna().sum()
    print(f"[dataset] wrote {path} ({len(df)} rows, {df.shape[1]} columns, {has_target} with target)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_probe = sub.add_parser("probe", help="Step 1: map season_id/competition_id, write ids.json")
    p_probe.add_argument("--file-type", default="stats", choices=["stats", "prices", "votes", "votes_euroleghe"])
    p_probe.add_argument("--season-id", type=int, default=17, help="a season_id you already confirmed in the browser")
    p_probe.add_argument("--competition-id", type=int, default=1, help="a competition_id you already confirmed (1 = Serie A)")

    p_dl = sub.add_parser("download", help="Step 2: download files listed in ids.json")
    p_dl.add_argument("--seasons", nargs="+", default=["all"], help="season labels from ids.json, e.g. 2023-24 2022-23, or 'all'")
    p_dl.add_argument("--leagues", nargs="+", default=["all"], help="league labels from ids.json, e.g. 'Serie A' 'Premier League', or 'all'")
    p_dl.add_argument("--file-types", nargs="+", default=["stats", "prices", "votes", "votes_euroleghe"])
    p_dl.add_argument("--matchdays", nargs="+", default=None, help="for votes/votes_euroleghe only: e.g. '1-38' or '1 2 3'. Default: 1-38, stopping early per season on the first empty response")
    p_dl.add_argument("--update-current", action="store_true", help="re-download the current season even if already saved")

    sub.add_parser("merge", help="Step 3: build fanta.db, CSVs, needs_review.csv, fanta_summary.xlsx")

    p_ds = sub.add_parser("dataset", help="Step 4: build the modelling dataset (prev-season summary + first matchdays -> full season)")
    p_ds.add_argument("--seasons", nargs="+", required=True, help="current_season labels, e.g. 2024-25 2025-26, or 'all' (every season in stats)")
    p_ds.add_argument("--matchday", type=int, required=True, help="current_matchday: time series covers matchdays 1..N")

    args = parser.parse_args()

    if args.command == "probe":
        probe.run_probe(args.file_type, args.season_id, args.competition_id)
    elif args.command == "download":
        matchdays = _parse_matchdays(args.matchdays) if args.matchdays else None
        downloader.run_download(args.seasons, args.leagues, args.file_types, args.update_current, matchdays=matchdays)
    elif args.command == "merge":
        merge.run_merge()
    elif args.command == "dataset":
        _run_dataset(args.seasons, args.matchday)


if __name__ == "__main__":
    main()
