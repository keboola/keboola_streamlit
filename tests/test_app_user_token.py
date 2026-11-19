import base64
import json
import logging
import os
import sys
import threading

import pytest
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from keboola_streamlit import AppUserTokenError, get_app_user_key, get_app_user_token  # noqa: E402
from keboola_streamlit import _app_user_token as module  # noqa: E402

NOW = 1_000_000.0
PUBLIC_URL = "https://my-app-123.hub.example.com"
REFRESH_URL = PUBLIC_URL + "/_proxy/app-user-token"


def jwt(exp=None, **claims):
    def part(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    if exp is not None:
        claims["exp"] = exp
    return f"{part({'alg': 'ES256'})}.{part(claims)}.c2lnbmF0dXJl"


HANDSHAKE_TOKEN = jwt(exp=NOW + 300, idp="https://idp.example.com", sub="user-1", jti="handshake")
REFRESH_TOKEN = jwt(exp=NOW + 3600, jti="refresh-1")


def headers(token=HANDSHAKE_TOKEN, refresh_token=REFRESH_TOKEN, public_url=PUBLIC_URL):
    result = {}
    if token is not None:
        result["X-Kbc-App-User-Token"] = token
    if refresh_token is not None:
        result["X-Kbc-App-User-Refresh-Token"] = refresh_token
    if public_url is not None:
        result["X-Kbc-App-Public-Url"] = public_url
    return result


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no JSON body")
        return self._body


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


class FakePost:
    """Answers each POST with the next response; an exception in the list is raised."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, url, **kwargs):
        with self.lock:
            self.calls.append((url, kwargs))
            response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


def refreshed(exp, jti="refreshed"):
    token = jwt(exp=exp, idp="https://idp.example.com", sub="user-1", jti=jti)
    return token, FakeResponse(200, {"token": token, "expiresAt": "2000-01-01T00:00:00Z"})


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(module, "_now", clock)
    return clock


@pytest.fixture(autouse=True)
def clear_refreshers():
    module._REFRESHERS.clear()
    yield
    module._REFRESHERS.clear()


def use_post(monkeypatch, *responses):
    post = FakePost(*responses)
    monkeypatch.setattr(module.requests, "post", post)
    return post


def test_no_token_header_gives_none(clock, monkeypatch):
    post = use_post(monkeypatch, FakeResponse(500))

    assert get_app_user_token(headers(token=None)) is None
    assert get_app_user_token(headers(token="")) is None
    assert post.calls == []


@pytest.mark.parametrize(
    "missing",
    [{"refresh_token": None}, {"public_url": None}, {"refresh_token": ""}, {"public_url": ""}],
)
def test_without_refresh_headers_the_handshake_token_is_used(clock, monkeypatch, missing):
    post = use_post(monkeypatch, FakeResponse(500))
    clock.now = NOW + 299

    assert get_app_user_token(headers(**missing)) == HANDSHAKE_TOKEN
    assert post.calls == []


def test_token_far_from_expiry_is_not_refreshed(clock, monkeypatch):
    post = use_post(monkeypatch, FakeResponse(500))
    clock.now = NOW + 239

    assert get_app_user_token(headers()) == HANDSHAKE_TOKEN
    assert post.calls == []


def test_token_near_expiry_is_refreshed_once(clock, monkeypatch):
    new_token, response = refreshed(exp=NOW + 600)
    post = use_post(monkeypatch, response)
    clock.now = NOW + 241

    assert get_app_user_token(headers()) == new_token
    assert get_app_user_token(headers()) == new_token

    assert len(post.calls) == 1
    url, kwargs = post.calls[0]
    assert url == REFRESH_URL
    assert kwargs["headers"] == {"Authorization": f"Bearer {REFRESH_TOKEN}"}
    assert kwargs["allow_redirects"] is False
    assert 0 < kwargs["timeout"] <= 30


def test_refreshed_token_is_refreshed_again_near_its_expiry(clock, monkeypatch):
    first, first_response = refreshed(exp=NOW + 600, jti="first")
    second, second_response = refreshed(exp=NOW + 900, jti="second")
    post = use_post(monkeypatch, first_response, second_response)

    clock.now = NOW + 250
    assert get_app_user_token(headers()) == first
    clock.now = NOW + 541
    assert get_app_user_token(headers()) == second
    assert len(post.calls) == 2


def test_trailing_slash_of_public_url_is_dropped(clock, monkeypatch):
    _, response = refreshed(exp=NOW + 600)
    post = use_post(monkeypatch, response)
    clock.now = NOW + 290

    get_app_user_token(headers(public_url=PUBLIC_URL + "/"))

    assert post.calls[0][0] == REFRESH_URL


def test_parallel_callers_share_one_refresh(clock, monkeypatch):
    new_token, response = refreshed(exp=NOW + 600)
    callers = 8
    barrier = threading.Barrier(callers)
    post = FakePost(response)

    def slow_post(url, **kwargs):
        threading.Event().wait(0.05)
        return post(url, **kwargs)

    monkeypatch.setattr(module.requests, "post", slow_post)
    clock.now = NOW + 290
    results = []

    def call():
        barrier.wait()
        results.append(get_app_user_token(headers()))

    threads = [threading.Thread(target=call) for _ in range(callers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [new_token] * callers
    assert len(post.calls) == 1


def test_401_stops_refreshing_and_keeps_the_valid_token(clock, monkeypatch):
    post = use_post(monkeypatch, FakeResponse(401))
    clock.now = NOW + 250

    assert get_app_user_token(headers()) == HANDSHAKE_TOKEN
    clock.now = NOW + 290
    assert get_app_user_token(headers()) == HANDSHAKE_TOKEN
    assert len(post.calls) == 1


def test_after_401_an_expired_token_raises_a_sign_in_error(clock, monkeypatch):
    post = use_post(monkeypatch, FakeResponse(401))
    clock.now = NOW + 250
    get_app_user_token(headers())

    clock.now = NOW + 300
    with pytest.raises(AppUserTokenError) as error:
        get_app_user_token(headers())

    assert "Reload the page" in str(error.value)
    assert len(post.calls) == 1


def test_a_new_refresh_token_after_reconnect_refreshes_again(clock, monkeypatch):
    new_token, response = refreshed(exp=NOW + 4000)
    use_post(monkeypatch, FakeResponse(401), response)
    clock.now = NOW + 250
    get_app_user_token(headers())

    clock.now = NOW + 3700
    reconnect_token = jwt(exp=NOW + 3750, idp="https://idp.example.com", sub="user-1", jti="handshake-2")
    reconnect_refresh = jwt(exp=NOW + 7200, jti="refresh-2")

    assert get_app_user_token(headers(token=reconnect_token, refresh_token=reconnect_refresh)) == new_token


def test_a_reconnect_before_the_old_refresh_token_expires_uses_the_new_one(clock, monkeypatch):
    new_token, response = refreshed(exp=NOW + 900)
    post = use_post(monkeypatch, FakeResponse(401), response)
    clock.now = NOW + 250
    get_app_user_token(headers())

    clock.now = NOW + 290
    reconnect_token = jwt(exp=NOW + 320, idp="https://idp.example.com", sub="user-1", jti="handshake-2")
    reconnect_refresh = jwt(exp=NOW + 3890, jti="refresh-2")

    assert get_app_user_token(headers(token=reconnect_token, refresh_token=reconnect_refresh)) == new_token
    assert post.calls[1][1]["headers"] == {"Authorization": f"Bearer {reconnect_refresh}"}


@pytest.mark.parametrize(
    "failure",
    [FakeResponse(503), FakeResponse(404), FakeResponse(500), requests.ConnectionError("down"), requests.Timeout()],
)
def test_temporary_failure_keeps_the_valid_token_and_backs_off(clock, monkeypatch, failure):
    new_token, response = refreshed(exp=NOW + 600)
    post = use_post(monkeypatch, failure, response)
    clock.now = NOW + 241

    assert get_app_user_token(headers()) == HANDSHAKE_TOKEN
    assert get_app_user_token(headers()) == HANDSHAKE_TOKEN
    assert len(post.calls) == 1

    clock.now = NOW + 241 + module.REFRESH_RETRY_DELAYS_SECONDS[0]
    assert get_app_user_token(headers()) == new_token
    assert len(post.calls) == 2


def test_backoff_grows_and_is_capped(clock, monkeypatch):
    post = use_post(monkeypatch, FakeResponse(503))
    clock.now = NOW + 241
    get_app_user_token(headers())

    def call():
        try:
            get_app_user_token(headers())
        except AppUserTokenError:
            pass

    for delay in list(module.REFRESH_RETRY_DELAYS_SECONDS) + [module.REFRESH_RETRY_DELAYS_SECONDS[-1]]:
        calls = len(post.calls)
        clock.now += delay - 0.5
        call()
        assert len(post.calls) == calls
        clock.now += 0.5
        call()
        assert len(post.calls) == calls + 1


@pytest.mark.parametrize(
    "response",
    [FakeResponse(200), FakeResponse(200, {"token": ""}), FakeResponse(200, {"expiresAt": "x"}), FakeResponse(200, [])],
)
def test_malformed_answer_is_a_temporary_failure(clock, monkeypatch, response):
    post = use_post(monkeypatch, response)
    clock.now = NOW + 241

    assert get_app_user_token(headers()) == HANDSHAKE_TOKEN
    assert get_app_user_token(headers()) == HANDSHAKE_TOKEN
    assert len(post.calls) == 1


def test_expiry_without_exp_claim_comes_from_expires_at(clock, monkeypatch):
    token_without_exp = jwt(idp="https://idp.example.com", sub="user-1")
    post = use_post(monkeypatch, FakeResponse(200, {"token": token_without_exp, "expiresAt": "1970-01-12T14:06:40Z"}))
    clock.now = NOW + 241

    assert get_app_user_token(headers()) == token_without_exp
    clock.now = NOW + 539
    assert get_app_user_token(headers()) == token_without_exp
    assert len(post.calls) == 1


def test_temporary_failure_with_an_expired_token_raises_a_retry_error(clock, monkeypatch):
    use_post(monkeypatch, FakeResponse(503))
    clock.now = NOW + 301

    with pytest.raises(AppUserTokenError) as error:
        get_app_user_token(headers())

    assert "Reload" not in str(error.value)


def test_errors_and_logs_never_hold_a_token(clock, monkeypatch, caplog):
    use_post(monkeypatch, FakeResponse(503), FakeResponse(401))
    caplog.set_level(logging.DEBUG)
    clock.now = NOW + 301
    messages = []
    for _ in range(2):
        with pytest.raises(AppUserTokenError) as error:
            get_app_user_token(headers())
        messages.append(str(error.value))
        clock.now += module.REFRESH_RETRY_DELAYS_SECONDS[0]

    text = " ".join(messages) + " ".join(record.getMessage() for record in caplog.records)
    assert caplog.records
    assert HANDSHAKE_TOKEN not in text
    assert REFRESH_TOKEN not in text
    assert REFRESH_TOKEN.split(".")[1] not in text


def test_refreshers_of_expired_refresh_tokens_are_dropped(clock, monkeypatch):
    use_post(monkeypatch, FakeResponse(401))
    clock.now = NOW + 250
    get_app_user_token(headers())
    assert len(module._REFRESHERS) == 1

    clock.now = NOW + 3601
    other_refresh = jwt(exp=NOW + 7200, jti="refresh-2")
    get_app_user_token(headers(token=jwt(exp=NOW + 3900), refresh_token=other_refresh))

    assert list(module._REFRESHERS) == [other_refresh]


def test_app_user_key_is_idp_and_sub(clock):
    assert get_app_user_key(headers()) == ("https://idp.example.com", "user-1")


@pytest.mark.parametrize("token", [None, ""])
def test_app_user_key_is_none_without_a_token(clock, token):
    assert get_app_user_key(headers(token=token)) is None


@pytest.mark.parametrize(
    "token", ["not-a-jwt", jwt(sub="user-1"), jwt(idp="https://idp.example.com"), jwt(idp="", sub="user-1")]
)
def test_app_user_key_of_an_unreadable_token_raises(clock, token):
    with pytest.raises(AppUserTokenError) as error:
        get_app_user_key(headers(token=token))

    assert token not in str(error.value)


def test_app_user_key_does_not_refresh(clock, monkeypatch):
    post = use_post(monkeypatch, FakeResponse(500))
    clock.now = NOW + 299

    get_app_user_key(headers())

    assert post.calls == []


def test_a_token_capped_at_the_refresh_token_expiry_is_not_refreshed_again(clock, monkeypatch):
    capped, response = refreshed(exp=NOW + 3600)
    post = use_post(monkeypatch, response)
    clock.now = NOW + 3550

    assert get_app_user_token(headers()) == capped
    clock.now = NOW + 3551
    assert get_app_user_token(headers()) == capped
    clock.now = NOW + 3599
    assert get_app_user_token(headers()) == capped

    assert len(post.calls) == 1


def test_a_handshake_token_that_outlives_the_refresh_token_is_not_refreshed(clock, monkeypatch):
    post = use_post(monkeypatch, FakeResponse(500))
    short_refresh = jwt(exp=NOW + 280, jti="refresh-short")
    clock.now = NOW + 250

    assert get_app_user_token(headers(refresh_token=short_refresh)) == HANDSHAKE_TOKEN
    assert post.calls == []


def test_a_capped_token_that_expired_raises_without_a_refresh(clock, monkeypatch):
    capped, response = refreshed(exp=NOW + 3600)
    post = use_post(monkeypatch, response)
    clock.now = NOW + 3550
    get_app_user_token(headers())

    clock.now = NOW + 3600
    with pytest.raises(AppUserTokenError):
        get_app_user_token(headers())

    assert len(post.calls) == 1
