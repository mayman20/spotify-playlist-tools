import os
from urllib.parse import urlparse


class Settings:
    spotify_client_id: str
    spotify_client_secret: str
    spotify_redirect_uri: str
    database_url: str
    app_secret_key: str
    frontend_url: str
    app_env: str
    database_url_configured: bool

    def __init__(self) -> None:
        self.spotify_client_id = os.getenv("SPOTIPY_CLIENT_ID", "").strip()
        self.spotify_client_secret = os.getenv("SPOTIPY_CLIENT_SECRET", "").strip()
        self.spotify_redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI", "").strip()
        configured_database_url = os.getenv("DATABASE_URL", "").strip()
        self.database_url_configured = bool(configured_database_url)
        self.database_url = configured_database_url or "sqlite:///local_dev.db"
        self.app_secret_key = os.getenv("APP_SECRET_KEY", "").strip()
        self.frontend_url = os.getenv("FRONTEND_URL", "").strip()
        self.app_env = os.getenv("APP_ENV", "").strip().lower()

    def validate(self) -> None:
        missing = []
        if self.app_env not in {"development", "production"}:
            missing.append("APP_ENV (must be development or production)")
        if not self.spotify_client_id:
            missing.append("SPOTIPY_CLIENT_ID")
        if not self.spotify_client_secret:
            missing.append("SPOTIPY_CLIENT_SECRET")
        if not self.spotify_redirect_uri:
            missing.append("SPOTIPY_REDIRECT_URI")
        if not self.app_secret_key:
            missing.append("APP_SECRET_KEY")
        if not self.frontend_url:
            missing.append("FRONTEND_URL")
        if self.app_env == "production":
            if not self.database_url_configured:
                missing.append("DATABASE_URL (persistent production database required)")
            elif self.database_url.lower().startswith(("postgres://", "postgresql://")):
                pass
            elif self.database_url.lower().startswith("sqlite:"):
                sqlite_path = self.database_url.replace("sqlite:///", "", 1).replace("sqlite://", "", 1)
                if not os.path.isabs(sqlite_path) or sqlite_path == ":memory:":
                    missing.append("DATABASE_URL (production SQLite must use an absolute persistent path)")
            else:
                missing.append("DATABASE_URL (must use PostgreSQL or persistent SQLite)")

            if self.app_secret_key and (
                len(self.app_secret_key.encode("utf-8")) < 43 or len(set(self.app_secret_key)) < 16
            ):
                missing.append("APP_SECRET_KEY (use a high-entropy value, e.g. secrets.token_urlsafe(32))")

            if self.frontend_url:
                frontend = urlparse(self.frontend_url)
                if frontend.scheme != "https" or not frontend.netloc:
                    missing.append("FRONTEND_URL (production URL must use HTTPS)")
            if self.spotify_redirect_uri:
                redirect = urlparse(self.spotify_redirect_uri)
                if redirect.scheme != "https" or not redirect.netloc:
                    missing.append("SPOTIPY_REDIRECT_URI (production redirect URI must use HTTPS)")
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
