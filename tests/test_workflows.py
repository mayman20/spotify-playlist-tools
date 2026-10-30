"""Offline behavior checks; no SDK import, authorization or real account calls.

Legacy modules initialize OAuth/logging at import time. Load only their function
definitions, with fake clients, so running these checks cannot trigger that setup.
"""
import ast
import contextlib
import io
import logging
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


class APIError(Exception):
    def __init__(self, status):
        self.http_status = status
        self.headers = {}


def functions(relative):
    tree = ast.parse((ROOT / relative).read_text())
    defs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    selected = ast.Module(body=[ast.ImportFrom(module="__future__", names=[
        ast.alias(name="annotations")], level=0), *defs], type_ignores=[])
    ns = {"SpotifyException": APIError, "time": SimpleNamespace(sleep=Mock()),
          "logging": Mock(), "log": Mock(), "defaultdict": defaultdict}
    exec(compile(ast.fix_missing_locations(selected), str(relative), "exec"), ns)
    ns.update(backoff=lambda fn, *a, **k: fn(*a, **k),
              sp_call=lambda fn, *a, **k: fn(*a, **k), render_progress=Mock())
    # Recover the real retry function when a test needs it.
    selected_ns = dict(ns)
    exec(compile(ast.fix_missing_locations(selected), str(relative), "exec"), selected_ns)
    ns["real_backoff"] = selected_ns.get("backoff")
    return ns


class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.n = functions("scripts/liked_add/liked_add.py")

    def test_saved_tracks_paginate_sort_and_skip_missing(self):
        sp = Mock()
        sp.current_user_saved_tracks.side_effect = [
            {"total": 4, "items": [{"track": {"id": "old"}, "added_at": "2024-01"},
                                    {"track": None}], "next": "next"},
            {"items": [{"track": {"id": "new"}, "added_at": "2024-03"},
                       {"track": {"id": None}}], "next": None}]
        self.assertEqual(self.n["fetch_all_saved_tracks"](sp),
                         [("new", "2024-03"), ("old", "2024-01")])
        self.assertEqual([c.kwargs["offset"] for c in sp.current_user_saved_tracks.call_args_list], [0, 50])

    def test_mirror_batches_preserve_order(self):
        sp = Mock()
        tracks = [str(i) for i in range(205)]
        self.n["set_playlist_exact"](sp, "target", tracks)
        sp.playlist_replace_items.assert_called_once_with("target", tracks[:100])
        self.assertEqual([c.args[1] for c in sp.playlist_add_items.call_args_list],
                         [tracks[100:200], tracks[200:]])

    def test_clear_failure_is_not_silently_successful(self):
        sp = Mock()
        sp.playlist_replace_items.side_effect = APIError(500)
        sp.playlist_remove_all_occurrences_of_items.side_effect = APIError(403)
        self.n["fetch_playlist_track_ids"] = lambda *a: ["old"]
        with self.assertRaises(APIError):
            self.n["set_playlist_exact"](sp, "target", [])

    def test_rate_limit_retry_is_bounded(self):
        fn = Mock(side_effect=APIError(429))
        with self.assertRaises(APIError):
            self.n["real_backoff"](fn)
        self.assertEqual(fn.call_count, 6)
        self.assertEqual(self.n["time"].sleep.call_count, 5)

    def test_programming_error_is_not_retried(self):
        fn = Mock(side_effect=ValueError("bad input"))
        with self.assertRaises(ValueError):
            self.n["real_backoff"](fn)
        self.assertEqual(fn.call_count, 1)


