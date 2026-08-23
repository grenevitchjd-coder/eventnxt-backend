"""
EventNXT's connection to Events360 — the identity provider for the whole
suite. EventNXT has no login/account system of its own; every authenticated
request is validated by calling Events360's /oauth/userinfo endpoint
(token introspection), not by decoding a JWT locally. No shared secret
between the two services beyond the OAuth client_secret used once, up
front, to exchange a code for a token.
"""

import httpx

from app.config import settings


def build_authorize_redirect_url(state: str) -> str:
    """
    The URL EventNXT sends the browser to for "Sign in with Events360" —
    lands on the EVENTS360 FRONTEND's /oauth/authorize page (not a backend
    endpoint directly, since only the frontend can see if the user is
    already logged in via localStorage).
    """
    callback_url = f"{settings.eventnxt_backend_url}/auth/callback"
    params = httpx.QueryParams(
        {
            "client_id": settings.oauth_client_id,
            "redirect_uri": callback_url,
            "scope": "profile",
            "state": state,
        }
    )
    return f"{settings.events360_frontend_url}/oauth/authorize?{params}"


def exchange_code_for_token(code: str) -> str:
    """
    Server-to-server call (never happens in the browser) — trades a
    one-time code for an access token, using the client_secret that only
    EventNXT's backend knows. Raises on failure.
    """
    callback_url = f"{settings.eventnxt_backend_url}/auth/callback"
    response = httpx.post(
        f"{settings.events360_api_url}/oauth/token",
        json={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.oauth_client_id,
            "client_secret": settings.oauth_client_secret,
            "redirect_uri": callback_url,
        },
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def fetch_userinfo(access_token: str) -> dict:
    """
    Called on every authenticated EventNXT request to verify the token and
    get the identity behind it. Returns None-like behavior via exception
    if the token is invalid/expired — callers should catch httpx.HTTPStatusError.
    """
    response = httpx.get(
        f"{settings.events360_api_url}/oauth/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()