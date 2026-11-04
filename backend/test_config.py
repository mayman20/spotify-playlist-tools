import os
import secrets
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import Settings


class SettingsValidationTests(unittest.TestCase):
    def production_environment(self):
        return {
            "APP_ENV": "production",
            "SPOTIPY_CLIENT_ID": "client-id",
            "SPOTIPY_CLIENT_SECRET": "client-secret",
            "SPOTIPY_REDIRECT_URI": "https://api.example.test/auth/callback",
            "DATABASE_URL": "postgresql://user:pass@db.example.test/spotify",
            "APP_SECRET_KEY": secrets.token_urlsafe(32),
            "FRONTEND_URL": "https://music.example.test/",
        }

    def test_valid_production_settings(self):
        with patch.dict(os.environ, self.production_environment(), clear=True):
            Settings().validate()

    def test_app_environment_must_be_explicit(self):
        env = self.production_environment()
        env.pop("APP_ENV")
        with patch.dict(os.environ, env, clear=True), self.assertRaisesRegex(RuntimeError, "APP_ENV"):
            Settings().validate()

    def test_production_requires_explicit_database(self):
        env = self.production_environment()
        env.pop("DATABASE_URL")
        with patch.dict(os.environ, env, clear=True), self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
            Settings().validate()

    def test_production_rejects_weak_secret(self):
        env = self.production_environment()
        env["APP_SECRET_KEY"] = "short"
        with patch.dict(os.environ, env, clear=True), self.assertRaisesRegex(RuntimeError, "high-entropy"):
            Settings().validate()

    def test_production_accepts_explicit_absolute_sqlite_path(self):
        env = self.production_environment()
        env["DATABASE_URL"] = "sqlite:////var/data/spotipy.sqlite3"
        with patch.dict(os.environ, env, clear=True):
            Settings().validate()

    def test_production_rejects_relative_sqlite_path(self):
        env = self.production_environment()
        env["DATABASE_URL"] = "sqlite:///local_dev.db"
        with patch.dict(os.environ, env, clear=True), self.assertRaisesRegex(RuntimeError, "persistent path"):
            Settings().validate()

    def test_production_requires_https_urls(self):
        env = self.production_environment()
        env["FRONTEND_URL"] = "http://music.example.test"
        with patch.dict(os.environ, env, clear=True), self.assertRaisesRegex(RuntimeError, "HTTPS"):
            Settings().validate()


if __name__ == "__main__":
    unittest.main()
