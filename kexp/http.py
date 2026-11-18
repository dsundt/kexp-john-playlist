"""Shared HTTP session + retrying JSON request helper."""
import math
import time

import requests

USER_AGENT = "kexp-john-playlist/1.0 (+github actions)"

# Statuses worth another attempt. 429 is deliberately included: Spotify
# rate-limits with a Retry-After header, and treating it as a hard 4xx throws
# away the one case the API explicitly tells us to retry.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# Cap on an honoured Retry-After so an unreasonable server hint can't stall a
# run (or a GitHub Action) for minutes at a time.
MAX_RETRY_AFTER_S = 30


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def _retry_after_seconds(r) -> float | None:
    """Spotify's Retry-After, in seconds, when present and sane (else None)."""
    raw = r.headers.get("Retry-After") if r.headers else None
    if not raw:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None  # HTTP-date form; fall back to plain backoff
    # NaN/inf survive float() and time.sleep(NaN) would raise, so reject
    # non-finite outright.
    if not math.isfinite(seconds):
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_S)


def request_json(session, method, url, *, expect=200, retries=3, session_call=None,
                  sleep=time.sleep, **kw):
    """Call session.<method>(url, **kw) with retry on network errors, 429 and 5xx.

    Retries up to `retries` times total, backing off `sleep(2*(attempt+1))`
    between attempts. A 429 honours the response's `Retry-After` header
    (clamped to MAX_RETRY_AFTER_S). Other 4xx responses raise immediately.
    Returns the Response once `status_code == expect`.
    """
    call = session_call or getattr(session, method)
    last_exc = None
    for attempt in range(retries):
        try:
            r = call(url, **kw)
        except requests.RequestException as e:
            last_exc = e
            delay = 2 * (attempt + 1)
        else:
            if r.status_code == expect:
                return r
            if 400 <= r.status_code < 500 and r.status_code not in RETRY_STATUSES:
                r.raise_for_status()
            last_exc = requests.HTTPError(f"{r.status_code} on {method} {url}", response=r)
            if r.status_code == 429:
                delay = _retry_after_seconds(r) or 2 * (attempt + 1)
            else:
                delay = 2 * (attempt + 1)
        if attempt < retries - 1:
            sleep(delay)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("request_json: exhausted retries with no response or exception")
