# Phase 1: kexp Package + Playlist Safety — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the single-file KEXP→Spotify bot into a tested `kexp` package and eliminate the playlist-truncation risk via snapshot-guarded DELETE-based dedupe, ISRC+hybrid near-dupe handling, add-time dedupe, backups, and outage alerting.

**Architecture:** Extract cohesive modules under `kexp/` behind small interfaces; `scripts/run.py` becomes a thin entrypoint calling `kexp.pipeline.main()`. Playlist mutations move from full-replace to uri-based `DELETE` + kept-copy re-add (dedupe) and backup→replace→verify→restore (reorder). Spotify's `DELETE /playlists/{id}/tracks` removes ALL occurrences of a supplied uri (per-uri `positions` are no longer honored), so dedupe deletes each duplicated uri and re-adds the copies it means to keep; only removed uris are touched, so it is non-truncating. Pure logic (matching, dedupe classification, feed) is unit-tested with no network; HTTP clients are tested with a mocked session.

**Tech Stack:** Python 3.11, `requests` (pinned 2.32.5), `pytest` (dev only). GitHub Actions runner. No new runtime deps.

## Global Constraints

- Python 3.11; runtime dependency is `requests==2.32.5` only (verbatim from `requirements.txt`).
- Never log secret values — lengths only (existing rule in `refresh_access_token`).
- Playlist mutations MUST be non-truncating: dedupe via uri-based `DELETE` with `snapshot_id` (+ re-add of kept copies for same-uri dupes); reorder via backup→replace→verify→auto-restore.
- Dedupe keeps the EARLIEST occurrence; auto-remove only exact (track id OR ISRC) and "safe" near-dupes (identical after stripping case/punctuation). Version-distinct near-dupes (differ by remaster/live/edit/radio/mono/feat) are REPORTED, never auto-removed.
- "My Artists" = artists in the user's playlist (flag name `is_my_artist` in new code; `in_playlist` retained in data files already on disk).
- `scripts/run.py` must remain the workflow entrypoint (`python scripts/run.py`) with unchanged env-var contract.
- Tests use `pytest` with a mocked `requests.Session`; no live API calls in the suite.

---

### Task 0: Package scaffold + pytest

**Files:**
- Create: `kexp/__init__.py`, `tests/__init__.py`, `tests/conftest.py`, `requirements-dev.txt`, `pytest.ini`
- Modify: CI workflow to run the suite. **As shipped, the pytest job lives in a SEPARATE `.github/workflows/tests.yml`** (push + PR), kept apart from `kexp.yml` so the test run never triggers the live playlist workflow (which stays schedule/dispatch-only). The step block below applies to `tests.yml`.

**Interfaces:**
- Produces: `tests/conftest.py::fake_session` fixture returning a `FakeSession` whose `.get/.post/.put/.delete` return queued `FakeResponse(status_code, json_body, headers)` objects and record calls in `.calls`.

- [ ] **Step 1: Write `requirements-dev.txt`**
```
-r requirements.txt
pytest==8.3.3
```
- [ ] **Step 2: Write `pytest.ini`**
```ini
[pytest]
testpaths = tests
python_files = test_*.py
addopts = -q
```
- [ ] **Step 3: Write `tests/conftest.py`** — a minimal fake HTTP session (no `requests` network):
```python
import json as _json
import pytest

class FakeResponse:
    def __init__(self, status_code=200, json_body=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_body
        self.headers = headers or {}
        self.text = text if text else (_json.dumps(json_body) if json_body is not None else "")
    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json
    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}", response=self)

class FakeSession:
    def __init__(self):
        self.queues = {"get": [], "post": [], "put": [], "delete": []}
        self.calls = []
    def _next(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        q = self.queues[method]
        return q.pop(0) if q else FakeResponse(200, {})
    def get(self, url, **kw): return self._next("get", url, **kw)
    def post(self, url, **kw): return self._next("post", url, **kw)
    def put(self, url, **kw): return self._next("put", url, **kw)
    def delete(self, url, **kw): return self._next("delete", url, **kw)
    def queue(self, method, *responses): self.queues[method].extend(responses)

@pytest.fixture
def fake_session():
    return FakeSession()
```
- [ ] **Step 4: Add the pytest job in `.github/workflows/tests.yml`** (a standalone workflow, NOT a job inside `kexp.yml`, so tests never trigger the live run). It sets `permissions: contents: read` and `persist-credentials: false` on checkout:
```yaml
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v6
        with:
          python-version: "3.11"
      - run: pip install -r requirements-dev.txt
      - run: pytest
```
- [ ] **Step 5: Run `pytest`** — Expected: `no tests ran` (exit 5) — acceptable at this point; verify conftest imports without error via `python -c "import tests.conftest"`.
- [ ] **Step 6: Commit** `git add -A && git commit -m "test: package scaffold + pytest + fake session"`

