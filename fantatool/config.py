import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://www.fantacalcio.it"

# Confirmed from browser Network tab.
STATS_URL_TEMPLATE = BASE_URL + "/api/v1/Excel/stats/{season_id}/{competition_id}"
# Confirmed by user: Euro Leghe prices/quotazioni. Uses its own season_id
# numbering, unrelated to the stats endpoint's (e.g. season_id=103 = 2020/21
# here, vs season_id=15 for the same real season on /stats/).
PRICES_URL_TEMPLATE = BASE_URL + "/api/v1/Excel/prices/{season_id}/{competition_id}"

# Confirmed by user: Serie A matchday votes. NOTE the second slot here is a
# matchday number (1-38ish), not a competition_id - this endpoint has a
# different shape from /stats/ and /prices/. season_id reuses the SAME
# numbering as /stats/ (season_id=14 = 2019-20 on both).
VOTES_URL_TEMPLATE = BASE_URL + "/api/v1/Excel/votes/{season_id}/{matchday}"

# NOT confirmed yet. Fill FANTACALCIO_EUROLEGHE_VOTES_URL_TEMPLATE in .env
# once you've captured the real request from the Euro Leghe "Voti" download
# button in DevTools -> Network. Use the literal placeholders {season_id}
# and {matchday} (assumed same shape as VOTES_URL_TEMPLATE above, but
# unconfirmed - the actual URL may differ).
EUROLEGHE_VOTES_URL_TEMPLATE = (
    os.environ.get("FANTACALCIO_EUROLEGHE_VOTES_URL_TEMPLATE") or None
)

ENDPOINTS = {
    "stats": STATS_URL_TEMPLATE,
    "prices": PRICES_URL_TEMPLATE,
    "votes": VOTES_URL_TEMPLATE,
    "votes_euroleghe": EUROLEGHE_VOTES_URL_TEMPLATE,
}

# stats and prices are keyed by (season_id, competition_id); votes and
# votes_euroleghe are keyed by (season_id, matchday) instead. ids.json only
# ever stores season/competition labels (competition axis) - votes reuses
# the "stats" season mapping directly rather than needing its own probe,
# since the season_id numbering is confirmed identical.
MATCHDAY_FILE_TYPES = {"votes", "votes_euroleghe"}
DEFAULT_MATCHDAYS = list(range(1, 39))  # Serie A: 38 matchdays in a full season

AUTH_COOKIE = os.environ.get("FANTACALCIO_AUTH") or None

DATA_DIR = Path("data")  # raw downloads: data/{league}/{file_type}_{season}.xlsx
OUTPUT_DIR = Path("output")  # merged deliverables: fanta.db, CSVs, summary
LOGS_DIR = Path("logs")  # download_log.csv + probe diagnostics

IDS_FILE = Path("ids.json")  # stays at root - you review/edit this by hand

DB_FILE = OUTPUT_DIR / "fanta.db"
NEEDS_REVIEW_FILE = OUTPUT_DIR / "needs_review.csv"
SUMMARY_FILE = OUTPUT_DIR / "fanta_summary.xlsx"
PLAYERS_CSV = OUTPUT_DIR / "players.csv"
STATS_CSV = OUTPUT_DIR / "stats.csv"
VOTES_CSV = OUTPUT_DIR / "votes.csv"
PRICES_CSV = OUTPUT_DIR / "prices.csv"

DOWNLOAD_LOG_FILE = LOGS_DIR / "download_log.csv"

REQUEST_DELAY_SECONDS = 3

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 fanta-cats-oh/1.0"
)

# Known Italian/English keywords used to guess a league label from a
# filename or sheet name returned by the server. Extend if a league you
# care about isn't recognised.
LEAGUE_KEYWORDS = {
    "Serie A": ["serie a", "seriea"],
    "Serie B": ["serie b", "serieb"],
    "Premier League": ["premier"],
    "La Liga": ["liga"],
    "Bundesliga": ["bundesliga"],
    "Ligue 1": ["ligue"],
    "Euroleghe": ["euroleghe", "euro leghe", "euro-leghe"],
}
