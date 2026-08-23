import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config import settings
from app.services.events360_client import build_authorize_redirect_url, exchange_code_for_token

router = APIRouter(prefix="/auth", tags=["auth"])

STATE_COOKIE = "eventnxt_oauth_state"


@router.get("/login")
def login():
    """
    "Sign in with Events360" — sends the browser to Events360's frontend
    authorize page. A random `state` value is set as an httponly cookie
    and passed through the redirect, then checked again in /auth/callback
    to guard against CSRF (someone tricking a user into completing a login
    flow they didn't start).
    """
    state = secrets.token_urlsafe(24)
    redirect_url = build_authorize_redirect_url(state)

    response = RedirectResponse(url=redirect_url, status_code=302)
    response.set_cookie(
        STATE_COOKIE, state, httponly=True, samesite="lax", max_age=600  # 10 minutes
    )
    return response


@router.get("/callback")
def callback(request: Request, code: str, state: str):
    """
    Events360 redirects the browser here after the user approves. Verifies
    the state cookie, exchanges the code for an access token (server-to-
    server, using the client_secret), then redirects the browser on to the
    EventNXT FRONTEND with that token so it can store it and proceed.
    """
    cookie_state = request.cookies.get(STATE_COOKIE)
    if not cookie_state or cookie_state != state:
        raise HTTPException(status_code=400, detail="Invalid or missing OAuth state — please try again.")

    access_token = exchange_code_for_token(code)

    frontend_complete_url = f"{settings.eventnxt_frontend_url}/auth/complete?token={access_token}"
    response = RedirectResponse(url=frontend_complete_url, status_code=302)
    response.delete_cookie(STATE_COOKIE)
    return response