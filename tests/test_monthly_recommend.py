"""Offline tests for monthly_recommend (no network, credentials, or OAuth)."""
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "monthly_recommend" / "monthly_recommend.py"
_SPEC = importlib.util.spec_from_file_location("monthly_recommend", _PATH)
mr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mr)

NOW = datetime(2026, 11, 4)
TEST_OWNER_ID = "test-owner"
TEST_OWNER_ID = "test-owner"


class ApiError(Exception):
    def __init__(self, status, msg="boom", headers=None):
        super().__init__(msg)
        self.http_status, self.msg, self.headers = status, msg, headers or {}


def run_write(sp, **kwargs):
    return mr.run(sp, "", write=True, expected_user_id=TEST_OWNER_ID,
                  stranger_client=sp, **kwargs)


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache_patch = mock.patch.object(
            mr, "PLAYLIST_CACHE_PATH", Path(self.temp_dir.name) / "playlist-cache.json")
        self.cache_patch.start()

    def tearDown(self):
        self.cache_patch.stop()
        self.temp_dir.cleanup()


class LoggingTests(unittest.TestCase):
    def test_json_logging_never_opens_source_local_log_file(self):
        prior_handlers = list(mr.log.handlers)
        for handler in prior_handlers:
            mr.log.removeHandler(handler)
        try:
            with mock.patch.object(mr, "RotatingFileHandler",
                                   side_effect=PermissionError("source tree is read-only")) as file_handler:
                mr._setup_logging(json_mode=True)
                file_handler.assert_not_called()
                self.assertEqual(len(mr.log.handlers), 1)
                self.assertIsInstance(mr.log.handlers[0], mr.logging.StreamHandler)
        finally:
            for handler in list(mr.log.handlers):
                mr.log.removeHandler(handler)
                handler.close()
            for handler in prior_handlers:
                mr.log.addHandler(handler)


def trk(i, artist="a", name=None):
    return {"id": f"t{i}", "uri": f"spotify:track:t{i}", "name": name or f"Song {i}",
            "artists": [{"id": f"ar_{artist}", "name": artist}]}


class FakeSp:
    def __init__(self, top=None, recent=None, saved=None, playlists=None,
                 albums=None, album_tracks=None, search_map=None, playlist_search=None,
                 playlist_contents=None, fail_put=False, fail_post_at=None,
                 public_ids=None, fail_privacy_demotion=False, fail_privacy_verification=False,
                 hide_from_public_listing=False, recent_error=None):
        self.top = top if top is not None else [trk(1, "seed")]
        self.recent = recent or []
        self.saved = saved or []
        self.playlists = playlists or []
        self.albums = albums or {}
        self.album_tr = album_tracks or {}
        self.search_map = search_map or {}
        self.playlist_search = playlist_search or []
        self.playlist_contents = playlist_contents or {}
        self.fail_put, self.fail_post_at = fail_put, fail_post_at
        self.public_ids = set(public_ids or [])
        self.fail_privacy_demotion = fail_privacy_demotion
        self.fail_privacy_verification = fail_privacy_verification
        self.hide_from_public_listing = hide_from_public_listing
        self.recent_error = recent_error
        self.recent_calls = 0
        self.privacy_updates = []
        self.request_order = []
        self.searches, self.created, self.puts, self.posts = [], [], [], []

    def next(self, page):
        return None

    def me(self):
        return {"id": TEST_OWNER_ID}

    def current_user_top_tracks(self, limit, time_range):
        return {"items": self.top}

    def current_user_recently_played(self, limit):
        self.recent_calls += 1
        if self.recent_error:
            raise self.recent_error
        return {"items": [{"track": t} for t in self.recent]}

    def current_user_saved_tracks(self, limit):
        return {"items": [{"track": t} for t in self.saved]}

    def current_user_playlists(self, limit):
        return {"items": self.playlists}

    def user_playlists(self, user, limit):
        if self.fail_privacy_verification:
            raise ApiError(503, "verification unavailable")
        visible = set() if self.hide_from_public_listing else self.public_ids
        return {"items": [{"id": pid} for pid in sorted(visible)]}

    def artist_albums(self, aid, album_type, limit):
        return {"items": self.albums.get(aid, [])}

    def album_tracks(self, alid, limit):
        return {"items": self.album_tr.get(alid, [])}

    def search(self, q, type, limit):
        if type == "track" and limit > 10:
            raise AssertionError("Development Mode Search limit exceeded")
        self.searches.append((type, q))
        if type == "playlist":
            return {"playlists": {"items": self.playlist_search}}
        return {"tracks": {"items": self.search_map.get(q, [])}}

    def _post(self, path, payload=None):
        if path == "me/playlists":
            self.created.append(payload)
            return {"id": "newpl"}
        self.posts.append((path, payload))
        if self.fail_post_at is not None and len(self.posts) > self.fail_post_at:
            raise ApiError(403, "forbidden")
        return {"snapshot_id": "s"}

    def _put(self, path, payload=None):
        self.request_order.append(path)
        if path.startswith("playlists/") and not path.endswith("/items"):
            self.privacy_updates.append((path, payload))
            if not self.fail_privacy_demotion:
                self.public_ids.discard(path.split("/")[1])
            return None
        if self.fail_put:
            raise ApiError(403, "forbidden")
        self.puts.append((path, payload))
        return {"snapshot_id": "s"}

    def _get(self, path, **kw):
        playlist_id = path.split("/")[1] if path.startswith("playlists/") else ""
        if path.startswith("playlists/") and not path.endswith("/items"):
            return {"owner": {"id": TEST_OWNER_ID},
                    "public": playlist_id in self.public_ids,
                    "collaborative": False}
        return {"items": self.playlist_contents.get(playlist_id, [])}