class MonthlyTests(unittest.TestCase):
    def setUp(self):
        self.n = functions("scripts/monthly_recommend/monthly_recommend.py")
        self.n.update(POPULARITY_MIN=20, POPULARITY_MAX=65, TARGET_TRACK_COUNT=30, MAX_PER_ARTIST=2)

    def test_target_lookup_requires_owner_across_pages(self):
        sp = Mock()
        sp.current_user_playlists.return_value = {"items": [
            {"name": "Monthly", "id": "foreign", "owner": {"id": "other"}}], "next": "next"}
        sp.next.return_value = {"items": [
            {"name": "Monthly", "id": "mine", "owner": {"id": "me"}}], "next": None}
        self.assertEqual(self.n["find_or_create_playlist"](sp, "me", "Monthly"), "mine")
        sp.user_playlist_create.assert_not_called()

    def test_foreign_name_collision_creates_owned_private_target(self):
        sp = Mock()
        sp.current_user_playlists.return_value = {"items": [
            {"name": "Monthly", "id": "foreign", "owner": {"id": "other"}}], "next": None}
        sp.user_playlist_create.return_value = {"id": "new"}
        self.assertEqual(self.n["find_or_create_playlist"](sp, "me", "Monthly"), "new")
        self.assertEqual(sp.user_playlist_create.call_args.kwargs["public"], False)

    def test_selection_filters_library_popularity_and_artist_cap(self):
        def track(tid, artist="one", pop=45):
            return dict(id=tid, artist_id=artist, popularity=pop, artist_score=1,
                        uri="spotify:track:"+tid, name=tid, artist_name=artist)
        candidates = [track("known"), track("a"), track("b"), track("c"),
                      track("low", "two", 1), track("high", "two", 99), track("d", "two")]
        self.assertEqual(self.n["select_tracks"](candidates, {"known"}),
                         ["spotify:track:a", "spotify:track:b", "spotify:track:d"])

    def test_monthly_overwrite_batches_in_order(self):
        sp = Mock()
        tracks = [str(i) for i in range(204)]
        self.n["overwrite_playlist"](sp, "target", tracks)
        sp.playlist_replace_items.assert_called_once_with("target", tracks[:100])
        self.assertEqual([c.args[1] for c in sp.playlist_add_items.call_args_list],
                         [tracks[100:200], tracks[200:]])


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.n = functions("scripts/vaulted_add/vaulted_add.py")
        self.n["EXCLUDE_DESCRIPTION_FLAG"] = "-*"
        self.sp = Mock()
        self.sp.me.return_value = {"id": "me"}
        self.n["sp"] = self.sp
        self.n["ProgressBar"] = lambda **k: SimpleNamespace(update=lambda *a: None)

    def test_sync_reads_only_eligible_owned_sources_and_diffs_target(self):
        def pl(pid, owner="me", desc=""):
            return dict(id=pid, name="_vaulted" if pid == "target" else pid,
                        owner={"id": owner}, description=desc)
        self.n["get_user_playlists"] = lambda: [pl("target"), pl("owned"),
            pl("excluded", desc="keep out -*"), pl("foreign", owner="other")]
        reader = self.n["get_playlist_tracks"] = Mock(return_value=["keep", "add", "add"])
        self.n["get_liked_songs"] = lambda: ["liked", "keep"]
        self.n["get_existing_playlist_tracks"] = lambda *a: ["keep", "obsolete"]
        with contextlib.redirect_stdout(io.StringIO()):
            self.n["add_tracks_to_existing_playlist"]("_vaulted")
        reader.assert_called_once_with("owned")
        self.assertEqual(set(self.sp.playlist_add_items.call_args.args[1]), {"add", "liked"})
        self.sp.playlist_remove_all_occurrences_of_items.assert_called_once_with("target", ["obsolete"])

    def test_missing_owned_target_does_not_write(self):
        self.n["get_user_playlists"] = lambda: [{"id": "foreign", "name": "_vaulted",
                                                 "owner": {"id": "other"}}]
        with contextlib.redirect_stdout(io.StringIO()):
            self.n["add_tracks_to_existing_playlist"]("_vaulted")
        self.sp.playlist_add_items.assert_not_called()
        self.sp.playlist_remove_all_occurrences_of_items.assert_not_called()


if __name__ == "__main__":
    unittest.main()
