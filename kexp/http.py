"""Shared HTTP session + retrying JSON request helper."""
import time
import requests

USER_AGENT = "kexp-john-playlist/1.0 (+github actions)"


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def request_json(session, method, url, *, expect=200, retries=3, session_call=None,
                  sleep=time.sleep, **kw):
    """Call session.<method>(url, **kw) with retry on network errors and 5xx.

    Retries up to `retries` times total, backing off `sleep(2*(attempt+1))`
    between attempts. 4xx responses raise immediately (via raise_for_status,
    no retry). Returns the Response once `status_code == expect`.
    """
    call = session_call or getattr(session, method)
    last_exc = None
    for attempt in range(retries):
        try:
            r = call(url, **kw)
        except requests.RequestException as e:
            last_exc = e
            sleep(2 * (attempt + 1))
            continue
        if r.status_code == expect:
            return r
        if 400 <= r.status_code < 500:
            r.raise_for_status()
        last_exc = requests.HTTPError(f"{r.status_code} on {method} {url}", response=r)
        sleep(2 * (attempt + 1))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("request_json: exhausted retries with no response or exception")
