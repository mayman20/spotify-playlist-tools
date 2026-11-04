from urllib.parse import quote_plus

from dotenv import load_dotenv
import httpx

load_dotenv("backend/.env")

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from .config import Settings
from .db import delete_tokens, get_tokens, init_db
from .security import (
    build_session_callback_url,
    frontend_origins,
    is_session_key_current,
    is_allowed_return_url,
    make_session_token,
    make_state,
    read_session_token,
    read_state,
)
from .spotify_auth import build_authorize_url, exchange_code_for_tokens, get_spotify_client_for_user, store_login_tokens
from .tasks import (
    get_artist_catalog_depth,
    get_automation_targets,
    get_dashboard_overview,
    get_genre_breakdown,
    get_genre_playlist_recommendations,
    get_listening_pattern,
    get_mood_timeline,
    get_playlist_freshness,
    get_recently_played,
    get_track_longevity,
    get_top_lists,
    run_archive_stale_playlists,
    run_liked_add,
    run_vaulted_add,
    search_artists,
)

settings = Settings()
app = FastAPI(title="Spotipy Scripts API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins(settings.frontend_url, settings.app_env),
    allow_origin_regex=(
        r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$" if settings.app_env == "development" else None
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    settings.validate()
    init_db(settings)


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header.")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header.")
    token = parts[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    return token


def _current_user_id(authorization: str | None) -> str:
    token = _extract_bearer_token(authorization)
    session = read_session_token(settings, token)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session token.")
    row = get_tokens(settings, session["spotify_user_id"])
    if not row or not is_session_key_current(session["session_key"], row.get("session_key_hash")):
        raise HTTPException(status_code=401, detail="Session has been revoked. Please connect Spotify again.")
    return session["spotify_user_id"]


@app.get("/")
def root() -> dict:
    return {"ok": True, "service": "spotipy_scripts_backend"}


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/auth/login")
def auth_login(request: Request, return_to: str | None = None) -> RedirectResponse:
    resolved_return_to = settings.frontend_url
    if return_to and is_allowed_return_url(return_to, settings.frontend_url, settings.app_env):
        resolved_return_to = return_to
    else:
        referer = request.headers.get("referer", "")
        if referer and is_allowed_return_url(referer, settings.frontend_url, settings.app_env):
            resolved_return_to = referer
    state = make_state(settings, {"return_to": resolved_return_to})
    return RedirectResponse(build_authorize_url(settings, state), status_code=302)


@app.get("/auth/callback")
def auth_callback(code: str | None = None, state: str | None = None, error: str | None = None) -> RedirectResponse:
    if error:
        target = f"{settings.frontend_url}?login_error={quote_plus(error)}"
        return RedirectResponse(target, status_code=302)
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state.")

    payload = read_state(settings, state)
    if not payload:
        raise HTTPException(status_code=400, detail="Invalid OAuth state.")

    return_to = str(payload.get("return_to") or settings.frontend_url)
    if not is_allowed_return_url(return_to, settings.frontend_url, settings.app_env):
        return_to = settings.frontend_url

    try:
        token_data = exchange_code_for_tokens(settings, code)
        user = store_login_tokens(settings, token_data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        # Common case: app is still in development mode and this Spotify user is not allowlisted.
        if status == 403:
            err = "spotify_forbidden_user_or_app_mode"
        else:
            err = f"spotify_http_{status}"
        target = f"{return_to}?login_error={quote_plus(err)}"
        return RedirectResponse(target, status_code=302)
    except Exception:
        target = f"{return_to}?login_error={quote_plus('spotify_auth_failed')}"
        return RedirectResponse(target, status_code=302)

    session_token = make_session_token(settings, user["spotify_user_id"], user["session_key"])
    target = build_session_callback_url(return_to, session_token, user["spotify_user_id"])
    return RedirectResponse(target, status_code=302)


@app.get("/me")
def me(authorization: str | None = Header(default=None, alias="Authorization")) -> dict:
    spotify_user_id = _current_user_id(authorization)
    row = get_tokens(settings, spotify_user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"spotify_user_id": row["spotify_user_id"], "display_name": row["display_name"]}


@app.get("/stats/overview")
def stats_overview(
    time_range: str = "short_term",
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    spotify_user_id = _current_user_id(authorization)
    sp, _ = get_spotify_client_for_user(settings, spotify_user_id)
    overview = get_dashboard_overview(sp, time_range=time_range)
    return {"ok": True, "overview": overview}


@app.get("/stats/top")
def stats_top(
    time_range: str = "short_term",
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    spotify_user_id = _current_user_id(authorization)
    sp, _ = get_spotify_client_for_user(settings, spotify_user_id)
    data = get_top_lists(sp, time_range=time_range)
    return {"ok": True, "data": data}


@app.get("/stats/track-longevity")
def stats_track_longevity(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    spotify_user_id = _current_user_id(authorization)
    sp, _ = get_spotify_client_for_user(settings, spotify_user_id)
    data = get_track_longevity(sp)
    return {"ok": True, "data": data}


@app.get("/recommendations/genre-playlists")
def recommendations_genre_playlists(
    time_range: str = "medium_term",
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    spotify_user_id = _current_user_id(authorization)
    sp, _ = get_spotify_client_for_user(settings, spotify_user_id)
    data = get_genre_playlist_recommendations(sp, time_range=time_range)
    return {"ok": True, "data": data}


@app.get("/automation/targets")
def automation_targets(authorization: str | None = Header(default=None, alias="Authorization")) -> dict:
    spotify_user_id = _current_user_id(authorization)
    sp, _ = get_spotify_client_for_user(settings, spotify_user_id)
    targets = get_automation_targets(sp)
    return {"ok": True, "targets": targets}


class RunRequest(BaseModel):
    target_playlist_id: str | None = None
    target_playlist_name: str | None = None
    dry_run: bool = False


class ArchiveStaleRequest(BaseModel):
    max_freshness_score: int = 30
    prefix: str = "[Archive]"


@app.post("/run/vaulted")
def run_vaulted(
    body: RunRequest | None = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    spotify_user_id = _current_user_id(authorization)
    sp, _ = get_spotify_client_for_user(settings, spotify_user_id)
    playlist_name = body.target_playlist_name if body and body.target_playlist_name else "_vaulted"
    playlist_id = body.target_playlist_id if body and body.target_playlist_id else None
    result = run_vaulted_add(sp, playlist_name=playlist_name, playlist_id=playlist_id, dry_run=body.dry_run if body else False)
    return {"ok": True, "script": "vaulted_add", "result": result}


@app.post("/run/liked")
def run_liked(
    body: RunRequest | None = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    spotify_user_id = _current_user_id(authorization)
    sp, _ = get_spotify_client_for_user(settings, spotify_user_id)
    playlist_name = body.target_playlist_name if body and body.target_playlist_name else "Liked Songs Mirror"
    playlist_id = body.target_playlist_id if body and body.target_playlist_id else None
    result = run_liked_add(sp, playlist_name=playlist_name, playlist_id=playlist_id, dry_run=body.dry_run if body else False)
    return {"ok": True, "script": "liked_add", "result": result}


@app.post("/logout")
def logout(authorization: str | None = Header(default=None, alias="Authorization")) -> dict:
    spotify_user_id = _current_user_id(authorization)
    delete_tokens(settings, spotify_user_id)
    return {"ok": True}


@app.get("/stats/recently-played")
def stats_recently_played(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    spotify_user_id = _current_user_id(authorization)
    sp, _ = get_spotify_client_for_user(settings, spotify_user_id)
    data = get_recently_played(sp)
    return {"ok": True, "data": data}


@app.get("/stats/listening-pattern")
def stats_listening_pattern(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    spotify_user_id = _current_user_id(authorization)
    sp, _ = get_spotify_client_for_user(settings, spotify_user_id)
    data = get_listening_pattern(sp)
    return {"ok": True, "data": data}


@app.get("/search/artists")
def search_artists_endpoint(
    q: str = "",
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    spotify_user_id = _current_user_id(authorization)
    sp, _ = get_spotify_client_for_user(settings, spotify_user_id)
    data = search_artists(sp, q)
    return {"ok": True, "data": data}


@app.get("/stats/artist-catalog")
def stats_artist_catalog(
    artist_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    spotify_user_id = _current_user_id(authorization)
    sp, _ = get_spotify_client_for_user(settings, spotify_user_id)
    data = get_artist_catalog_depth(sp, artist_id)
    return {"ok": True, "data": data}


@app.get("/stats/genre-breakdown")
def stats_genre_breakdown(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    spotify_user_id = _current_user_id(authorization)
    sp, _ = get_spotify_client_for_user(settings, spotify_user_id)
    data = get_genre_breakdown(sp)
    return {"ok": True, "data": data}


@app.get("/stats/mood-timeline")
def stats_mood_timeline(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    spotify_user_id = _current_user_id(authorization)
    sp, _ = get_spotify_client_for_user(settings, spotify_user_id)
    data = get_mood_timeline(sp)
    return {"ok": True, "data": data}


@app.get("/stats/playlist-freshness")
def stats_playlist_freshness(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    spotify_user_id = _current_user_id(authorization)
    sp, _ = get_spotify_client_for_user(settings, spotify_user_id)
    data = get_playlist_freshness(sp)
    return {"ok": True, "data": data}


@app.post("/run/archive-stale")
def run_archive_stale(
    body: ArchiveStaleRequest | None = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    spotify_user_id = _current_user_id(authorization)
    sp, _ = get_spotify_client_for_user(settings, spotify_user_id)
    threshold = body.max_freshness_score if body else 30
    prefix = body.prefix if body else "[Archive]"
    result = run_archive_stale_playlists(sp, max_freshness_score=threshold, prefix=prefix)
    return {"ok": True, "script": "archive_stale_playlists", "result": result}
