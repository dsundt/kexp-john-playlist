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
