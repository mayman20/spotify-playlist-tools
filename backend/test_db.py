import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import delete_tokens, get_tokens, init_db, upsert_tokens
from backend.security import hash_session_key, is_session_key_current, new_session_key


class SessionKeyPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "tokens.sqlite3"
        self.settings = SimpleNamespace(
            database_url=f"sqlite:////{self.database_path.as_posix().lstrip('/')}",
            app_secret_key="test-secret-with-enough-characters-for-encryption",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_session_key_hash_is_persisted_rotated_and_deleted(self):
        init_db(self.settings)
        old_key = new_session_key()
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        upsert_tokens(
            self.settings,
            "user-1",
            "Listener",
            "access-token",
            "refresh-token",
            expires_at,
            hash_session_key(old_key),
        )

        row = get_tokens(self.settings, "user-1")
        self.assertTrue(is_session_key_current(old_key, row["session_key_hash"]))
        self.assertEqual(row["access_token"], "access-token")

        new_key = new_session_key()
        upsert_tokens(
            self.settings,
            "user-1",
            "Listener",
            "access-token-2",
            "refresh-token-2",
            expires_at,
            hash_session_key(new_key),
        )
        row = get_tokens(self.settings, "user-1")
        self.assertFalse(is_session_key_current(old_key, row["session_key_hash"]))
        self.assertTrue(is_session_key_current(new_key, row["session_key_hash"]))

        delete_tokens(self.settings, "user-1")
        self.assertIsNone(get_tokens(self.settings, "user-1"))

    def test_existing_token_table_gets_nullable_session_hash_column(self):
        with sqlite3.connect(self.database_path) as conn:
            conn.execute(
                """
                CREATE TABLE spotify_user_tokens (
                    spotify_user_id TEXT PRIMARY KEY,
                    display_name TEXT,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                "INSERT INTO spotify_user_tokens (spotify_user_id, display_name, access_token, refresh_token, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("legacy-user", "Listener", "legacy-access", "legacy-refresh", datetime.now(timezone.utc).isoformat()),
            )

        init_db(self.settings)
        row = get_tokens(self.settings, "legacy-user")
        self.assertIsNone(row["session_key_hash"])
        self.assertEqual((row["access_token"], row["refresh_token"]), ("legacy-access", "legacy-refresh"))
        self.assertFalse(is_session_key_current("old-key", row["session_key_hash"]))
        with sqlite3.connect(self.database_path) as conn:
            access_token, refresh_token = conn.execute(
                "SELECT access_token, refresh_token FROM spotify_user_tokens WHERE spotify_user_id = ?",
                ("legacy-user",),
            ).fetchone()
        self.assertTrue(access_token.startswith("enc::"))
        self.assertTrue(refresh_token.startswith("enc::"))


if __name__ == "__main__":
    unittest.main()