---

### Task 1: `kexp/config.py` — typed config from env

**Files:** Create `kexp/config.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `Config.from_env(env: dict) -> Config` dataclass with fields: `client_id, client_secret, refresh_token, playlist_id` (str), `do_spotify_adds, do_reorder, do_dedupe, do_email` (bool), `feed_max_items` (int, default 200), plus the SMTP fields. `Config.missing() -> list[str]` names required-but-empty secrets.

- [ ] **Step 1: Failing test** `tests/test_config.py`:
```python
from kexp.config import Config
def test_from_env_parses_and_flags():
    c = Config.from_env({"SPOTIFY_CLIENT_ID":"a","SPOTIFY_CLIENT_SECRET":"b",
        "SPOTIFY_REFRESH_TOKEN":"c","SPOTIFY_PLAYLIST_ID":"d","DO_DEDUPE":"0","FEED_MAX_ITEMS":"50"})
    assert c.client_id == "a" and c.do_dedupe is False and c.feed_max_items == 50
    assert c.do_reorder is True  # default
    assert c.missing() == []
def test_missing_lists_empty_required():
    assert set(Config.from_env({}).missing()) == {
        "SPOTIFY_CLIENT_ID","SPOTIFY_CLIENT_SECRET","SPOTIFY_REFRESH_TOKEN","SPOTIFY_PLAYLIST_ID"}
```
- [ ] **Step 2: Run** `pytest tests/test_config.py -v` → FAIL (no module).
- [ ] **Step 3: Implement `kexp/config.py`** with a frozen dataclass, `_flag(env,key,default)` returning `env.get(key,default)=="1" if present else default`, and `from_env` reading all fields (mirror the env names in the current `run.py` header). `missing()` returns the required-secret names whose value is falsy.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: kexp.config typed env config`.

---

### Task 2: `kexp/http.py` — shared session + retry helper

**Files:** Create `kexp/http.py`, `tests/test_http.py`

**Interfaces:**
- Produces: `make_session() -> requests.Session` (User-Agent set). `request_json(session, method, url, *, expect=200, retries=3, session_call=None, **kw) -> Response` — retries `requests.RequestException` and 5xx with backoff `sleep(2*(n+1))`; returns the Response on `expect`; raises on 4xx (calls `raise_for_status`). `sleep` injectable via param `sleep=time.sleep`.