def catalog_sp(**kw):
    sp = FakeSp(**kw)
    sp.albums = {"ar_seed": [{"id": "al1"}]}
    sp.album_tr = {"al1": [trk(i, f"x{i // 2}") for i in range(10, 20)]}
    return sp


def cand(i, artist="a", score=1.0):
    t = trk(i, artist)
    return {"id": t["id"], "uri": t["uri"], "name": t["name"], "artist_id": f"ar_{artist}",
            "artist_name": artist, "artist_ids": {f"ar_{artist}"}, "score": score}


class SelectionTests(OfflineTestCase):
    def test_dedupe_exclusions_cap_and_determinism(self):
        cands = [cand(1, "a", 5), cand(1, "a", 5), cand(2, "a", 4), cand(3, "a", 3),
                 cand(4, "b", 2), cand(5, "c", 9), cand(6, "seed", 9)]
        args = (cands, {"t5"}, {"ar_seed"}, "2026-11")
        result = mr.select_tracks(*args)
        ids = [p["id"] for p in result["picks"]]
        self.assertEqual(ids, ["t1", "t2", "t4"])
        self.assertEqual(result["stats"]["duplicate"], 1)
        self.assertEqual(result["stats"]["excluded"], 1)
        self.assertEqual(result["stats"]["seed_artist"], 1)
        self.assertEqual([p["id"] for p in mr.select_tracks(*args)["picks"]], ids)

    def test_target_and_month_tiebreak(self):
        cands = [cand(i, f"artist{i}") for i in range(100)]
        a = mr.select_tracks(cands, set(), month="2026-11")["picks"]
        self.assertEqual(len(a), mr.TARGET_TRACK_COUNT)
        self.assertEqual(a, mr.select_tracks(list(reversed(cands)), set(), month="2026-11")["picks"])
        self.assertNotEqual(a, mr.select_tracks(cands, set(), month="2026-12")["picks"])

    def test_active_lanes_receive_proportional_slots_with_minimum_representation(self):
        cands = [cand(100 + i, f"a{i}", 100 - i) | {"lane": "large", "lane_weight": 100}
                 for i in range(8)]
        cands += [cand(200, "b", 1) | {"lane": "small-b", "lane_weight": 1},
                  cand(201, "c", 1) | {"lane": "small-c", "lane_weight": 1}]
        result = mr.select_tracks(cands, set(), month="2026-11", target=4)
        self.assertEqual(len(result["picks"]), 4)
        self.assertEqual({p["lane"] for p in result["picks"]}, {"large", "small-b", "small-c"})


