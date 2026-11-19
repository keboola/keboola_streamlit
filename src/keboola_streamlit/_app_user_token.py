from __future__ import annotations

import base64
import json
import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Tuple

import requests
import streamlit as st

TOKEN_HEADER = "X-Kbc-App-User-Token"
REFRESH_TOKEN_HEADER = "X-Kbc-App-User-Refresh-Token"
PUBLIC_URL_HEADER = "X-Kbc-App-Public-Url"
REFRESH_PATH = "/_proxy/app-user-token"
REFRESH_MARGIN_SECONDS = 60
REFRESH_TIMEOUT_SECONDS = 10
REFRESH_RETRY_DELAYS_SECONDS = (2, 4, 8, 16, 30)
# apps-proxy closes a websocket with a user token within 1 hour, and the refresh token expires with it.
REFRESH_TOKEN_MAX_LIFE_SECONDS = 3600

SIGN_IN_EXPIRED = "Your sign-in to this app has expired. Reload the page."
SIGN_IN_NOT_RENEWED = "Your sign-in to this app could not be renewed. Try again in a moment."
UNREADABLE_TOKEN = "The sign-in token of this app has no user."

_REFRESHERS: Dict[str, "_Refresher"] = {}
_REFRESHERS_LOCK = threading.Lock()


class AppUserTokenError(Exception):
    """The signed-in user's token has expired and no new one could be obtained."""


def _now() -> float:
    return time.time()


def get_app_user_token(headers: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """
    Returns a valid token of the signed-in user, or None when the request has none.

    Keboola's apps proxy adds the token after an OAuth/OIDC login. It lives 5 minutes. On a websocket (Streamlit) the
    proxy also adds a refresh token, and this function gets a new user token from the proxy before the current one
    expires.

    Args:
        headers (Optional[Mapping[str, str]]): The request headers. Defaults to st.context.headers.

    Raises:
        AppUserTokenError: The token has expired and the proxy gave no new one.
    """
    headers = st.context.headers if headers is None else headers
    token = headers.get(TOKEN_HEADER) or None
    refresh_token = headers.get(REFRESH_TOKEN_HEADER) or None
    public_url = headers.get(PUBLIC_URL_HEADER) or None
    if token is None or refresh_token is None or public_url is None:
        return token
    return _get_refresher(token, refresh_token, public_url).token()


def get_app_user_key(headers: Optional[Mapping[str, str]] = None) -> Optional[Tuple[str, str]]:
    """
    Returns the signed-in user as (idp, sub), or None when the request has no user token.

    The pair is the same for every token of one user, so it can key a per-user cache. The token is not verified:
    only the apps proxy sets the header, and it drops copies sent by the browser.

    Args:
        headers (Optional[Mapping[str, str]]): The request headers. Defaults to st.context.headers.

    Raises:
        AppUserTokenError: The token has no idp and sub.
    """
    headers = st.context.headers if headers is None else headers
    token = headers.get(TOKEN_HEADER) or None
    if token is None:
        return None
    claims = _claims(token)
    idp, sub = claims.get("idp"), claims.get("sub")
    if isinstance(idp, str) and isinstance(sub, str) and idp and sub:
        return (idp, sub)
    raise AppUserTokenError(UNREADABLE_TOKEN)


def _get_refresher(token: str, refresh_token: str, public_url: str) -> "_Refresher":
    now = _now()
    with _REFRESHERS_LOCK:
        for key in [key for key, refresher in _REFRESHERS.items() if refresher.refresh_token_expires_at <= now]:
            del _REFRESHERS[key]
        refresher = _REFRESHERS.get(refresh_token)
        if refresher is None:
            # A reconnected websocket brings a new refresh token, so a refresher refused with 401 is not reused.
            refresher = _Refresher(token, refresh_token, public_url, now)
            _REFRESHERS[refresh_token] = refresher
        return refresher


class _Refresher:
    def __init__(self, token: str, refresh_token: str, public_url: str, now: float):
        self._token = token
        self._expires_at = _expiry(token) or 0.0
        self._refresh_token = refresh_token
        self._url = public_url.rstrip("/") + REFRESH_PATH
        self.refresh_token_expires_at = _expiry(refresh_token) or now + REFRESH_TOKEN_MAX_LIFE_SECONDS
        self._refused = False
        self._failures = 0
        self._retry_at = 0.0
        self._lock = threading.Lock()

    def token(self) -> str:
        with self._lock:
            now = _now()
            if now < self._expires_at - REFRESH_MARGIN_SECONDS:
                return self._token
            if not self._refused and now >= self._retry_at:
                self._refresh(now)
            if _now() < self._expires_at:
                return self._token
            raise AppUserTokenError(SIGN_IN_EXPIRED if self._refused else SIGN_IN_NOT_RENEWED)

    def _refresh(self, now: float) -> None:
        try:
            response = requests.post(
                self._url,
                headers={"Authorization": f"Bearer {self._refresh_token}"},
                timeout=REFRESH_TIMEOUT_SECONDS,
                allow_redirects=False,
            )
        except requests.RequestException as e:
            self._fail(now, f"the request failed ({type(e).__name__})")
            return
        if response.status_code == 401:
            self._refused = True
            logging.warning("The apps proxy refused to renew the app user token (401); it renews after a reconnect.")
            return
        if response.status_code != 200:
            self._fail(now, f"status {response.status_code}")
            return
        token, expires_at = _parse_answer(response)
        if token is None or expires_at is None:
            self._fail(now, "an unexpected answer")
            return
        self._token, self._expires_at = token, expires_at
        self._failures, self._retry_at = 0, 0.0

    def _fail(self, now: float, reason: str) -> None:
        delay = REFRESH_RETRY_DELAYS_SECONDS[min(self._failures, len(REFRESH_RETRY_DELAYS_SECONDS) - 1)]
        self._failures += 1
        self._retry_at = now + delay
        logging.warning(f"Could not renew the app user token: {reason}. Next try in {delay} s.")


def _parse_answer(response: requests.Response) -> Tuple[Optional[str], Optional[float]]:
    try:
        body = response.json()
    except ValueError:
        return None, None
    if not isinstance(body, dict):
        return None, None
    token = body.get("token")
    if not isinstance(token, str) or not token:
        return None, None
    return token, _expiry(token) or _parse_time(body.get("expiresAt"))


def _parse_time(value: Any) -> Optional[float]:
    if not isinstance(value, str):
        return None
    try:
        # Python 3.10 does not read a "Z" suffix.
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _expiry(token: str) -> Optional[float]:
    exp = _claims(token).get("exp")
    if isinstance(exp, (int, float)) and not isinstance(exp, bool):
        return float(exp)
    return None


def _claims(token: str) -> Dict[str, Any]:
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}
    return claims if isinstance(claims, dict) else {}
