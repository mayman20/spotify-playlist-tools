#!/usr/bin/env python3
"""
monthly_recommend.py — monthly Spotify discovery playlist for Joe's existing Spotify app.

Usage:
  monthly_recommend.py            preview only (dry run) — nothing is written
  monthly_recommend.py --write    create/replace the private "Monthly Recs YYYY-MM" playlist
                                   (requires SPOTIFY_EXPECTED_USER_ID)
  monthly_recommend.py --json     emit a sanitized structured result for runners

Seeds: your top tracks (short term) and recently played.
Primary provider: Hive Mind style playlist co-occurrence, ranked with current
listening lanes, playlist density, repeated evidence, and a deep-cut ceiling.
Fallback providers: Last.fm similar tracks when configured, then Spotify artist
albums if the existing app cannot read playlist contents or finds no picks.

The existing app supports public playlist search/content. If that access changes,
the script falls back to supported catalog methods. It never uses Recommendations,
Audio Features, or artist top tracks. Playlist writes use /items routes, with a
legacy read fallback for playlist contents. A reduced-field Spotify playlist
cache expires after six hours. Env var names: SPOTIPY_CLIENT_ID,
SPOTIPY_CLIENT_SECRET, SPOTIPY_REDIRECT_URI, optional LASTFM_API_KEY, and
SPOTIFY_EXPECTED_USER_ID (required for CLI writes). Values are never printed.

Logs: monthly_recommend.log next to this file (512 KB rotating, 2 backups).
"""

import argparse
import hashlib
import itertools
import json
import logging
import math
import os
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

# ── paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
ENV_PATH     = PROJECT_ROOT / ".env"
CACHE_PATH   = PROJECT_ROOT / ".cache" / "spotipy_general.cache"
PLAYLIST_CACHE_PATH = PROJECT_ROOT / ".cache" / "monthly_playlist_ttl.json"
EXPECTED_USER_ENV = "SPOTIFY_EXPECTED_USER_ID"
LOG_PATH     = SCRIPT_DIR / "monthly_recommend.log"

# ── tunables ──────────────────────────────────────────────────────────────────

PLAYLIST_NAME_PREFIX = "Monthly Recs"
PLAYLIST_DESCRIPTION = "Auto-generated monthly picks. [spotipy:monthly_recommend]"
TARGET_TRACK_COUNT   = 30
MAX_PER_ARTIST       = 2
WRITE_CHUNK          = 100      # API max items per add/replace request
SEARCH_LIMIT         = 10       # Dev Mode Search max
MAX_SEARCHES         = 120      # hard bound on Spotify searches per run
TOP_SEED_TRACKS      = 25
RECENT_SEED_TRACKS   = 10
TOP_SEED_ARTISTS     = 15
LASTFM_TRACK_SIMILAR = 10
LASTFM_MIN_MATCH     = 0.05
ALBUMS_PER_ARTIST    = 5
MAX_RETRIES          = 5
MAX_RETRY_WAIT       = 60
VAULTED_TAG          = "[spotipy:vaulted_add]"
MAX_PLAYLISTS        = 90
PLAYLISTS_PER_QUERY  = 8
MIN_PLAYLIST_TRACKS  = 5
MAX_PLAYLIST_TRACKS  = 250
POPULARITY_CEILING   = 60
MIN_PLAYLIST_COUNT   = 2
PLAYLIST_CACHE_TTL   = 6 * 60 * 60  # temporary performance cache; never retain indefinitely
MAX_CACHED_PLAYLISTS = 200

# Taste lanes from the working Hive Mind prototype. Activity weights come from
# current Spotify top/recent data; local Downloads history is not required.
LANES = (
    {"name": "shoegaze", "artists": ("my bloody valentine", "title fight", "slowdive", "duster", "whirr", "nothing", "narrow head", "swirlies"),
     "queries": ("shoegaze deep cuts", "obscure shoegaze", "dream pop deep cuts")},
    {"name": "90s alt / grunge", "artists": ("alice in chains", "stone temple pilots", "nirvana", "incubus", "meat puppets", "mother love bone", "screaming trees"),
     "queries": ("90s alternative deep cuts", "underrated grunge b-sides", "seattle scene deep cuts")},
    {"name": "classic rock", "artists": ("mama's pride", "the lovin' spoonful", "allman brothers band", "little feat", "poco", "pure prairie league", "firefall"),
     "queries": ("70s deep cuts not the hits", "obscure southern rock", "forgotten 70s rock")},
    {"name": "jazz-funk / soul", "artists": ("roy ayers", "lonnie liston smith", "gary bartz", "ronnie laws", "zhane", "esthero", "stanley clarke"),
     "queries": ("rare groove jazz funk", "spiritual jazz deep cuts", "90s r&b deep cuts", "jazz funk obscure")},
    {"name": "rap / drill", "artists": ("chief keef", "454", "earl sweatshirt", "playboi carti", "fredo santana"),
     "queries": ("underground drill", "plugg rap deep cuts", "underground rap deep cuts")},
    {"name": "electronic", "artists": ("vegyn", "phantogram", "gary numan", "yves tumor", "four tet"),
     "queries": ("leftfield electronic deep cuts", "downtempo obscure", "experimental electronic deep cuts")},
)
# Required scopes are validated against the existing token cache before any
# API request. Recently-played access is optional and falls back to top tracks.
REQUIRED_SCOPES = (
    "user-top-read", "user-library-read", "playlist-read-private", "playlist-modify-private"
)
OPTIONAL_RECENT_SCOPE = "user-read-recently-played"
SCOPES = " ".join((*REQUIRED_SCOPES, OPTIONAL_RECENT_SCOPE))
OAUTH_TIMEOUT_SECONDS = 15