- [ ] **Step 1: Failing test** — network error then success retries; 4xx raises immediately:
```python
import requests
from kexp.http import request_json
from tests.conftest import FakeResponse
def test_retries_network_then_succeeds(fake_session):
    calls={"n":0}
    def flaky(url,**kw):
        calls["n"]+=1
        if calls["n"]<3: raise requests.ConnectionError("x")
        return FakeResponse(200,{"ok":True})
    fake_session.post=flaky
    r=request_json(fake_session,"post","http://x",sleep=lambda *_:None)
    assert r.json()["ok"] and calls["n"]==3
def test_4xx_raises(fake_session):
    fake_session.queue("get",FakeResponse(400,text="bad"))
    import pytest
    with pytest.raises(requests.HTTPError):
        request_json(fake_session,"get","http://x",sleep=lambda *_:None)
```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `request_json` looping `retries` times: `getattr(session,method)(url,**kw)` inside try/except `requests.RequestException` (retry+sleep); on response, if `status_code==expect` return; if `400<=code<500` call `raise_for_status()`; else retry. After loop raise last error. `make_session()` sets `SESSION.headers["User-Agent"]`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: kexp.http session + retry helper`.

---

### Task 3: `kexp/feed.py` — escape / self-heal / trim (extract existing)

**Files:** Create `kexp/feed.py`, `tests/test_feed.py`. Modify `scripts/run.py` later to import.

**Interfaces:**
- Produces: `append_item(path, title, link, guid, pubdate, *, max_items=200)`, `normalize_if_needed(path, *, max_items=200) -> bool`, `ensure_exists(path, title, link, desc)`. Escapes content, self-heals stray `&`, trims to `max_items`.

- [ ] **Step 1: Failing test** — port the already-proven behavior: malformed feed with raw `&` and 5 items, `max_items=3` → `normalize_if_needed` heals to well-formed 3 items; a second call returns False (idempotent). `append_item` with `Simon & Garfunkel — <x>` round-trips via `xml.dom.minidom.parseString`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** by moving the shipped feed functions from `scripts/run.py` (the `_ITEM_RE`, `_STRAY_AMP_RE`, `_feed_header`, `_build_item`, `_read_feed_items`, `_write_feed`, `ensure_feed_exists`, `normalize_feed_if_needed`, `append_feed_item`) into `kexp/feed.py`, parameterizing `path`/`max_items`/`title`/`link`/`desc` instead of module globals.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `refactor: extract feed logic into kexp.feed with tests`.

---

### Task 4: `kexp/matching.py` — normalization + ISRC + fallback search

**Files:** Create `kexp/matching.py`, `tests/test_matching.py`

**Interfaces:**
- Produces:
  - `normalize_title(s) -> str` (lowercase, strip case/punctuation only — NO version-keyword stripping).
  - `strip_version(s) -> str` (also removes trailing remaster/live/edit/radio/mono/version + parenthetical/feat).
  - `is_version_variant(a, b) -> bool` — True when `normalize_title(a)!=normalize_title(b)` but `strip_version(a)==strip_version(b)`.
  - `search_best(search_fn, artist, song) -> track|None` — ordered fallback using an injected `search_fn(query, params)->items`.

- [ ] **Step 1: Failing tests**:
```python
from kexp.matching import normalize_title, strip_version, is_version_variant
def test_normalize_punct_case():
    assert normalize_title("Stop!") == normalize_title("stop")
def test_version_variant_detection():
    assert is_version_variant("Finest Worksong","Finest Worksong - Remastered") is True
    assert is_version_variant("Feeling Good","Feeling Good") is False
```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the three pure functions (regexes per the shipped `clean_title` plus a version-keyword regex), and `search_best` trying: strict field query → version-stripped query → loose query, returning the first non-empty best item (prefer exact normalized-title match; de-prioritize items whose title adds live/remix not in the query).
- [ ] **Step 4: Run** → PASS. Add a `search_best` test with a fake `search_fn` returning `[]` for strict and a hit for the stripped query.
- [ ] **Step 5: Commit** `feat: kexp.matching normalization + ISRC-aware fallback search`.

---

### Task 5: `kexp/dedupe.py` — classify duplicates (pure)

**Files:** Create `kexp/dedupe.py`, `tests/test_dedupe.py`

**Interfaces:**
- Consumes: playlist items as `[{uri, id, isrc, name, artist, position}]` in playlist order.
- Produces: `classify(items) -> DedupePlan` where `DedupePlan` has:
  - `remove_positions: list[int]` — positions to DELETE (later exact-by-id, exact-by-ISRC, and SAFE near-dupes; earliest kept).
  - `report: list[dict]` — version-distinct near-dupe groups for the operator (never auto-removed).
  - `counts: {exact, isrc, safe_near, reported}`.

- [ ] **Step 1: Failing test** covering all four cases with realistic rows (exact id dup; same ISRC diff id; safe near "Stop"/"Stop!"; version-distinct "Finest Worksong"/"- Remastered" → report, not remove):
```python
from kexp.dedupe import classify
def test_classify_paths():
    items=[
      {"position":0,"uri":"u0","id":"a","isrc":"I1","artist":"X","name":"Song"},
      {"position":1,"uri":"u1","id":"a","isrc":"I1","artist":"X","name":"Song"},      # exact id -> remove 1
      {"position":2,"uri":"u2","id":"b","isrc":"I2","artist":"Y","name":"Feeling Good"},
      {"position":3,"uri":"u3","id":"c","isrc":"I2","artist":"Y","name":"Feeling Good"}, # same ISRC -> remove 3
      {"position":4,"uri":"u4","id":"d","isrc":"I3","artist":"Z","name":"Stop"},
      {"position":5,"uri":"u5","id":"e","isrc":"I4","artist":"Z","name":"Stop!"},        # safe near -> remove 5
      {"position":6,"uri":"u6","id":"f","isrc":"I5","artist":"R","name":"Finest Worksong"},
      {"position":7,"uri":"u7","id":"g","isrc":"I6","artist":"R","name":"Finest Worksong - Remastered"}, # report
    ]
    plan=classify(items)
    assert plan.remove_positions==[1,3,5]
    assert plan.counts=={"exact":1,"isrc":1,"safe_near":1,"reported":1}
    assert len(plan.report)==1 and plan.report[0]["artist"]=="R"