class ProviderTests(OfflineTestCase):
    def test_recently_played_403_falls_back_to_disclosed_top_only_seeds(self):
        sp = FakeSp(recent_error=ApiError(403, "insufficient scope"))
        seeds = mr.gather_seeds(sp)
        self.assertEqual(seeds["seed_source"], "top_only_recent_unavailable")
        self.assertEqual(sp.recent_calls, 1)
        self.assertEqual(len(seeds["tracks"]), 1)

    def test_missing_recent_scope_skips_endpoint_and_uses_top_only_seeds(self):
        sp = FakeSp()
        sp._monthly_recent_scope_available = False
        seeds = mr.gather_seeds(sp)
        self.assertEqual(seeds["seed_source"], "top_only_recent_unavailable")
        self.assertEqual(sp.recent_calls, 0)

    def test_month_override_drives_playlist_name_and_invalid_month_fails(self):
        result = mr.run(catalog_sp(), "", write=False, now=NOW, month_override="2026-10")
        self.assertEqual(result["playlist"], "Monthly Recs 2026-10")
        with self.assertRaises(ValueError):
            mr.run(catalog_sp(), "", write=False, now=NOW, month_override="2026-13")

    def test_playlist_cache_expires_and_stores_only_required_metadata(self):
        track = trk(90, "cache", "Cached")
        track.update({"popularity": 33, "album": {"id": "unused"}, "external_urls": {"spotify": "unused"}})
        cached_items = mr._cacheable_items([{"track": track}])
        self.assertNotIn("album", cached_items[0]["track"])
        self.assertNotIn("external_urls", cached_items[0]["track"])
        mr._save_playlist_cache({"pl": {"fetched_at": 1000, "items": cached_items}}, now_ts=1000)
        self.assertIn("pl", mr._load_playlist_cache(now_ts=1001))
        self.assertNotIn("pl", mr._load_playlist_cache(now_ts=1000 + mr.PLAYLIST_CACHE_TTL + 1))
    def test_hivemind_playlist_cooccurrence_uses_lane_and_track_provenance(self):
        recommendation = trk(20, "new", "New Song")
        recommendation["popularity"] = 42
        seed = trk(1, "seed", "Seed Song")
        seed["popularity"] = 50
        item_seed = {"track": seed}
        item_recommendation = {"track": recommendation}
        playlists = [{"id": f"pl{i}", "name": f"Seed Mix {i}", "tracks": {"total": 20}}
                     for i in (1, 2)]
        sp = FakeSp(top=[seed], playlist_search=playlists,
                    playlist_contents={"pl1": [item_seed, item_recommendation],
                                       "pl2": [item_seed, item_recommendation]})
        seeds = mr.gather_seeds(sp)
        result = mr.provider_hivemind(sp, seeds, "2026-11")
        self.assertTrue(result["available"])
        self.assertEqual(len(result["candidates"]), 1)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["id"], "t20")
        self.assertEqual(candidate["playlist_count"], 2)
        self.assertTrue(candidate["lane"])
        self.assertEqual(len(candidate["sources"]), 2)

    def test_hivemind_filters_seed_credit_and_popular_tracks(self):
        seed = trk(1, "seed", "Seed Song")
        too_popular = trk(2, "new", "Famous")
        too_popular["popularity"] = 90
        feature = trk(3, "new", "Feature")
        feature["artists"].append({"id": "ar_seed", "name": "seed"})
        hidden_title = trk(4, "new", "Seed Song Remix")
        pl = {"id": "pl1", "name": "Mix", "tracks": {"total": 20}}
        rows = [{"track": t} for t in (seed, too_popular, feature, hidden_title)]
        sp = FakeSp(top=[seed], playlist_search=[pl, {**pl, "id": "pl2"}],
                    playlist_contents={"pl1": rows, "pl2": rows})
        result = mr.provider_hivemind(sp, mr.gather_seeds(sp), "2026-11")
        self.assertEqual(result["candidates"], [])

    def test_catalog_fallback_excludes_known_and_dry_run_writes_nothing(self):
        sp = catalog_sp(saved=[trk(10, "x5")], recent=[trk(11, "x5")])
        result = mr.run(sp, "", write=False, now=NOW)
        ids = {p["id"] for p in result["picks"]}
        self.assertEqual(result["provider"], "catalog")
        self.assertFalse(result["written"])
        self.assertNotIn("t10", ids)
        self.assertNotIn("t11", ids)
        self.assertIn("t12", ids)
        self.assertIn("DRY RUN", result["message"])
        self.assertFalse(sp.created or sp.puts or sp.posts)

    def test_lastfm_resolution_and_https(self):
        seen_urls = []

        def fetch(url):
            seen_urls.append(url)
            return {"similartracks": {"track": [
                {"name": "Found", "artist": {"name": "New"}, "match": "0.9"},
                {"name": "Low", "artist": {"name": "New"}, "match": "0.01"}]}}

        found = {"id": "n1", "uri": "spotify:track:n1", "name": "Found",
                 "artists": [{"id": "ar_new", "name": "New"}]}
        sp = FakeSp(search_map={'track:"Found" artist:"New"': [found]})
        with mock.patch.object(mr, "fetch_json", fetch):
            result = mr.run(sp, "KEY", write=False, now=NOW)
        self.assertEqual(result["provider"], "lastfm")
        self.assertEqual([p["id"] for p in result["picks"]], ["n1"])
        self.assertEqual(sum(kind == "track" for kind, _ in sp.searches), 1)
        self.assertTrue(seen_urls[0].startswith("https://"))

    def test_lastfm_total_failure_is_not_silent(self):
        with mock.patch.object(mr, "fetch_json", side_effect=OSError("down")):
            result = mr.run(FakeSp(), "KEY", write=False, now=NOW)
        self.assertEqual(result["provider"], "catalog")
        self.assertIn("Last.fm fallback failed", result["summary"])

    def test_no_seeds_and_zero_candidates_are_actionable_without_write(self):
        no_seeds_sp = FakeSp(top=[])
        result = run_write(no_seeds_sp, now=NOW)
        self.assertEqual(result["code"], 2)
        self.assertIn("No discovery provider", result["message"])
        self.assertFalse(result["written"])

        sp = FakeSp()
        result = run_write(sp, now=NOW)
        self.assertFalse(result["picks"])
        self.assertFalse(result["written"])
        self.assertIn("Nothing written", result["message"])
        self.assertFalse(sp.created or sp.puts)

    def test_unexpected_account_fails_before_playlist_mutation(self):
        sp = catalog_sp()
        sp.me = lambda: {"id": "different-account"}
        with self.assertRaises(mr.RecommendError):
            mr.run(sp, "", write=True, now=NOW, expected_user_id=TEST_OWNER_ID, stranger_client=sp)
        self.assertFalse(sp.created or sp.puts or sp.posts)

    def test_write_requires_caller_supplied_expected_user_id(self):
        sp = catalog_sp()
        with self.assertRaises(mr.RecommendError):
            mr.run(sp, "", write=True, now=NOW, stranger_client=sp)
        self.assertFalse(sp.created or sp.puts or sp.posts)


