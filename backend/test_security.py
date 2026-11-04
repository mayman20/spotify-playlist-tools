import unittest
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import Settings
from backend.security import (
    build_session_callback_url,
    frontend_origins,
    is_allowed_return_url,
    is_session_key_current,
    make_session_token,
    new_session_key,
    read_session_token,
)


class OAuthUrlTests(unittest.TestCase):
    def test_production_only_allows_configured_frontend_origin(self):
        self.assertTrue(
            is_allowed_return_url(
                "https://music.example.test/spotify/#/dashboard",
                "https://music.example.test/spotify/",
                "production",
            )
        )
        self.assertFalse(
            is_allowed_return_url("https://attacker.example/", "https://music.example.test/", "production")
        )

    def test_loopback_exception_is_development_only(self):
        self.assertTrue(is_allowed_return_url("http://localhost:8080/", "https://music.example.test/", "development"))
        self.assertTrue(is_allowed_return_url("http://127.0.0.1:9999/", "https://music.example.test/", "development"))
        self.assertFalse(is_allowed_return_url("http://localhost:8080/", "https://music.example.test/", "production"))
        self.assertFalse(is_allowed_return_url("http://127.0.0.1:9999/", "https://music.example.test/", "production"))

    def test_return_urls_with_userinfo_are_rejected(self):
        self.assertFalse(
            is_allowed_return_url(
                "https://music.example.test@attacker.example/",
                "https://music.example.test/",
                "production",
            )
        )

    def test_production_cors_origins_do_not_include_loopback_or_wildcard(self):
        origins = frontend_origins("https://music.example.test/", "production")
        self.assertIn("https://music.example.test", origins)
        self.assertNotIn("*", origins)
        self.assertFalse(any("localhost" in origin or "127.0.0.1" in origin for origin in origins))

    def test_missing_frontend_has_no_wildcard_origin(self):
        self.assertEqual(frontend_origins("", "production"), [])

    def test_callback_delivers_session_in_fragment_not_query(self):
        target = build_session_callback_url(
            "https://music.example.test/?keep=yes#/old-route",
            "signed-session-value",
            "spotify-user",
        )
        self.assertEqual(target, "https://music.example.test/?keep=yes#?session_token=signed-session-value&spotify_user_id=spotify-user")
        self.assertNotIn("signed-session-value", target.split("#", 1)[0])


class SessionRevocationTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings()
        self.settings.app_secret_key = "test-secret-with-enough-characters-for-signing"

    def test_session_key_is_signed_into_the_token(self):
        key = new_session_key()
        token = make_session_token(self.settings, "spotify-user", key)
        self.assertEqual(
            read_session_token(self.settings, token),
            {"spotify_user_id": "spotify-user", "session_key": key},
        )

    def test_missing_session_key_invalidates_legacy_session_tokens(self):
        from itsdangerous import URLSafeTimedSerializer
        from backend.security import SESSION_SALT

        token = URLSafeTimedSerializer(self.settings.app_secret_key).dumps(
            {"spotify_user_id": "spotify-user"}, salt=SESSION_SALT
        )
        self.assertIsNone(read_session_token(self.settings, token))

    def test_session_digest_invalidates_previous_sessions(self):
        old_key = new_session_key()
        current_key = new_session_key()
        from backend.security import hash_session_key

        current_hash = hash_session_key(current_key)
        self.assertTrue(is_session_key_current(current_key, current_hash))
        self.assertFalse(is_session_key_current(old_key, current_hash))
        self.assertFalse(is_session_key_current(current_key, None))


if __name__ == "__main__":
    unittest.main()