log = logging.getLogger("monthly_recommend")


def _setup_logging(*, json_mode: bool = False) -> None:
    if log.handlers:
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S")
    log.setLevel(logging.INFO)
    handlers: List[logging.Handler] = [logging.StreamHandler()]
    # Scheduled JSON invocations run from a read-only source tree. Their
    # wrapper owns durable status, so they must not try to create a source-local
    # log file before the structured result can be emitted.
    if not json_mode:
        handlers.append(RotatingFileHandler(LOG_PATH, maxBytes=512 * 1024, backupCount=2, encoding="utf-8"))
    for h in handlers:
        h.setFormatter(fmt)
        log.addHandler(h)


class RecommendError(RuntimeError):
    """Actionable failure; message is safe to show to the user."""

# ── helpers ───────────────────────────────────────────────────────────────────

def describe_error(e: Exception) -> str:
    """Return short error text without query strings or credential parameters."""
    status = getattr(e, "http_status", None)
    msg = getattr(e, "msg", None) or str(e)
    text = f"Spotify API error {status}: {msg}" if status else f"{type(e).__name__}: {msg}"
    text = re.sub(r"https?://[^\s'\"]+", lambda m: m.group(0).split("?", 1)[0], text)
    text = re.sub(
        r"(?i)\b(api_key|client_secret|access_token|refresh_token|authorization|token)(\s*[=:]\s*)[^&\s,;]+",
        r"\1\2[redacted]", text,
    )
    return text[:400]