```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `classify`: iterate keeping first-seen sets for `id`, `isrc`, and `(artist_norm, normalize_title(name))`; when a later row matches an already-seen id/isrc/safe-key → add its position to `remove_positions`. For rows sharing `(artist_norm, strip_version(name))` but NOT the safe key and NOT id/isrc → add to `report` (group by that key). Use `kexp.matching`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: kexp.dedupe classify (exact/ISRC/safe-near/report)`.

---

### Task 6: `kexp/spotify_client.py` — auth, read (w/ ISRC), safe DELETE, verified reorder

**Files:** Create `kexp/spotify_client.py`, `tests/test_spotify_client.py`

**Interfaces:**
- Consumes: `Config`, `kexp.http.request_json`, a session.
- Produces class `SpotifyClient(session, config, sleep=time.sleep)` with:
  - `refresh_token() -> str` (retry + error-body logging; fail-fast 4xx).
  - `fetch_playlist(token) -> (snapshot_id, items)` where each item includes `uri,id,isrc,name,artist,album_release_date,position` (ISRC from `track.external_ids.isrc`).
  - `remove_duplicate_uris(token, snapshot_id, removals: list[{uri, remove_count, total_count}]) -> None` — batches ≤100 uris, `DELETE` with `{tracks:[{uri}], snapshot_id}` (per-uri `positions` are NO LONGER honored by Spotify — a uri removes ALL its occurrences); for same-uri dupes where `keep = total_count - remove_count > 0`, re-adds the kept copies via `POST {uris:[uri]*keep}`. (Superseded the earlier positional `remove_positions`.)
  - `reorder(token, ordered_uris) -> None` — caller-supplied; NOT used directly by dedupe.
  - `replace_with_verify(token, ordered_uris, expected_set) -> bool` — backup done by caller; PUT first100+POST rest, then re-`fetch_playlist`; if the resulting uri-set != `expected_set` raise `PlaylistVerifyError` (caller restores + alerts).

- [ ] **Step 1: Failing test** — uri-based delete carries snapshot_id and re-adds kept copies only for same-uri dupes:
```python
def test_remove_duplicate_uris_distinct_deletes_no_readd(fake_session):
    from kexp.spotify_client import SpotifyClient
    from kexp.config import Config
    from tests.conftest import FakeResponse
    c=Config.from_env({"SPOTIFY_CLIENT_ID":"i","SPOTIFY_CLIENT_SECRET":"s",
        "SPOTIFY_REFRESH_TOKEN":"r","SPOTIFY_PLAYLIST_ID":"pl"})
    fake_session.queue("delete",FakeResponse(200,{"snapshot_id":"S2"}))
    sc=SpotifyClient(fake_session,c,sleep=lambda *_:None)
    sc.remove_duplicate_uris("tok","S1",[{"uri":"u1","remove_count":1,"total_count":1}])
    call=fake_session.calls[-1]
    assert call["method"]=="delete"
    body=call["json"]
    assert body["snapshot_id"]=="S1"
    assert body["tracks"]==[{"uri":"u1"}]          # uri-only, no positions
    assert not any(c["method"]=="post" for c in fake_session.calls)  # no re-add
```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the class. `refresh_token` mirrors the shipped retry/error-body function but on `self.session`. `fetch_playlist` paginates GET, reads `external_ids.isrc`, tracks integer `position`, returns snapshot from the first page. `remove_duplicate_uris` chunks the `removals` uris into ≤100-object DELETE bodies (`{tracks:[{uri}], snapshot_id}`, each with the SAME `snapshot_id`), then re-adds `keep = total_count - remove_count` copies via POST for any same-uri dup. `replace_with_verify` does the PUT/POST then re-fetch + set compare, raising `PlaylistVerifyError` on mismatch. Define `class PlaylistVerifyError(RuntimeError)`.
- [ ] **Step 4: Run** → PASS. Add a `replace_with_verify` test where the re-fetch returns a short set → raises `PlaylistVerifyError`.
- [ ] **Step 5: Commit** `feat: kexp.spotify_client (auth, ISRC read, safe DELETE, verified reorder)`.

