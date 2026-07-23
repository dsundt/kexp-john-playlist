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
        if not q:
            raise AssertionError(f"unqueued {method.upper()} {url}")
        return q.pop(0)
    def get(self, url, **kw): return self._next("get", url, **kw)
    def post(self, url, **kw): return self._next("post", url, **kw)
    def put(self, url, **kw): return self._next("put", url, **kw)
    def delete(self, url, **kw): return self._next("delete", url, **kw)
    def queue(self, method, *responses): self.queues[method].extend(responses)

@pytest.fixture
def fake_session():
    return FakeSession()
