import unittest
from unittest import mock
import sys
from pathlib import Path

from spotipy.exceptions import SpotifyException

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import tasks

MUTATIONS = (
    "user_playlist_create",
    "playlist_change_details",
    "playlist_add_items",
    "playlist_remove_all_occurrences_of_items",
    "playlist_replace_items",
)


class FakeSpotify:
    def __init__(self, playlists, playlist_tracks, liked):
        self._playlists = playlists
        self._tracks = playlist_tracks
        self._liked = liked
        self.calls = []

    def _rec(self, name, *a, **k):
        self.calls.append(name)

    def me(self):
        return {"id": "me"}

    def current_user_playlists(self, limit=50, offset=0):
        return {"items": list(self._playlists), "next": None}

    def current_user_saved_tracks(self, limit=50, offset=0):
        return {"items": [{"track": {"id": t}} for t in self._liked], "next": None}

    def playlist_tracks(self, playlist_id, fields=None, limit=100):
        return {"items": [{"track": {"id": t}} for t in self._tracks.get(playlist_id, [])], "next": None}

    def next(self, resp):
        return None

    def user_playlist_create(self, **kw):
        self._rec("user_playlist_create")
        return {"id": "new", "name": kw.get("name")}

    def playlist_change_details(self, *a, **k):
        self._rec("playlist_change_details")

    def playlist_add_items(self, *a, **k):
        self._rec("playlist_add_items")

    def playlist_remove_all_occurrences_of_items(self, *a, **k):
        self._rec("playlist_remove_all_occurrences_of_items")

    def playlist_replace_items(self, *a, **k):
        self._rec("playlist_replace_items")

    def mutations(self):
        return [c for c in self.calls if c in MUTATIONS]


def pl(pid, name, desc="", owner="me"):
    return {"id": pid, "name": name, "description": desc, "owner": {"id": owner}}


class DryRunTests(unittest.TestCase):
    def vaulted_client(self, with_target):
        playlists = [pl("a", "Mix")]
        tracks = {"a": ["t1", "t2"]}
        if with_target:
            playlists.append(pl("v", "_vaulted"))  # untagged -> would_tag
            tracks["v"] = ["t2", "old"]
        return FakeSpotify(playlists, tracks, ["t3"])

    def test_vaulted_preview_existing_no_mutation(self):
        sp = self.vaulted_client(True)
        r = tasks.run_vaulted_add(sp, dry_run=True)
        self.assertEqual(sp.mutations(), [])
        self.assertTrue(r["dry_run"])
        self.assertFalse(r["would_create"])
        self.assertTrue(r["would_tag"])
        self.assertEqual((r["would_add"], r["would_remove"]), (2, 1))

    def test_vaulted_preview_missing_target_no_mutation(self):
        sp = self.vaulted_client(False)
        r = tasks.run_vaulted_add(sp, dry_run=True)
        self.assertEqual(sp.mutations(), [])
        self.assertTrue(r["would_create"])
        self.assertIsNone(r["playlist_id"])
        self.assertEqual(r["would_add"], 3)

    def test_vaulted_preview_ignores_unowned_id(self):
        sp = FakeSpotify([pl("x", "Other", owner="someone")], {}, [])
        r = tasks.run_vaulted_add(sp, playlist_id="x", dry_run=True)
        self.assertTrue(r["would_create"])
        self.assertEqual(sp.mutations(), [])

    def test_liked_preview_existing_no_mutation(self):
        liked = [f"t{i}" for i in range(250)]
        sp = FakeSpotify([pl("l", "Liked Songs Mirror")], {"l": ["z"]}, liked)
        r = tasks.run_liked_add(sp, dry_run=True)
        self.assertEqual(sp.mutations(), [])
        self.assertEqual((r["would_replace"], r["would_add"], r["current_tracks"]), (100, 150, 1))
        self.assertFalse(r["would_create"])

    def test_liked_preview_missing_target_no_mutation(self):
        sp = FakeSpotify([], {}, ["t1"])
        r = tasks.run_liked_add(sp, dry_run=True)
        self.assertEqual(sp.mutations(), [])
        self.assertTrue(r["would_create"])
        self.assertIsNone(r["playlist_id"])

    def test_liked_preview_empty_library_reports_cleared_tracks(self):
        sp = FakeSpotify([pl("l", "Liked Songs Mirror")], {"l": ["old-1", "old-2"]}, [])
        r = tasks.run_liked_add(sp, dry_run=True)
        self.assertEqual(sp.mutations(), [])
        self.assertEqual((r["total_tracks"], r["would_remove"], r["would_replace"]), (0, 2, 0))


