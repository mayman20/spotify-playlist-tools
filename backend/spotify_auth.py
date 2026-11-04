from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import spotipy

from .config import Settings
from .db import get_tokens, is_expired, upsert_tokens
from .security import hash_session_key, new_session_key


AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
ME_URL = "https://api.spotify.com/v1/me"
SCOPES = (
    "user-library-read "
    "user-top-read "
    "user-read-recently-played "
    "playlist-read-private "
    "playlist-read-collaborative "
    "playlist-modify-private "
    "playlist-modify-public"
)


def build_authorize_url(settings: Settings, state: str) -> str:
    params = {
        "client_id": settings.spotify_client_id,
        "response_type": "code",
        "redirect_uri": settings.spotify_redirect_uri,
        "scope": SCOPES,
        "state": state,
        "show_dialog": "true",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_tokens(settings: Settings, code: str) -> dict:
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.spotify_redirect_uri,
            "client_id": settings.spotify_client_id,
            "client_secret": settings.spotify_client_secret,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(settings: Settings, refresh_token: str) -> dict:
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings.spotify_client_id,
            "client_secret": settings.spotify_client_secret,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def get_me(access_token: str) -> dict:
    resp = httpx.get(ME_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def store_login_tokens(settings: Settings, token_data: dict) -> dict:
    access_token = token_data["access_token"]
    refresh_token = token_data["refresh_token"]
    expires_in = int(token_data.get("expires_in", 3600))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    me = get_me(access_token)
    spotify_user_id = me["id"]
    display_name = me.get("display_name") or spotify_user_id
    session_key = new_session_key()
    upsert_tokens(
        settings=settings,
        spotify_user_id=spotify_user_id,
        display_name=display_name,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        session_key_hash=hash_session_key(session_key),
    )
    return {"spotify_user_id": spotify_user_id, "display_name": display_name, "session_key": session_key}


def get_spotify_client_for_user(settings: Settings, spotify_user_id: str) -> tuple[spotipy.Spotify, dict]:
    row = get_tokens(settings, spotify_user_id)
    if not row:
        raise ValueError("No stored Spotify tokens for user.")

    access_token = row["access_token"]
    refresh_token = row["refresh_token"]
    expires_at = row["expires_at"]

    if is_expired(expires_at):
        refreshed = refresh_access_token(settings, refresh_token)
        access_token = refreshed["access_token"]
        refresh_token = refreshed.get("refresh_token", refresh_token)
        expires_in = int(refreshed.get("expires_in", 3600))
        new_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        upsert_tokens(
            settings=settings,
            spotify_user_id=row["spotify_user_id"],
            display_name=row["display_name"] or row["spotify_user_id"],
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=new_expires_at,
            session_key_hash=row.get("session_key_hash"),
        )

    return spotipy.Spotify(auth=access_token), row
