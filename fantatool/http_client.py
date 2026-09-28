import sys
import time

import requests

from . import config


class AuthError(Exception):
    pass


class RateLimitedClient:
    """Wraps requests.Session with a fixed delay between calls and a
    hard stop on 401/403 (expired session)."""

    def __init__(self, delay=config.REQUEST_DELAY_SECONDS):
        if not config.AUTH_COOKIE:
            sys.exit(
                "FANTACALCIO_AUTH missing. Copy .env.example to .env and set it. "
                "See README for how to grab the cookie."
            )
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Cookie": config.AUTH_COOKIE,
                "User-Agent": config.USER_AGENT,
                "Accept": (
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet, application/octet-stream, */*"
                ),
                "Referer": config.BASE_URL + "/",
            }
        )
        self.delay = delay
        self._last_request = 0.0

    def get(self, url):
        elapsed = time.time() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        resp = self.session.get(url, timeout=30)
        self._last_request = time.time()
        if resp.status_code in (401, 403):
            raise AuthError(
                f"Got HTTP {resp.status_code} from {url}. "
                "Session expired or cookie invalid - refresh FANTACALCIO_AUTH in .env."
            )
        return resp
