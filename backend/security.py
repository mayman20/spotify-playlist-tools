import hmac
from hashlib import sha256
from secrets import token_urlsafe
from urllib.parse import urlencode, urlparse, urlsplit, urlunsplit

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import Settings


STATE_SALT = "spotify-auth-state"
SESSION_SALT = "spotify-session-token"


def get_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.app_secret_key)


def frontend_origins(frontend_url: str, app_env: str) -> list[str]:
    if not frontend_url:
        return []

    candidates = {frontend_url.rstrip("/")}
    parsed = urlparse(frontend_url)
    if parsed.scheme and parsed.netloc:
        candidates.add(f"{parsed.scheme}://{parsed.netloc}")
    if app_env == "development":
        candidates.update(
            {
                "http://127.0.0.1:5173",
                "http://localhost:5173",
                "http://127.0.0.1:8080",
                "http://localhost:8080",
            }
        )
    return sorted(candidates)


def is_allowed_return_url(return_to: str, frontend_url: str, app_env: str) -> bool:
    try:
        parsed = urlparse(return_to)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        host = (parsed.hostname or "").lower()
        is_loopback_name = host in {"localhost", "127.0.0.1"}
        if is_loopback_name and app_env != "development":
            return False
        normalized_origin = f"{parsed.scheme}://{parsed.netloc}"
    except (TypeError, ValueError):
        return False

    if normalized_origin in frontend_origins(frontend_url, app_env):
        return True
    return app_env == "development" and is_loopback_name


def make_state(settings: Settings, payload: dict) -> str:
    return get_serializer(settings).dumps(payload, salt=STATE_SALT)


def read_state(settings: Settings, token: str, max_age_seconds: int = 600) -> dict | None:
    try:
        return get_serializer(settings).loads(token, salt=STATE_SALT, max_age=max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None


def new_session_key() -> str:
    return token_urlsafe(32)


def hash_session_key(session_key: str) -> str:
    return sha256(session_key.encode("utf-8")).hexdigest()


def is_session_key_current(session_key: str, stored_hash: str | None) -> bool:
    if not session_key or not stored_hash:
        return False
    return hmac.compare_digest(stored_hash, hash_session_key(session_key))


def build_session_callback_url(return_to: str, session_token: str, spotify_user_id: str) -> str:
    parsed = urlsplit(return_to)._replace(fragment="")
    fragment = "?" + urlencode({"session_token": session_token, "spotify_user_id": spotify_user_id})
    return urlunsplit(parsed._replace(fragment=fragment))


def make_session_token(settings: Settings, spotify_user_id: str, session_key: str) -> str:
    return get_serializer(settings).dumps(
        {"spotify_user_id": spotify_user_id, "session_key": session_key},
        salt=SESSION_SALT,
    )


def read_session_token(
    settings: Settings,
    token: str,
    max_age_seconds: int = 60 * 60 * 24 * 14,
) -> dict[str, str] | None:
    try:
        payload = get_serializer(settings).loads(token, salt=SESSION_SALT, max_age=max_age_seconds)
        spotify_user_id = str(payload.get("spotify_user_id", "")).strip()
        session_key = str(payload.get("session_key", "")).strip()
        if not spotify_user_id or not session_key:
            return None
        return {"spotify_user_id": spotify_user_id, "session_key": session_key}
    except (BadSignature, SignatureExpired):
        return None