def sp_call(fn: Callable, *args, **kwargs):
    """Call fn, retrying only HTTP 429 (bounded). Other errors propagate."""
    delay = 2
    for attempt in range(MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if getattr(e, "http_status", None) != 429 or attempt == MAX_RETRIES:
                raise
            try:
                retry_after = int((getattr(e, "headers", None) or {}).get("Retry-After", delay))
            except (TypeError, ValueError):
                retry_after = delay
            wait = min(max(retry_after, delay), MAX_RETRY_WAIT)
            log.warning("Rate-limited — sleeping %ds (retry %d/%d).", wait, attempt + 1, MAX_RETRIES)
            time.sleep(wait)
            delay *= 2


def chunked(lst: list, size: int) -> Iterable[list]:
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def paged(sp, first: Dict) -> Iterable[Dict]:
    """Yield items across pages of a standard paging object."""
    page = first
    while page:
        yield from page.get("items") or []
        page = sp_call(sp.next, page) if page.get("next") else None


def _meta(track: Dict, score: float) -> Optional[Dict]:
    tid = track.get("id")
    if not tid:
        return None
    arts = track.get("artists") or []
    primary = arts[0] if arts else {}
    return {
        "id": tid,
        "uri": track.get("uri") or f"spotify:track:{tid}",
        "name": track.get("name", ""),
        "artist_id": primary.get("id") or primary.get("name") or tid,
        "artist_name": primary.get("name", ""),
        "artist_ids": {a["id"] for a in arts if a.get("id")},
        "popularity": track.get("popularity"),
        "score": score,
    }

# ── auth ──────────────────────────────────────────────────────────────────────

def get_client():
    from spotipy import Spotify
    from spotipy.oauth2 import CacheFileHandler, SpotifyOAuth

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cache_handler = CacheFileHandler(cache_path=str(CACHE_PATH))
    cached = cache_handler.get_cached_token()
    if not cached:
        raise RecommendError(
            "No cached Spotify OAuth session. Complete interactive Spotify setup before running this job.")
    scopes = set(re.split(r"[\s,]+", cached.get("scope", "").strip())) - {""}
    missing = sorted(set(REQUIRED_SCOPES) - scopes)
    if missing:
        raise RecommendError(
            "Cached Spotify session is missing required scopes: " + ", ".join(missing)
            + ". Reauthorize interactively before running this job.")
    recent_available = OPTIONAL_RECENT_SCOPE in scopes
    requested_scopes = (*REQUIRED_SCOPES, *((OPTIONAL_RECENT_SCOPE,) if recent_available else ()))
    auth = SpotifyOAuth(
        scope=" ".join(requested_scopes),
        cache_handler=cache_handler,
        open_browser=False,
        requests_timeout=OAUTH_TIMEOUT_SECONDS,
    )

    # Prevent any future Spotipy error path from falling through to input() or
    # opening a browser in a scheduled/background invocation.
    def refuse_interactive_auth(*_args, **_kwargs):
        raise RecommendError(
            "Spotify authorization needs interactive setup; this runner will not prompt or open a browser.")

    auth.get_auth_response = refuse_interactive_auth
    client = Spotify(auth_manager=auth, requests_timeout=OAUTH_TIMEOUT_SECONDS)
    client._monthly_recent_scope_available = recent_available
    return client

# ── seeds & exclusions ────────────────────────────────────────────────────────

def gather_seeds(sp) -> Dict[str, Any]:
    """Top (short term) + recently played tracks. Returns tracks, artists, known ids."""
    top = sp_call(sp.current_user_top_tracks, limit=50, time_range="short_term").get("items") or []
    medium = sp_call(sp.current_user_top_tracks, limit=50, time_range="medium_term").get("items") or []
    recent_available = getattr(sp, "_monthly_recent_scope_available", True)
    recent_items = []
    if recent_available:
        try:
            recent_items = sp_call(sp.current_user_recently_played, limit=50).get("items") or []
        except Exception as exc:
            if getattr(exc, "http_status", None) != 403:
                raise
            recent_available = False
            log.warning("Recently-played access is unavailable; using top-track seeds only.")
    recent = [i.get("track") or {} for i in recent_items]

    seed_tracks: List[Dict] = []
    seen: Set[str] = set()
    for t in top[:TOP_SEED_TRACKS] + [r for r in recent if r.get("id")][:RECENT_SEED_TRACKS]:
        if t.get("id") and t["id"] not in seen:
            seen.add(t["id"])
            seed_tracks.append(t)

    freq: Dict[str, float] = defaultdict(float)
    names: Dict[str, str] = {}
    # Preserve individual track affinity. Short-term and recently played items
    # receive more weight than the broader medium-term baseline.
    for t in top:
        for a in t.get("artists") or []:
            if a.get("id"):
                freq[a["id"]] += 1.0
                names.setdefault(a["id"], a.get("name", ""))
    for t in medium:
        for a in t.get("artists") or []:
            if a.get("id"):
                freq[a["id"]] += 0.35
                names.setdefault(a["id"], a.get("name", ""))
    for t in recent:
        for a in t.get("artists") or []:
            if a.get("id"):
                freq[a["id"]] += 1.5
                names.setdefault(a["id"], a.get("name", ""))
    artists = [
        {"id": aid, "name": names[aid], "freq": n}
        for aid, n in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    known = {t["id"] for t in top + medium + recent if t.get("id")}
    return {"tracks": seed_tracks, "artists": artists,
            "artist_ids": {a["id"] for a in artists}, "known_ids": known,
            "artist_names": {a["id"]: names[a["id"]] for a in artists},
            "seed_source": "top_and_recent" if recent_available else "top_only_recent_unavailable"}


def gather_library_ids(sp, user_id: str) -> Set[str]:
    """Owned vaulted playlist (/items works for owned playlists) else liked songs."""
    for pl in paged(sp, sp_call(sp.current_user_playlists, limit=50)):
        if (pl.get("owner") or {}).get("id") != user_id:
            continue
        if VAULTED_TAG.lower() not in (pl.get("description") or "").lower():
            continue
        ids: Set[str] = set()
        page = sp_call(sp._get, f"playlists/{pl['id']}/items", limit=100)
        for it in paged(sp, page):
            tr = it.get("item") or it.get("track") or {}   # field renamed in newer API
            if tr.get("id"):
                ids.add(tr["id"])
        log.info("Library loaded from vaulted playlist: %d ids.", len(ids))
        return ids
    ids = set()
    for it in paged(sp, sp_call(sp.current_user_saved_tracks, limit=50)):
        if (it.get("track") or {}).get("id"):
            ids.add(it["track"]["id"])
    log.info("Library loaded from liked songs: %d ids.", len(ids))
    return ids

# ── providers ─────────────────────────────────────────────────────────────────

def fetch_json(url: str) -> Dict:
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 (https only)
        return json.loads(resp.read().decode())


def _canon(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _title_contains_seed(title: str, seed_names: Set[str]) -> bool:
    title_words = " " + re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip() + " "
    for name in seed_names:
        name_words = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
        if name_words and f" {name_words} " in title_words:
            return True
    return False


def _playlist_total(playlist: Dict) -> int:
    """Read either legacy tracks.total or renamed items.total metadata."""
    return int(((playlist.get("items") or {}).get("total")
                or (playlist.get("tracks") or {}).get("total") or 0))


def _playlist_search(sp, query: str) -> List[Dict]:
    """Use grandfathered 50-result search when available; retry the Dev cap."""
    try:
        res = sp_call(sp.search, q=query, type="playlist", limit=50)
    except Exception as exc:
        if getattr(exc, "http_status", None) != 400:
            raise
        log.info("Playlist search limit 50 unavailable; retrying with Development Mode limit 10.")
        res = sp_call(sp.search, q=query, type="playlist", limit=10)
    return ((res or {}).get("playlists") or {}).get("items") or []


def _playlist_items(sp, playlist_id: str) -> List[Dict]:
    """Read a playlist's first 100 items, preferring the current endpoint."""
    try:
        page = sp_call(sp._get, f"playlists/{playlist_id}/items", limit=100)
    except Exception as current_error:
        # The verified grandfathered app also supports the legacy route; keep it
        # as a compatibility path for older server-side behavior.
        if getattr(current_error, "http_status", None) not in (400, 404, 405):
            raise
        try:
            page = sp_call(sp.playlist_tracks, playlist_id, limit=100)
        except Exception:
            raise current_error
    return (page or {}).get("items") or []


def _load_playlist_cache(now_ts: Optional[float] = None) -> Dict[str, Dict]:
    """Load only fresh, reduced playlist metadata; discard expired entries."""
    now_ts = now_ts if now_ts is not None else time.time()
    try:
        data = json.loads(PLAYLIST_CACHE_PATH.read_text(encoding="utf-8"))
        raw = data.get("entries", {}) if data.get("version") == 1 else {}
    except FileNotFoundError:
        raw = {}
    except (OSError, ValueError, TypeError) as exc:
        log.warning("Playlist cache unreadable; refreshing from Spotify (%s).", type(exc).__name__)
        raw = {}
    fresh = {}
    for pid, entry in raw.items():
        fetched_at = entry.get("fetched_at", 0)
        if not isinstance(fetched_at, (int, float)) or not 0 <= now_ts - fetched_at <= PLAYLIST_CACHE_TTL:
            continue
        items = entry.get("items")
        if isinstance(items, list):
            fresh[pid] = {"fetched_at": fetched_at, "items": items}
    return dict(list(fresh.items())[-MAX_CACHED_PLAYLISTS:])


def _cacheable_items(items: List[Dict]) -> List[Dict]:
    """Retain only the fields the recommender needs, never full Spotify payloads."""
    out = []
    for item in items:
        track = (item or {}).get("item") or (item or {}).get("track") or {}
        if not track.get("id") or not track.get("uri") or not track.get("name"):
            continue
        artists = [{"id": a.get("id"), "name": a.get("name")} for a in track.get("artists", [])
                   if a.get("id") or a.get("name")]
        out.append({"track": {"id": track["id"], "uri": track["uri"], "name": track["name"],
                               "popularity": track.get("popularity"), "artists": artists}})
    return out


def _save_playlist_cache(entries: Dict[str, Dict], now_ts: Optional[float] = None) -> None:
    """Atomically write a bounded six-hour cache; cache I/O must not fail a run."""
    now_ts = now_ts if now_ts is not None else time.time()
    fresh = {
        pid: entry for pid, entry in entries.items()
        if 0 <= now_ts - entry.get("fetched_at", 0) <= PLAYLIST_CACHE_TTL
    }
    fresh = dict(list(fresh.items())[-MAX_CACHED_PLAYLISTS:])
    payload = json.dumps({"version": 1, "entries": fresh}, ensure_ascii=False, separators=(",", ":"))
    tmp_path = None
    try:
        PLAYLIST_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=PLAYLIST_CACHE_PATH.parent,
                                         prefix=".monthly-playlist-cache-", delete=False) as tmp:
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, PLAYLIST_CACHE_PATH)
    except OSError as exc:
        log.warning("Could not update temporary playlist cache (%s).", type(exc).__name__)
        if tmp_path:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _active_lanes(seeds: Dict) -> Dict[str, Dict]:
    """Assign currently active seed artists to saved listening-taste lanes."""
    names = seeds.get("artist_names") or {}
    weights = {artist["id"]: artist["freq"] for artist in seeds.get("artists", [])}
    lane_by_name = {
        _canon(name): lane["name"]
        for lane in LANES for name in lane["artists"]
    }
    active: Dict[str, Dict] = {}
    for aid, name in names.items():
        lane_name = lane_by_name.get(_canon(name), "Current mix")
        lane = active.setdefault(lane_name, {"artists": [], "mass": 0.0})
        lane["artists"].append({"id": aid, "name": name, "freq": weights.get(aid, 1.0)})
        lane["mass"] += weights.get(aid, 1.0)
    # Only add generic genre queries for lanes Joe is listening to now.
    for lane_name, lane in active.items():
        lane["artists"].sort(key=lambda x: (-x["freq"], _canon(x["name"])))
        lane["queries"] = next((x["queries"] for x in LANES if x["name"] == lane_name), ())
    return active


def _discovery_queries(seeds: Dict, lanes: Dict[str, Dict]) -> List[str]:
    """Keep queries track-aware while bounding calls per monthly run."""
    queries: List[str] = []
    # Pair artist names from the same active taste lane, as in Hive Mind.
    for lane in lanes.values():
        names = [x["name"] for x in lane["artists"][:6]]
        queries.extend(f'"{a}" "{b}"' for a, b in itertools.combinations(names, 2))
        queries.extend(lane.get("queries", ()))
    # Search actual tracks as well as artist names so we do not collapse taste
    # into artist counts. Most recent individual tracks have first claim.
    for track in seeds["tracks"][:12]:
        artists = track.get("artists") or [{}]
        artist = artists[0].get("name", "")
        title = track.get("name", "")
        if artist and title:
            queries.append(f'"{title}" "{artist}"')
    for artist in seeds.get("artists", [])[:12]:
        if artist.get("name"):
            queries.append(artist["name"])
    out, seen = [], set()
    for q in queries:
        key = _canon(q)
        if key and key not in seen:
            seen.add(key)
            out.append(q)
    return out[:36]


def _playlist_weight(n_tracks: int, n_seed_artists: int) -> float:
    multiplier = 1.0 if n_seed_artists == 1 else 3.0 if n_seed_artists == 2 else 8.0
    return multiplier / math.log2(4 + max(n_tracks, 0))


def provider_hivemind(sp, seeds: Dict, month: str) -> Dict[str, Any]:
    """Mine playlist co-occurrence with lane weighting and deep-cut filters."""
    lanes = _active_lanes(seeds)
    lane_artist_ids = {
        name: {a["id"] for a in lane["artists"]}
        for name, lane in lanes.items()
    }
    artist_name_by_id = seeds.get("artist_names") or {}
    seed_names = set(artist_name_by_id.values())
    seed_track_ids = seeds.get("known_ids", set())
    playlists: Dict[str, Dict] = {}
    playlist_query_hits: Dict[str, Set[str]] = defaultdict(set)
    searches = 0

    for query in _discovery_queries(seeds, lanes):
        try:
            results = _playlist_search(sp, query)
        except Exception as exc:
            # Feature/permission denial should let run() choose a viable
            # fallback. Other API failures remain visible to the caller.
            if getattr(exc, "http_status", None) in (403, 404):
                raise RecommendError("Spotify playlist search is unavailable to this app/account.") from exc
            raise
        searches += 1
        for pl in results:
            if not pl or not pl.get("id"):
                continue
            pid = pl["id"]
            total = _playlist_total(pl)
            if not MIN_PLAYLIST_TRACKS <= total <= MAX_PLAYLIST_TRACKS:
                continue
            playlists.setdefault(pid, pl)
            playlist_query_hits[pid].add(query)
        if len(playlists) >= MAX_PLAYLISTS:
            break

    score: Dict[str, float] = defaultdict(float)
    npl: Counter = Counter()
    meta: Dict[str, Dict] = {}
    provenance: Dict[str, List[str]] = defaultdict(list)
    lane_scores: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    reads = 0
    unavailable = 0
    playlist_cache = _load_playlist_cache()
    cache_hits = 0
    cache_time = time.time()

    for pid, pl in list(playlists.items())[:MAX_PLAYLISTS]:
        cached = playlist_cache.get(pid)
        if cached:
            items = cached["items"]
            cache_hits += 1
        else:
            try:
                items = _playlist_items(sp, pid)
                reads += 1
                playlist_cache[pid] = {"fetched_at": cache_time, "items": _cacheable_items(items)}
            except Exception as exc:
                if getattr(exc, "http_status", None) in (403, 404):
                    unavailable += 1
                    continue
                raise
        rows = []
        playlist_artists: Set[str] = set()
        for item in items:
            track = (item or {}).get("item") or (item or {}).get("track") or {}
            if not track.get("id") or not track.get("uri") or not track.get("name"):
                continue
            artists = track.get("artists") or []
            artist_ids = {a.get("id") for a in artists if a.get("id")}
            artist_names = {a.get("name", "") for a in artists if a.get("name")}
            playlist_artists.update(artist_ids)
            rows.append((track, artist_ids, artist_names))
        matched_artists = playlist_artists & seeds["artist_ids"]
        matched_tracks = {track["id"] for track, _, _ in rows} & seed_track_ids
        if not matched_artists and not matched_tracks:
            continue
        # Lane relevance uses the actual playlist contents, not search text.
        matched_lanes = [name for name, ids in lane_artist_ids.items() if ids & matched_artists]
        if not matched_lanes:
            matched_lanes = ["Current mix"]
        seed_count = max(len(matched_artists), len(matched_tracks))
        weight = _playlist_weight(len(rows) or _playlist_total(pl), seed_count)
        if matched_tracks:
            weight *= 1.35
        pname = pl.get("name") or pid
        for track, artist_ids, artist_names in rows:
            tid = track["id"]
            if tid in seed_track_ids:
                continue
            # Exclude every credited seed artist and track-title feature leak.
            credited_names = {_canon(name) for name in artist_names}
            seed_artist_names = {_canon(name) for name in seed_names}
            if artist_ids & seeds["artist_ids"] or credited_names & seed_artist_names:
                continue
            if _title_contains_seed(track.get("name", ""), seed_names):
                continue
            m = _meta(track, 0.0)
            if not m:
                continue
            uri = m["uri"]
            score[uri] += weight
            npl[uri] += 1
            if uri not in meta:
                meta[uri] = m
            if pname not in provenance[uri] and len(provenance[uri]) < 3:
                provenance[uri].append(pname)
            for lane_name in matched_lanes:
                lane_scores[uri][lane_name] += weight * max(
                    1.0, lanes.get(lane_name, {}).get("mass", 1.0)
                )

    _save_playlist_cache(playlist_cache, cache_time)
    if reads == 0 and cache_hits == 0 and unavailable:
        raise RecommendError("Spotify playlist contents are unavailable to this app/account.")

    candidates = []
    for uri, m in meta.items():
        pop = m.get("popularity")
        if npl[uri] < MIN_PLAYLIST_COUNT:
            continue
        # The grandfathered app supplies popularity. On a Dev Mode response
        # without it, retain the candidate and use occurrence evidence only.
        if pop is not None and pop > POPULARITY_CEILING:
            continue
        obscurity = 1.0 - (float(pop) / 100.0) * 0.8 if pop is not None else 1.0
        lane = max(lane_scores[uri], key=lane_scores[uri].get) if lane_scores[uri] else "Current mix"
        lane_weight = lanes.get(lane, {}).get("mass", 1.0) or 1.0
        candidates.append({
            **m,
            "score": (score[uri] / math.log(2 + npl[uri])) * obscurity,
            "lane": lane,
            "lane_weight": lane_weight,
            "sources": provenance[uri],
            "playlist_count": npl[uri],
        })
    return {
        "name": "hivemind", "candidates": candidates, "exclude_seed_artists": True,
        "detail": f"{searches} searches, {len(playlists)} playlists found, {reads} fetched, "
                  f"{cache_hits} cached, {unavailable} inaccessible",
        "available": reads > 0 or cache_hits > 0,
    }


def _lastfm_url(key: str, artist: str, track: str) -> str:
    q = urllib.parse.urlencode({
        "method": "track.getsimilar", "artist": artist, "track": track,
        "api_key": key, "limit": LASTFM_TRACK_SIMILAR, "format": "json",
    })
    return "https://ws.audioscrobbler.com/2.0/?" + q


def provider_lastfm(sp, seeds: Dict, key: str) -> Dict[str, Any]:
    """Last.fm similar tracks → Spotify Search (bounded). Key is never logged."""
    scores: Dict[str, float] = defaultdict(float)
    meta: Dict[str, Dict] = {}
    searches = lf_fail = lf_ok = 0
    tracks = seeds["tracks"]
    n = max(len(tracks), 1)
    for i, seed in enumerate(tracks):
        arts = seed.get("artists") or [{}]
        weight = 1.0 - (i / n) * 0.5
        try:
            data = fetch_json(_lastfm_url(key, arts[0].get("name", ""), seed.get("name", "")))
        except Exception as exc:  # network/HTTP; never include URL (contains key)
            lf_fail += 1
            log.warning("  [lastfm] request failed for seed '%s': %s", seed.get("name", "?")[:40], type(exc).__name__)
            continue
        lf_ok += 1
        for sim in (data.get("similartracks") or {}).get("track") or []:
            match = float(sim.get("match") or 0)
            name = (sim.get("name") or "").strip()
            artist = ((sim.get("artist") or {}).get("name") or "").strip()
            if not name or not artist or match < LASTFM_MIN_MATCH:
                continue
            if searches >= MAX_SEARCHES:
                break
            searches += 1
            res = sp_call(sp.search, q=f'track:"{name}" artist:"{artist}"', type="track", limit=SEARCH_LIMIT)
            for cand in (res.get("tracks") or {}).get("items") or []:
                ca = (cand.get("artists") or [{}])[0].get("name", "").strip().lower()
                if (cand.get("name") or "").strip().lower() == name.lower() and ca == artist.lower():
                    m = _meta(cand, 0.0)
                    if m:
                        scores[m["uri"]] += match * weight
                        meta.setdefault(m["uri"], m)
                    break
    if lf_ok == 0 and tracks:
        raise RecommendError("Last.fm: every request failed — check LASTFM_API_KEY and network access.")
    cands = [{**m, "score": scores[u]} for u, m in meta.items()]
    return {"name": "lastfm", "candidates": cands, "exclude_seed_artists": True,
            "detail": f"{lf_ok} seeds answered, {lf_fail} failed, {searches} searches"}


def provider_catalog(sp, seeds: Dict, month: str) -> Dict[str, Any]:
    """No-key fallback: tracks from recent albums/singles of your top artists."""
    scores: Dict[str, float] = defaultdict(float)
    meta: Dict[str, Dict] = {}
    albums_seen = 0
    for artist in seeds["artists"][:TOP_SEED_ARTISTS]:
        res = sp_call(sp.artist_albums, artist["id"], album_type="album,single", limit=ALBUMS_PER_ARTIST)
        for ai, album in enumerate((res or {}).get("items") or []):
            if not album.get("id"):
                continue
            albums_seen += 1
            tr = sp_call(sp.album_tracks, album["id"], limit=50)
            for t in (tr or {}).get("items") or []:
                m = _meta(t, 0.0)
                if m:
                    scores[m["uri"]] += artist["freq"] / (1 + ai)
                    meta.setdefault(m["uri"], m)
    cands = [{**m, "score": scores[u]} for u, m in meta.items()]
    return {"name": "catalog", "candidates": cands, "exclude_seed_artists": False,
            "detail": f"{albums_seen} albums scanned (deeper cuts by your top artists)"}

# ── selection ─────────────────────────────────────────────────────────────────

def select_tracks(
    candidates: List[Dict],
    exclude_ids: Set[str],
    seed_artist_ids: Set[str] = frozenset(),
    month: str = "",
    target: int = TARGET_TRACK_COUNT,
    per_artist: int = MAX_PER_ARTIST,
) -> Dict[str, Any]:
    """Deterministic: exact id/uri dedupe, exclusions, score then month-hash tiebreak."""
    stats = {"input": len(candidates), "duplicate": 0, "excluded": 0, "seed_artist": 0}
    seen_uris: Set[str] = set()
    pool: List[Dict] = []
    for c in candidates:
        if c["uri"] in seen_uris:
            stats["duplicate"] += 1
        elif c["id"] in exclude_ids:
            stats["excluded"] += 1
        elif c.get("artist_ids", set()) & seed_artist_ids:
            stats["seed_artist"] += 1
        else:
            seen_uris.add(c["uri"])
            pool.append(c)

    def tie(c: Dict) -> str:
        return hashlib.sha1(f"{month}|{c['uri']}".encode()).hexdigest()

    pool.sort(key=lambda c: (-c["score"], tie(c)))
    counts: Dict[str, int] = defaultdict(int)
    picks: List[Dict] = []
    lanes: Dict[str, List[Dict]] = defaultdict(list)
    for c in pool:
        lanes[c.get("lane") or "Current mix"].append(c)

    # Allocate slots in proportion to current listening mass so one broad lane
    # cannot consume the whole playlist. Refill unused slots by global score.
    lane_names = sorted(lanes)
    weights = {name: max(float(lanes[name][0].get("lane_weight", 1.0)), 0.01)
               for name in lane_names}
    total_weight = sum(weights.values()) or 1.0
    exact = {name: target * weights[name] / total_weight for name in lane_names}
    quotas = {name: min(len(lanes[name]), int(exact[name])) for name in lane_names}
    if target >= len(lane_names):
        for name in lane_names:
            if quotas[name] == 0 and lanes[name]:
                quotas[name] = 1
    while sum(quotas.values()) > target:
        donors = [name for name in lane_names if quotas[name] > 1]
        if not donors:
            break
        donor = max(donors, key=lambda n: (quotas[n] - exact[n], quotas[n], n))
        quotas[donor] -= 1
    while sum(quotas.values()) < target:
        available = [name for name in lane_names if quotas[name] < len(lanes[name])]
        if not available:
            break
        name = max(available, key=lambda n: (exact[n] - quotas[n], weights[n], n))
        quotas[name] += 1
    for name in lane_names:
        lane_picks = 0
        for c in lanes[name]:
            if len(picks) >= target:
                break
            if lane_picks >= quotas[name]:
                break
            if counts[c["artist_id"]] >= per_artist:
                continue
            counts[c["artist_id"]] += 1
            picks.append(c)
            lane_picks += 1

    selected = {c["uri"] for c in picks}
    for c in pool:
        if len(picks) >= target:
            break
        if c["uri"] in selected or counts[c["artist_id"]] >= per_artist:
            continue
        counts[c["artist_id"]] += 1
        picks.append(c)
        selected.add(c["uri"])
    stats["eligible"] = len(pool)
    return {"picks": picks, "stats": stats}

# ── playlist management ───────────────────────────────────────────────────────

def _validated_month(month: str) -> str:
    try:
        parsed = datetime.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise ValueError("month must be a valid YYYY-MM value") from exc
    if parsed.strftime("%Y-%m") != month:
        raise ValueError("month must be a valid YYYY-MM value")
    return month


def playlist_name(now: datetime, month: Optional[str] = None) -> str:
    label = _validated_month(month) if month else now.strftime("%Y-%m")
    return f"{PLAYLIST_NAME_PREFIX} {label}"


def find_playlist(sp, user_id: str, name: str) -> Optional[str]:
    for pl in paged(sp, sp_call(sp.current_user_playlists, limit=50)):
        # Only reuse the script's tagged, non-collaborative playlist owned by
        # the authenticated account. A matching name alone is unsafe.
        description = (pl.get("description") or "").lower()
        if (pl.get("name") == name
                and (pl.get("owner") or {}).get("id") == user_id
                and PLAYLIST_DESCRIPTION.lower() in description
                and not pl.get("collaborative", False)):
            return pl["id"]
    return None


def create_playlist(sp, name: str) -> str:
    res = sp_call(sp._post, "me/playlists", payload={
        "name": name, "public": False, "description": PLAYLIST_DESCRIPTION})
    if not isinstance(res, dict) or not res.get("id"):
        raise RecommendError(f"Playlist creation for '{name}' returned no id.")
    return res["id"]


def write_playlist(sp, playlist_id: str, uris: List[str]) -> None:
    """Replace contents (first <=100 via PUT, rest appended via POST). Raises on failure."""
    path = f"playlists/{playlist_id}/items"
    chunks = list(chunked(uris, WRITE_CHUNK)) or [[]]
    res = sp_call(sp._put, path, payload={"uris": chunks[0]})
    if not isinstance(res, dict) or not res.get("snapshot_id"):
        raise RecommendError("Playlist replace did not confirm (no snapshot id).")
    for chunk in chunks[1:]:
        res = sp_call(sp._post, path, payload={"uris": chunk})
        if not isinstance(res, dict) or not res.get("snapshot_id"):
            raise RecommendError("Playlist append did not confirm (no snapshot id).")


def get_stranger_client():
    """Client-credentials view used only to verify playlist profile exposure."""
    from spotipy import Spotify
    from spotipy.oauth2 import SpotifyClientCredentials

    return Spotify(auth_manager=SpotifyClientCredentials())


def make_private_and_verify(sp, user_id: str, playlist_id: str, stranger_client=None) -> None:
    """Set private and verify owner, API privacy fields, and public-profile absence."""
    sp_call(sp._put, f"playlists/{playlist_id}", payload={"public": False})
    try:
        details = sp_call(
            sp._get, f"playlists/{playlist_id}",
            fields="public,collaborative,owner(id)",
        )
        if (not isinstance(details, dict)
                or (details.get("owner") or {}).get("id") != user_id
                or details.get("public") is not False
                or details.get("collaborative") is not False):
            raise RecommendError(
                "Spotify did not confirm the owned playlist is private and non-collaborative; "
                "no tracks were changed.")
    except RecommendError:
        raise
    except Exception as exc:
        raise RecommendError(
            f"Could not verify playlist privacy ({describe_error(exc)}); no tracks were changed.") from exc

    stranger = stranger_client or get_stranger_client()
    try:
        for playlist in paged(stranger, sp_call(stranger.user_playlists, user_id, limit=50)):
            if playlist.get("id") == playlist_id:
                raise RecommendError(
                    "Playlist is still visible in the public profile; no tracks were changed.")
    except RecommendError:
        raise
    except Exception as exc:
        raise RecommendError(
            f"Could not verify playlist privacy ({describe_error(exc)}); no tracks were changed.") from exc


def ensure_and_write(
    sp,
    user_id: str,
    name: str,
    uris: List[str],
    stranger_client=None,
) -> str:
    pid = find_playlist(sp, user_id, name)
    if pid:
        log.info("Found owned tagged playlist '%s' — enforcing privacy before replacing.", name)
    else:
        pid = create_playlist(sp, name)
        log.info("Created playlist '%s'; verifying privacy before adding tracks.", name)
    make_private_and_verify(sp, user_id, pid, stranger_client=stranger_client)
    write_playlist(sp, pid, uris)
    return pid

# ── run ───────────────────────────────────────────────────────────────────────

def run(
    sp,
    lastfm_key: str = "",
    write: bool = False,
    now: Optional[datetime] = None,
    month_override: Optional[str] = None,
    expected_user_id: Optional[str] = None,
    stranger_client=None,
) -> Dict[str, Any]:
    """Returns {"written": bool, "picks": [...], "provider": str, "message": str, "code": int}.
    Raises on API/write failure; "written" is only True after every write is confirmed."""
    now = now or datetime.now()
    month = month_override or now.strftime("%Y-%m")
    name = playlist_name(now, month)
    result: Dict[str, Any] = {
        "written": False, "outcome": "running", "playlist_id": None,
        "picks": [], "provider": None, "playlist": name, "seed_source": None,
    }

    if write and not expected_user_id:
        raise RecommendError(f"{EXPECTED_USER_ENV} is required before playlist writes.")
    user_id = sp_call(sp.me).get("id")
    if not user_id:
        raise RecommendError("Spotify did not return the authenticated account ID; no changes made.")
    if expected_user_id and user_id != expected_user_id:
        raise RecommendError("Authenticated Spotify account did not match the expected owner; no changes made.")
    seeds = gather_seeds(sp)
    result["seed_source"] = seeds["seed_source"]
    if not seeds["tracks"]:
        result.update(code=2, outcome="no_seeds",
                      message=f"No discovery provider could run: no top tracks available ({seeds['seed_source']}). "
                                      "Listen for a while, then retry.")
        return result
    library = gather_library_ids(sp, user_id)

    fallback_reason = ""
    try:
        prov = provider_hivemind(sp, seeds, month)
    except RecommendError as exc:
        fallback_reason = str(exc)
        log.warning("Playlist co-occurrence unavailable: %s", fallback_reason)
        prov = {"name": "hivemind", "candidates": [], "exclude_seed_artists": True,
                "detail": fallback_reason, "available": False}

    if not prov.get("candidates"):
        if lastfm_key:
            try:
                lastfm = provider_lastfm(sp, seeds, lastfm_key)
                if lastfm.get("candidates"):
                    prov = lastfm
            except Exception as exc:
                detail = str(exc) if isinstance(exc, RecommendError) else describe_error(exc)
                fallback_reason = f"{fallback_reason}; Last.fm fallback failed: {detail}".strip("; ")
                log.warning("Last.fm fallback unavailable: %s", detail)
        if not prov.get("candidates"):
            catalog = provider_catalog(sp, seeds, month)
            if fallback_reason:
                catalog["detail"] += f"; co-occurrence unavailable: {fallback_reason}"
            prov = catalog
    result["provider"] = prov["name"]
    exclude = library | seeds["known_ids"]
    sel = select_tracks(
        prov["candidates"], exclude,
        seeds["artist_ids"] if prov["exclude_seed_artists"] else set(), month,
    )
    picks = sel["picks"]
    result["picks"] = picks
    s = sel["stats"]
    result["summary"] = (
        f"seed_source={seeds['seed_source']}; provider={prov['name']} ({prov['detail']}); candidates={s['input']}, "
        f"duplicates={s['duplicate']}, known/library={s['excluded']}, seed-artist={s['seed_artist']}, "
        f"eligible={s['eligible']}, picked={len(picks)}"
    )
    if not picks:
        result.update(code=0, outcome="no_candidates",
                      message=f"No candidates to {'write' if write else 'preview'} — "
                                      f"{result['summary']}. Nothing written.")
        return result
    if not write:
        result.update(code=0, outcome="preview",
                      message=f"DRY RUN — seed source: {seeds['seed_source']}; nothing written. "
                              f"Re-run with --write to update '{name}'.")
        return result

    playlist_id = ensure_and_write(
        sp, user_id, name, [p["uri"] for p in picks], stranger_client=stranger_client)
    result.update(written=True, outcome="written", playlist_id=playlist_id, code=0,
                  message=f"WRITTEN — '{name}' (private) now has {len(picks)} tracks.")
    return result


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Monthly Spotify discovery playlist (dry run unless --write).")
    ap.add_argument("--write", action="store_true", help="create/replace the private monthly playlist")
    ap.add_argument("--dry-run", action="store_true", help="preview only (default; kept for compatibility)")
    ap.add_argument("--month", help="monthly playlist label in YYYY-MM format (defaults to current month)")
    ap.add_argument("--run-label", help="optional short label shown in logs/output")
    ap.add_argument("--json", action="store_true", help="emit one sanitized structured result object")
    args = ap.parse_args(argv)
    if args.write and args.dry_run:
        ap.error("--write and --dry-run are mutually exclusive")
    if args.month:
        try:
            parsed_month = datetime.strptime(args.month, "%Y-%m")
            if parsed_month.strftime("%Y-%m") != args.month:
                raise ValueError("not canonical YYYY-MM")
        except ValueError:
            ap.error("--month must be a valid YYYY-MM value")

    _setup_logging(json_mode=args.json)
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=ENV_PATH)
    key = os.getenv("LASTFM_API_KEY", "")
    expected_user_id = os.getenv(EXPECTED_USER_ENV, "").strip()
    log.info("Monthly Recommend — mode=%s, last.fm=%s, month=%s, run_label=%s",
             "WRITE" if args.write else "DRY RUN",
             "configured" if key else "not configured (Spotify playlist discovery)",
             args.month or datetime.now().strftime("%Y-%m"), (args.run_label or "-")[:60])
    if args.write and not expected_user_id:
        log.error("%s is required for --write; nothing was written.", EXPECTED_USER_ENV)
        message = f"{EXPECTED_USER_ENV} is required for --write. Nothing was written."
        if args.json:
            print(json.dumps(_cli_result("error", 1, False, args.month, None, message)))
        else:
            print(f"FAILED: {message}")
        return 1
    try:
        res = run(get_client(), key, args.write, month_override=args.month,
                  expected_user_id=expected_user_id or None)
    except RecommendError as e:
        detail = describe_error(e)
        log.error("%s", detail)
        message = detail + (" Playlist may be partially updated." if args.write else " Nothing was written.")
        if args.json:
            print(json.dumps(_cli_result("error", 1, False, args.month, None, message)))
        else:
            print(f"FAILED: {message}")
        return 1
    except Exception as e:
        detail = describe_error(e)
        log.error("Run failed (%s).", type(e).__name__)
        message = detail + (" Playlist may be partially updated; rerun --write."
                            if args.write else " Nothing was written.")
        if args.json:
            print(json.dumps(_cli_result("error", 1, False, args.month, None, message)))
        else:
            print(f"FAILED: {message}")
        return 1

    if args.json:
        print(json.dumps(_cli_result(res["outcome"], res["code"], res["written"],
                                     res.get("playlist"), res.get("playlist_id"), res["message"],
                                     seed_source=res.get("seed_source"))))
    else:
        for p in res["picks"]:
            lane = f" [{p.get('lane')}]" if p.get("lane") else ""
            print(f"  + {p['name'][:48]:<48}  {p['artist_name'][:24]}{lane}")
            for source in p.get("sources", [])[:1]:
                print(f"      from: {source[:70]}")
        print(res.get("summary", ""))
        print(res["message"])
    return res["code"]


def _cli_result(outcome: str, code: int, written: bool, playlist: Optional[str],
                playlist_id: Optional[str], message: str,
                seed_source: Optional[str] = None) -> Dict[str, Any]:
    """Structured runner contract; deliberately excludes track details and raw errors."""
    return {
        "outcome": outcome,
        "code": code,
        "written": bool(written),
        "playlist": playlist,
        "playlist_id": playlist_id,
        "message": message[:500],
        "seed_source": seed_source,
    }


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Aborted.")
        sys.exit(130)