---

### Task 7: `kexp/kexp_client.py` — plays + host check

**Files:** Create `kexp/kexp_client.py`, `tests/test_kexp_client.py`

**Interfaces:**
- Produces class `KexpClient(session)` with `fetch_plays(after_iso, before_iso, limit=200) -> list`, `is_john_show(show_uri) -> bool` (per-uri cache dict; does NOT memoize failures; narrows to `(requests.RequestException, ValueError)`).

- [ ] **Step 1: Failing test** — a lookup failure returns False, is not cached, and re-checks on the next call (port the shipped test using a failing `session.get`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** by moving the shipped `is_john_show`/`fetch_kexp_plays` behavior into the class, cache keyed by `show_uri`, only successful determinations cached.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: kexp.kexp_client plays + cached host check`.

---

### Task 8: `kexp/backup.py` — snapshot + prune

**Files:** Create `kexp/backup.py`, `tests/test_backup.py`

**Interfaces:**
- Produces: `write_backup(dir, snapshot_id, uris, *, now_str) -> str` (writes `playlist-<now_str>.json` with `{snapshot_id, uris, count}`, returns path); `prune(dir, keep=30) -> int` (deletes oldest beyond `keep`, returns #deleted). `now_str` injected (no `datetime.now` in the function — deterministic tests).

- [ ] **Step 1: Failing test** — write creates a parseable file with the uris + count; prune keeps newest `keep` by filename sort.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: kexp.backup snapshot + prune`.

---

### Task 9: `kexp/alerting.py` — failure email + heartbeat

**Files:** Create `kexp/alerting.py`, `tests/test_alerting.py`

**Interfaces:**
- Produces: `write_heartbeat(path, *, now_str, counts)`; `send_failure_email(config, *, subject, body, smtp_factory=smtplib.SMTP) -> bool` (returns False when SMTP config missing; uses injected `smtp_factory` so tests pass a fake).

- [ ] **Step 1: Failing test** — with a fake `smtp_factory` recording `send_message`, `send_failure_email` returns True and sends; with missing SMTP config returns False. `write_heartbeat` writes timestamp+counts JSON.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: kexp.alerting failure email + heartbeat`.

---

### Task 10: `kexp/emailer.py` — daily summary + near-dupe report

**Files:** Create `kexp/emailer.py`, `tests/test_emailer.py`

**Interfaces:**
- Produces: `render_daily(rows, near_dupe_report, *, date_str, prefix) -> (subject, body)` — body lists today's added tracks and, when `near_dupe_report` is non-empty, a "Possible duplicates to review" section (version-distinct groups). Sending stays in `alerting`/`pipeline`; this module is pure rendering.

- [ ] **Step 1: Failing test** — report section appears only when there are entries; subject includes the count.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** pure string rendering.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: kexp.emailer daily summary + near-dupe report`.

---

### Task 11: `kexp/pipeline.py` — orchestration (add-time dedupe, safe writes, alert wrap)

**Files:** Create `kexp/pipeline.py`, `tests/test_pipeline.py`. Modify `scripts/run.py` to `from kexp.pipeline import main; main()`.

**Interfaces:**
- Consumes: all modules above.
- Produces: `run_dedupe(client, token, playlist_id, backup_dir, *, now_str, dry_run) -> (removed, report)` (real shipped signature) — fetch playlist, `classify`, backup, convert `plan.remove_positions` into per-uri `{uri, remove_count, total_count}` removals and call `remove_duplicate_uris`, return counts + report; `main()` — wraps the whole run in try/except → `alerting.send_failure_email` + re-raise; writes heartbeat on success; live/backfill add path calls `matching.search_best` and skips add-time dupes using the pre-fetched id/isrc/safe-key sets.

- [ ] **Step 1: Failing test** for `run_dedupe`: seed a `SpotifyClient` (fake session) whose `fetch_playlist` returns items with one exact dup; assert a uri-based DELETE was issued (and, for a same-uri dup, one kept copy re-added) and a backup file was written.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `run_dedupe` and the add-time-skip helper `build_membership(items) -> (ids, isrcs, safe_keys)`; wire `main()` to reuse the existing backfill/live/email/reorder flow but through the new modules, guarding time-gates as today.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: kexp.pipeline orchestration with add-time dedupe + safe writes`.

---

### Task 12: Thin `scripts/run.py` + end-to-end dry-run test

**Files:** Modify `scripts/run.py` (reduce to entrypoint). Create `tests/test_e2e_dryrun.py`.

**Interfaces:**
- Consumes: `kexp.pipeline.main`.
- Produces: `scripts/run.py` = `#!/usr/bin/env python3` + `from kexp.pipeline import main` + `if __name__=="__main__": main()`.

- [ ] **Step 1: Failing test** — with `DO_SPOTIFY_ADDS=0` and a fake client whose playlist has dupes, running the dedupe path performs NO `delete`/`put`/`post` calls (dry-run asserts zero mutating calls) and still writes a heartbeat.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the `DO_SPOTIFY_ADDS`/dry-run guard in `pipeline` (a `dry_run` flag that short-circuits every mutating client call) and shrink `run.py`.
- [ ] **Step 4: Run full suite** `pytest` → all PASS.
- [ ] **Step 5: Commit** `refactor: thin run.py entrypoint + e2e dry-run test`.

---

### Task 13: One-time near-dupe cleanup verification (live-safe)

**Files:** Create `tests/test_current_playlist_cleanup.py` (offline fixture of the 22 near-dup groups).

**Interfaces:** Consumes `kexp.dedupe.classify`.

- [ ] **Step 1: Test** — feed a fixture representing the current 22 near-dup groups (from `near_dupe_review.txt`): assert `classify` auto-removes the **19 safe** ones and **reports the 3** version-distinct ones. Reported (kept, never auto-removed): Frankie Goes To Hollywood — Relax (Come Fighting)/Relax; Michael Kiwanuka — Cold Little Heart/Radio Edit; R.E.M. — Finest Worksong/Remastered. **Stop vs Stop!** differ only by punctuation, which `normalize_title` strips, so per the design's rule (punctuation-only match = SAFE) it IS auto-removed (keeping the earliest), NOT reported. This proves the first live run cleans exactly what we expect.
- [ ] **Step 2: Run** → PASS (implementation already exists from Task 5).
- [ ] **Step 3: Commit** `test: verify one-time cleanup of current 22 near-dupes`.

---

## Self-Review

- **Spec coverage:** package layout (Tasks 1–12), safe DELETE dedupe (Task 6), ISRC+hybrid (Tasks 4–5), add-time dedupe (Task 11), backup (Task 8), alerting (Task 9), better matching (Task 4), reorder verify/restore (Task 6 `replace_with_verify` + Task 11 wiring), one-time cleanup (Task 13), tests/dry-run (Tasks 0,12). ✔
- **Placeholder scan:** every code step shows real code or a precise port of an already-shipped function. ✔
- **Type consistency:** `classify`→`DedupePlan.remove_positions` consumed by `SpotifyClient.remove_duplicate_uris(removals)`; note Task 11 converts `remove_positions` (flat positions) into per-uri `{uri, remove_count, total_count}` removals using the items list — called out in Task 11 Step 3. ✔

## Execution Handoff

Plan saved. Executing via **subagent-driven development** (fresh subagent per task, review between tasks), per the autonomous SDLC directive.