class RealModeTests(unittest.TestCase):
    def test_vaulted_real_mutates(self):
        sp = FakeSpotify([pl("a", "Mix")], {"a": ["t1"]}, [])
        tasks.run_vaulted_add(sp)
        self.assertIn("user_playlist_create", sp.calls)
        self.assertIn("playlist_add_items", sp.calls)

    def test_liked_real_mutates(self):
        sp = FakeSpotify([], {}, ["t1"])
        tasks.run_liked_add(sp)
        self.assertIn("user_playlist_create", sp.calls)
        self.assertIn("playlist_replace_items", sp.calls)


class FlakyCall:
    """Fake callable that raises queued exceptions, then returns a value."""

    def __init__(self, errors, result="ok"):
        self.errors = list(errors)
        self.result = result
        self.count = 0

    def __call__(self, *a, **k):
        self.count += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.result


def spotify_error(status, headers=None):
    return SpotifyException(status, -1, f"fake {status}", headers=headers)


class BackoffTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(tasks.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def slept(self):
        return [c.args[0] for c in self.sleep.call_args_list]

    def test_generic_success_after_retries(self):
        call = FlakyCall([RuntimeError("a"), RuntimeError("b")])
        self.assertEqual(tasks._backoff(call), "ok")
        self.assertEqual(call.count, 3)
        self.assertEqual(self.slept(), [1.0, 2.0])

    def test_generic_exhaustion_reraises_last_original(self):
        errors = [ValueError(f"e{i}") for i in range(10)]
        call = FlakyCall(errors)
        with self.assertRaises(ValueError) as ctx:
            tasks._backoff(call)
        self.assertIs(ctx.exception, errors[4])
        self.assertEqual(call.count, 5)
        self.assertEqual(self.slept(), [1.0, 2.0, 4.0, 8.0])

    def test_generic_delay_is_capped(self):
        with mock.patch.object(tasks, "BACKOFF_MAX_ATTEMPTS", 10):
            call = FlakyCall([RuntimeError()] * 9)
            tasks._backoff(call)
        self.assertEqual(max(self.slept()), tasks.BACKOFF_MAX_DELAY_SECONDS)

    def test_non_429_spotify_error_immediate(self):
        err = spotify_error(404)
        call = FlakyCall([err])
        with self.assertRaises(SpotifyException) as ctx:
            tasks._backoff(call)
        self.assertIs(ctx.exception, err)
        self.assertEqual(call.count, 1)
        self.sleep.assert_not_called()

    def test_429_honors_retry_after_then_succeeds(self):
        call = FlakyCall([spotify_error(429, {"Retry-After": "7"})])
        self.assertEqual(tasks._backoff(call), "ok")
        self.assertEqual(self.slept(), [7.0])

    def test_429_retry_after_header_case_insensitive(self):
        call = FlakyCall([spotify_error(429, {"retry-after": "5"})])
        tasks._backoff(call)
        self.assertEqual(self.slept(), [5.0])

    def test_429_retry_after_at_cap_is_honored(self):
        call = FlakyCall([spotify_error(429, {"Retry-After": "30"})])
        tasks._backoff(call)
        self.assertEqual(self.slept(), [30.0])

    def test_429_retry_after_over_cap_reraises_immediately(self):
        err = spotify_error(429, {"Retry-After": "31"})
        call = FlakyCall([err])
        with self.assertRaises(SpotifyException) as ctx:
            tasks._backoff(call)
        self.assertIs(ctx.exception, err)
        self.assertEqual(call.count, 1)
        self.sleep.assert_not_called()

    def test_429_missing_or_invalid_retry_after_uses_fallback(self):
        for headers in (None, {}, {"Retry-After": "soon"}, {"Retry-After": "-3"}, {"Retry-After": "nan"}):
            self.sleep.reset_mock()
            call = FlakyCall([spotify_error(429, headers)])
            self.assertEqual(tasks._backoff(call), "ok")
            self.assertEqual(self.slept(), [tasks.RETRY_AFTER_FALLBACK_SECONDS], headers)

    def test_429_repeated_is_bounded(self):
        errors = [spotify_error(429, {"Retry-After": "1"}) for _ in range(10)]
        call = FlakyCall(errors)
        with self.assertRaises(SpotifyException) as ctx:
            tasks._backoff(call)
        self.assertIs(ctx.exception, errors[4])
        self.assertEqual(call.count, 5)
        self.assertEqual(len(self.slept()), 4)
        self.assertTrue(all(s <= tasks.RETRY_AFTER_MAX_SECONDS for s in self.slept()))


if __name__ == "__main__":
    unittest.main()