class PlaylistTests(OfflineTestCase):
    def test_write_creates_private_playlist_then_reuses_owned_monthly_playlist(self):
        sp = catalog_sp()
        result = run_write(sp, now=NOW)
        self.assertTrue(result["written"])
        self.assertEqual(result["outcome"], "written")
        self.assertEqual(result["playlist_id"], "newpl")
        self.assertEqual(result["playlist"], "Monthly Recs 2026-11")
        self.assertEqual(sp.created, [{"name": "Monthly Recs 2026-11", "public": False,
                                       "description": mr.PLAYLIST_DESCRIPTION}])
        self.assertEqual(sp.puts[0][0], "playlists/newpl/items")
        self.assertEqual(sp.request_order, ["playlists/newpl", "playlists/newpl/items"])

        sp2 = catalog_sp(playlists=[{"id": "pl9", "name": "Monthly Recs 2026-11",
                                    "owner": {"id": TEST_OWNER_ID},
                                    "description": mr.PLAYLIST_DESCRIPTION}],
                         public_ids={"pl9"})
        run_write(sp2, now=NOW)
        self.assertFalse(sp2.created)
        self.assertEqual(sp2.puts[0][0], "playlists/pl9/items")
        self.assertEqual(sp2.privacy_updates, [("playlists/pl9", {"public": False})])
        self.assertNotIn("pl9", sp2.public_ids)

    def test_never_reuses_playlist_with_unknown_or_other_owner(self):
        for owner in ({"id": "someone-else"}, {}):
            with self.subTest(owner=owner):
                sp = FakeSp(playlists=[{"id": "unsafe", "name": "Monthly Recs 2026-11", "owner": owner,
                                       "description": mr.PLAYLIST_DESCRIPTION}])
                self.assertIsNone(mr.find_playlist(sp, TEST_OWNER_ID, "Monthly Recs 2026-11"))

        untagged = FakeSp(playlists=[{"id": "unsafe", "name": "Monthly Recs 2026-11",
                                      "owner": {"id": TEST_OWNER_ID}, "description": "unrelated"}])
        self.assertIsNone(mr.find_playlist(untagged, TEST_OWNER_ID, "Monthly Recs 2026-11"))

    def test_privacy_failure_blocks_playlist_content_write(self):
        existing = {"id": "old", "name": "Monthly Recs 2026-11", "owner": {"id": TEST_OWNER_ID},
                    "description": mr.PLAYLIST_DESCRIPTION}
        still_public = FakeSp(playlists=[existing], public_ids={"old"}, fail_privacy_demotion=True)
        with self.assertRaises(mr.RecommendError):
            mr.ensure_and_write(still_public, TEST_OWNER_ID, existing["name"], ["u1"],
                                stranger_client=still_public)
        self.assertFalse(still_public.puts or still_public.posts)

        unverifiable = FakeSp(playlists=[existing], fail_privacy_verification=True)
        with self.assertRaises(mr.RecommendError):
            mr.ensure_and_write(unverifiable, TEST_OWNER_ID, existing["name"], ["u1"],
                                stranger_client=unverifiable)
        self.assertFalse(unverifiable.puts or unverifiable.posts)

    def test_public_detail_blocks_write_even_if_public_profile_list_hides_playlist(self):
        existing = {"id": "old", "name": "Monthly Recs 2026-11", "owner": {"id": TEST_OWNER_ID},
                    "description": mr.PLAYLIST_DESCRIPTION}
        sp = FakeSp(playlists=[existing], public_ids={"old"}, fail_privacy_demotion=True,
                    hide_from_public_listing=True)
        with self.assertRaisesRegex(mr.RecommendError, "did not confirm.*private"):
            mr.ensure_and_write(sp, TEST_OWNER_ID, existing["name"], ["u1"],
                                stranger_client=sp)
        self.assertFalse(sp.puts or sp.posts)

    def test_batches_writes_and_propagates_append_failure(self):
        sp = FakeSp()
        mr.write_playlist(sp, "p", [f"spotify:track:{i}" for i in range(250)])
        self.assertEqual(len(sp.puts[0][1]["uris"]), 100)
        self.assertEqual([len(p[1]["uris"]) for p in sp.posts], [100, 50])

        failing = FakeSp(fail_post_at=0)
        with self.assertRaises(ApiError):
            mr.write_playlist(failing, "p", [f"u{i}" for i in range(150)])

    def test_replace_failure_or_unconfirmed_response_raises(self):
        sp = catalog_sp(fail_put=True)
        with self.assertRaises(ApiError):
            run_write(sp, now=NOW)
        sp = FakeSp()
        sp._put = lambda path, payload=None: {}
        with self.assertRaises(mr.RecommendError):
            mr.write_playlist(sp, "p", ["u1"])


class CliTests(OfflineTestCase):
    def test_retries_429_bounded_and_surfaces_other_errors(self):
        n = {"count": 0}

        def always_429():
            n["count"] += 1
            raise ApiError(429, headers={"Retry-After": "1"})

        with mock.patch.object(mr.time, "sleep"):
            with self.assertRaises(ApiError):
                mr.sp_call(always_429)
        self.assertEqual(n["count"], mr.MAX_RETRIES + 1)
        with self.assertRaises(ApiError) as caught:
            mr.sp_call(lambda: (_ for _ in ()).throw(ApiError(403, "no")))
        self.assertIn("403", mr.describe_error(caught.exception))

    def test_error_details_strip_urls_and_credential_parameters(self):
        detail = mr.describe_error(Exception(
            "request https://example.test/path?api_key=do-not-show&other=x client_secret=also-secret"))
        self.assertNotIn("do-not-show", detail)
        self.assertNotIn("also-secret", detail)
        self.assertNotIn("?api_key", detail)

    def test_get_client_reuses_existing_oauth_cache_and_requested_scopes(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "spotipy.cache"
            cache.write_bytes(b"existing-auth-cache")
            seen = {}

            class FakeSpotify:
                def __init__(self, auth_manager, requests_timeout):
                    self.auth_manager = auth_manager
                    self.requests_timeout = requests_timeout
                    seen["spotify"] = self

            class FakeCacheFileHandler:
                def __init__(self, cache_path):
                    seen["cache_path"] = cache_path

                def get_cached_token(self):
                    return {"scope": " ".join(mr.REQUIRED_SCOPES)}

            class FakeOAuth:
                def __init__(self, **kwargs):
                    seen["oauth_kwargs"] = kwargs

            spotipy_module = types.ModuleType("spotipy")
            spotipy_module.__path__ = []
            spotipy_module.Spotify = FakeSpotify
            oauth_module = types.ModuleType("spotipy.oauth2")
            oauth_module.CacheFileHandler = FakeCacheFileHandler
            oauth_module.SpotifyOAuth = FakeOAuth

            with mock.patch.object(mr, "CACHE_PATH", cache), mock.patch.dict(
                sys.modules, {"spotipy": spotipy_module, "spotipy.oauth2": oauth_module}
            ):
                client = mr.get_client()
            self.assertEqual(cache.read_bytes(), b"existing-auth-cache")
            self.assertEqual(seen["cache_path"], str(cache))
            self.assertEqual(seen["oauth_kwargs"]["scope"], " ".join(mr.REQUIRED_SCOPES))
            self.assertFalse(seen["oauth_kwargs"]["open_browser"])
            self.assertEqual(client.requests_timeout, mr.OAUTH_TIMEOUT_SECONDS)
            self.assertFalse(client._monthly_recent_scope_available)
            with self.assertRaisesRegex(mr.RecommendError, "will not prompt"):
                client.auth_manager.get_auth_response()

    def test_get_client_fails_fast_without_cache_or_required_scope(self):
        class FakeCacheFileHandler:
            token = None

            def __init__(self, cache_path):
                pass

            def get_cached_token(self):
                return self.token

        class FakeOAuth:
            def __init__(self, **kwargs):
                raise AssertionError("OAuth manager must not be built after failed cache validation")

        spotipy_module = types.ModuleType("spotipy")
        spotipy_module.__path__ = []
        spotipy_module.Spotify = lambda **kw: None
        oauth_module = types.ModuleType("spotipy.oauth2")
        oauth_module.CacheFileHandler = FakeCacheFileHandler
        oauth_module.SpotifyOAuth = FakeOAuth
        with tempfile.TemporaryDirectory() as temp, \
             mock.patch.object(mr, "CACHE_PATH", Path(temp) / "spotipy.cache"), \
             mock.patch.dict(sys.modules, {"spotipy": spotipy_module, "spotipy.oauth2": oauth_module}):
            with self.assertRaisesRegex(mr.RecommendError, "No cached Spotify OAuth session"):
                mr.get_client()
            FakeCacheFileHandler.token = {"scope": "user-top-read"}
            with self.assertRaisesRegex(mr.RecommendError, "missing required scopes"):
                mr.get_client()

    def test_plain_cli_is_dry_run_and_write_is_explicit(self):
        calls = []
        fake_dotenv = type("Dotenv", (), {"load_dotenv": staticmethod(lambda **kw: None)})
        with mock.patch.object(mr, "_setup_logging"), \
             mock.patch.object(mr, "get_client", return_value=object()), \
             mock.patch.object(mr, "run", side_effect=lambda sp, key, write, **kw: calls.append((write, kw)) or
                               {"picks": [], "message": "ok", "code": 0}), \
             mock.patch.dict(sys.modules, {"dotenv": fake_dotenv}), \
             mock.patch.dict(os.environ, {mr.EXPECTED_USER_ENV: TEST_OWNER_ID}):
            self.assertEqual(mr.main([]), 0)
            self.assertEqual(mr.main(["--write"]), 0)
            self.assertEqual(mr.main(["--month", "2026-10", "--run-label", "test"]), 0)
        self.assertEqual([c[0] for c in calls], [False, True, False])
        self.assertEqual(calls[2][1]["month_override"], "2026-10")
        self.assertEqual(calls[1][1]["expected_user_id"], TEST_OWNER_ID)

    def test_json_cli_emits_only_sanitized_result_fields(self):
        fake_dotenv = type("Dotenv", (), {"load_dotenv": staticmethod(lambda **kw: None)})
        returned = {"outcome": "no_candidates", "code": 0, "written": False,
                    "playlist": "Monthly Recs 2026-11", "playlist_id": None,
                    "picks": [{"name": "private song title"}], "message": "No candidates."}
        with mock.patch.object(mr, "_setup_logging"), \
             mock.patch.object(mr, "get_client", return_value=object()), \
             mock.patch.object(mr, "run", return_value=returned), \
             mock.patch.dict(sys.modules, {"dotenv": fake_dotenv}), \
             mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("sys.stdout") as stdout:
                code = mr.main(["--json"])
                output = "".join(c.args[0] for c in stdout.write.call_args_list if c.args)
        self.assertEqual(code, 0)
        import json
        payload = json.loads(output.strip())
        self.assertEqual(payload["outcome"], "no_candidates")
        self.assertNotIn("picks", payload)
        self.assertNotIn("private song title", output)

    def test_json_write_missing_owner_fails_before_client_auth(self):
        fake_dotenv = type("Dotenv", (), {"load_dotenv": staticmethod(lambda **kw: None)})
        with mock.patch.object(mr, "_setup_logging"), \
             mock.patch.object(mr, "get_client", side_effect=AssertionError("must not authenticate")), \
             mock.patch.dict(sys.modules, {"dotenv": fake_dotenv}), \
             mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("sys.stdout") as stdout:
            code = mr.main(["--write", "--json"])
            output = "".join(c.args[0] for c in stdout.write.call_args_list if c.args)
        import json
        payload = json.loads(output.strip())
        self.assertEqual(code, 1)
        self.assertEqual(payload["outcome"], "error")
        self.assertFalse(payload["written"])
        self.assertIn(mr.EXPECTED_USER_ENV, payload["message"])


if __name__ == "__main__":
    unittest.main()
