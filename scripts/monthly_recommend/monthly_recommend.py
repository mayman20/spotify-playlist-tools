#!/usr/bin/env python3
"""
monthly_recommend.py

Builds four monthly discovery playlists seeded by your top 50 tracks this
month (short_term = last ~4 weeks).  Run all four and pick whichever sounds
best to guide future tuning.

  [named]  — Playlist co-occurrence using ONLY artist-specific playlists
              (playlist title must contain the artist name; no generic fallback).
  [multi]  — Playlist co-occurrence across mixed playlists (named preferred,
              some generic allowed), but only keeps tracks that appeared in
              playlists seeded by at least 2 different top artists.
  [lastfm] — Uses last.fm artist.getSimilar to find adjacent artists, then
              pulls their Spotify top tracks. Requires LASTFM_API_KEY in .env.
  [track]  — Uses last.fm track.getSimilar for each of your top songs directly.
              More specific than artist-level — finds music adjacent to the
              specific songs you've been playing, not just the artist.
              Also requires LASTFM_API_KEY in .env.

Playlists created/overwritten each run:
  "Monthly Recs YYYY-MM [named]"
  "Monthly Recs YYYY-MM [multi]"
  "Monthly Recs YYYY-MM [lastfm]"   (skipped if LASTFM_API_KEY not set)
  "Monthly Recs YYYY-MM [track]"    (skipped if LASTFM_API_KEY not set)

Logs written to monthly_recommend.log in this folder (512 KB rotating, 2 backups).
"""

import json
import os
import sys
import time
import logging
import random
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, List, Set, Tuple

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException
from dotenv import load_dotenv

# ── paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
ENV_PATH     = PROJECT_ROOT / ".env"
CACHE_PATH   = PROJECT_ROOT / ".cache" / "spotipy_general.cache"
LOG_PATH     = SCRIPT_DIR / "monthly_recommend.log"

# ── tunables ──────────────────────────────────────────────────────────────────

PLAYLIST_NAME_PREFIX    = "Monthly Recs"
TARGET_TRACK_COUNT      = 30

# [named] / [multi]: how many top artists to seed from, playlists per artist.
TOP_SEED_ARTISTS        = 20
PLAYLISTS_PER_ARTIST    = 10
TRACKS_PER_PLAYLIST     = 40

MAX_PER_ARTIST          = 2     # diversity cap: max tracks from one artist
POPULARITY_MIN          = 20
POPULARITY_MAX          = 65
MARKET                  = "US"

# [lastfm] artist.getSimilar: similar artists per seed artist.
LASTFM_SIMILAR_LIMIT    = 5
LASTFM_TOP_SIMILAR      = 40

# [track] track.getSimilar: how many of your top tracks to use as seeds,
# and how many last.fm similar tracks to request per seed.
TOP_SEED_TRACKS         = 25
LASTFM_TRACK_SIMILAR    = 10

VAULTED_TAG             = "[spotipy:vaulted_add]"

# ── logging ───────────────────────────────────────────────────────────────────

def _setup_logging() -> logging.Logger:
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    file_h = RotatingFileHandler(
        LOG_PATH, maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
    )
    file_h.setFormatter(fmt)
    logger = logging.getLogger("monthly_recommend")
    logger.setLevel(logging.INFO)
    logger.addHandler(console)
    logger.addHandler(file_h)
    return logger

log = _setup_logging()

# ── helpers ───────────────────────────────────────────────────────────────────

def sp_call(fn, *args, **kwargs):
    """Thin 429-retry wrapper."""
    delay = 2
    for attempt in range(6):
        try:
            return fn(*args, **kwargs)
        except SpotifyException as e:
            if e.http_status == 429:
                wait = max(int(getattr(e, "headers", {}).get("Retry-After", delay)), delay)
                log.warning("Rate-limited — sleeping %ds (attempt %d/6).", wait, attempt + 1)
                time.sleep(wait)
                delay *= 2
                continue
            raise
    return fn(*args, **kwargs)


def chunked(lst: list, size: int):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]

# ── auth ──────────────────────────────────────────────────────────────────────

def get_client() -> spotipy.Spotify:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            scope=(
                "user-top-read "
                "user-read-recently-played "
                "user-library-read "
                "playlist-read-private "
                "playlist-modify-private "
                "playlist-modify-public"
            ),
            cache_path=str(CACHE_PATH),
        )
    )

# ── seeds ─────────────────────────────────────────────────────────────────────

def gather_seeds(
    sp: spotipy.Spotify,
) -> Tuple[List[Dict], List[Dict], Set[str]]:
    """
    Pull short_term top 50 tracks.
    Returns:
        seed_tracks     Raw top track objects (for [track] approach).
        seed_artists    List of {id, name, freq} sorted by freq descending.
        seed_artist_ids Full set of artist IDs (used to exclude seed artists from recs).
    """
    res = sp_call(sp.current_user_top_tracks, limit=50, time_range="short_term")
    items = res.get("items", [])

    freq:  Dict[str, int] = defaultdict(int)
    names: Dict[str, str] = {}

    for track in items:
        for artist in track.get("artists", []):
            aid = artist.get("id")
            if aid:
                freq[aid] += 1
                names.setdefault(aid, artist.get("name", ""))

    sorted_artists = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    seed_artists = [{"id": aid, "name": names[aid], "freq": count} for aid, count in sorted_artists]
    seed_ids = {a["id"] for a in seed_artists}

    log.info(
        "Seed artists (%d unique): %s",
        len(seed_artists),
        ", ".join(f"{a['name']}(×{a['freq']})" for a in seed_artists[:8]),
    )
    log.info(
        "Seed tracks (top %d): %s",
        min(TOP_SEED_TRACKS, len(items)),
        ", ".join(
            f"{t.get('name', '?')} – {(t.get('artists') or [{}])[0].get('name', '?')}"
            for t in items[:5]
        ),
    )
    return items, seed_artists, seed_ids

# ── fast exclusion set ────────────────────────────────────────────────────────

def gather_known_track_ids(sp: spotipy.Spotify) -> Set[str]:
    """Short+medium term top tracks and recently played — pre-filter before library load."""
    known: Set[str] = set()
    for tr in ("short_term", "medium_term"):
        res = sp_call(sp.current_user_top_tracks, limit=50, time_range=tr)
        for t in res.get("items", []):
            if t.get("id"):
                known.add(t["id"])
    try:
        res = sp_call(sp.current_user_recently_played, limit=50)
        for item in res.get("items", []):
            if (item.get("track") or {}).get("id"):
                known.add(item["track"]["id"])
    except SpotifyException:
        pass
    log.info("Fast exclusion set: %d tracks.", len(known))
    return known

# ── full library ──────────────────────────────────────────────────────────────

def gather_library_track_ids(sp: spotipy.Spotify, user_id: str) -> Set[str]:
    """Load every track the user already owns (vaulted playlist, else liked songs)."""
    try:
        res = sp_call(sp.current_user_playlists, limit=50)
        while True:
            for pl in res.get("items", []):
                if (pl.get("owner") or {}).get("id") != user_id:
                    continue
                if VAULTED_TAG.lower() not in (pl.get("description") or "").lower():
                    continue
                total = int((pl.get("tracks") or {}).get("total") or 0)
                log.info(
                    "Vaulted playlist '%s' (%d tracks) — loading for library filter.",
                    pl["name"], total,
                )
                ids: Set[str] = set()
                page = sp_call(
                    sp.playlist_tracks, pl["id"],
                    fields="items(track.id),next", limit=100,
                )
                while page:
                    for item in page.get("items", []):
                        tid = (item.get("track") or {}).get("id")
                        if tid:
                            ids.add(tid)
                    page = sp_call(sp.next, page) if page.get("next") else None
                log.info("Library loaded from vaulted: %d unique IDs.", len(ids))
                return ids
            if not res.get("next"):
                break
            res = sp_call(sp.next, res)
    except SpotifyException:
        pass

    log.info("No vaulted playlist found — loading liked songs.")
    ids = set()
    res = sp_call(sp.current_user_saved_tracks, limit=50)
    while res:
        for item in res.get("items", []):
            tid = (item.get("track") or {}).get("id")
            if tid:
                ids.add(tid)
        res = sp_call(sp.next, res) if res.get("next") else None
    log.info("Library loaded from liked songs: %d IDs.", len(ids))
    return ids

# ── approach A: named playlists only ─────────────────────────────────────────

def gather_named_playlist_tracks(
    sp: spotipy.Spotify,
    seed_artists: List[Dict],
    seed_artist_ids: Set[str],
    known_ids: Set[str],
) -> List[Dict]:
    """
    Playlist co-occurrence using ONLY playlists whose title contains the
    artist's name.  Skips any artist with no such playlists — no generic fallback.
    """
    track_scores:   Dict[str, float] = defaultdict(float)
    track_meta:     Dict[str, Dict]  = {}
    seen_playlists: Set[str]         = set()
    playlists_searched = 0

    for artist in seed_artists[:TOP_SEED_ARTISTS]:
        try:
            res = sp_call(sp.search, q=artist["name"], type="playlist", limit=20)
            playlists = (res.get("playlists") or {}).get("items") or []
        except Exception:
            log.warning("  [named] search failed for '%s', skipping.", artist["name"])
            continue

        artist_name_lower = artist["name"].lower()
        named = [
            pl for pl in playlists
            if pl and artist_name_lower in (pl.get("name") or "").lower()
        ][:PLAYLISTS_PER_ARTIST]

        if not named:
            log.info("  [named] [%s] — no artist-named playlists, skipping.", artist["name"])
            continue

        artist_pl_count = 0
        for pl in named:
            pid = pl.get("id")
            if not pid or pid in seen_playlists:
                continue
            seen_playlists.add(pid)
            playlists_searched += 1
            artist_pl_count += 1

            try:
                page = sp_call(sp.playlist_tracks, pid, limit=100)
            except Exception:
                continue

            items = list((page or {}).get("items", []) or [])
            random.shuffle(items)

            sampled = 0
            for item in items:
                if sampled >= TRACKS_PER_PLAYLIST:
                    break
                track = item.get("track") or {}
                tid   = track.get("id")
                if not tid or tid in known_ids:
                    continue
                artists = track.get("artists") or []
                if {a.get("id") for a in artists if a.get("id")} & seed_artist_ids:
                    continue
                primary = artists[0] if artists else {}
                track_scores[tid] += artist["freq"]
                if tid not in track_meta:
                    track_meta[tid] = {
                        "id":          tid,
                        "name":        track.get("name", ""),
                        "popularity":  track.get("popularity", 0),
                        "artist_id":   primary.get("id") or tid,
                        "artist_name": primary.get("name", ""),
                        "uri":         track.get("uri") or f"spotify:track:{tid}",
                    }
                sampled += 1

        log.info(
            "  [named] [%s] (×%d) → %d playlist(s)",
            artist["name"], artist["freq"], artist_pl_count,
        )

    candidates = [{**track_meta[tid], "artist_score": track_scores[tid]} for tid in track_meta]
    log.info("[named] candidates: %d tracks across %d playlists.", len(candidates), playlists_searched)
    return candidates

# ── approach B: mixed playlists + ≥2 seed contributors ───────────────────────

def gather_multi_seed_tracks(
    sp: spotipy.Spotify,
    seed_artists: List[Dict],
    seed_artist_ids: Set[str],
    known_ids: Set[str],
) -> List[Dict]:
    """
    Playlist co-occurrence across a mixed pool (named playlists preferred,
    some generic allowed as fallback).  Only keeps tracks that appeared in
    playlists seeded by at least 2 different top artists — the ≥2 filter
    is the quality gate here, not the playlist name.

    A track showing up in both a Dire Straits search and a Jamiroquai search
    is a meaningful signal regardless of whether those playlists had the
    artist name in the title.
    """
    track_scores:       Dict[str, float]    = defaultdict(float)
    track_contributors: Dict[str, Set[str]] = defaultdict(set)
    track_meta:         Dict[str, Dict]     = {}
    seen_playlists:     Set[str]            = set()
    playlists_searched = 0

    for artist in seed_artists[:TOP_SEED_ARTISTS]:
        try:
            res = sp_call(sp.search, q=artist["name"], type="playlist", limit=20)
            playlists = (res.get("playlists") or {}).get("items") or []
        except Exception:
            continue

        artist_name_lower = artist["name"].lower()
        named  = [pl for pl in playlists if pl and artist_name_lower in (pl.get("name") or "").lower()]
        ranked = [pl for pl in playlists if pl and pl not in named]
        # Prefer named; fill remaining slots with top generic results
        ordered = named[:3] + ranked[:max(0, PLAYLISTS_PER_ARTIST - len(named[:3]))]

        for pl in ordered:
            pid = pl.get("id") if pl else None
            if not pid or pid in seen_playlists:
                continue
            seen_playlists.add(pid)
            playlists_searched += 1

            try:
                page = sp_call(sp.playlist_tracks, pid, limit=100)
            except Exception:
                continue

            items = list((page or {}).get("items", []) or [])
            random.shuffle(items)

            sampled = 0
            for item in items:
                if sampled >= TRACKS_PER_PLAYLIST:
                    break
                track = item.get("track") or {}
                tid   = track.get("id")
                if not tid or tid in known_ids:
                    continue
                artists = track.get("artists") or []
                if {a.get("id") for a in artists if a.get("id")} & seed_artist_ids:
                    continue
                primary = artists[0] if artists else {}
                track_scores[tid] += artist["freq"]
                track_contributors[tid].add(artist["id"])
                if tid not in track_meta:
                    track_meta[tid] = {
                        "id":          tid,
                        "name":        track.get("name", ""),
                        "popularity":  track.get("popularity", 0),
                        "artist_id":   primary.get("id") or tid,
                        "artist_name": primary.get("name", ""),
                        "uri":         track.get("uri") or f"spotify:track:{tid}",
                    }
                sampled += 1

        log.info(
            "  [multi] [%s] (×%d) → %d playlist(s) (%d named)",
            artist["name"], artist["freq"],
            len(ordered),
            len(named[:3]),
        )

    multi = {tid for tid, seeds in track_contributors.items() if len(seeds) >= 2}
    candidates = [
        {**track_meta[tid], "artist_score": track_scores[tid]}
        for tid in multi
    ]
    log.info(
        "[multi] candidates: %d (≥2 seed contributors) from %d total, %d playlists.",
        len(candidates), len(track_meta), playlists_searched,
    )
    return candidates

# ── approach C: last.fm artist.getSimilar ────────────────────────────────────

def gather_lastfm_tracks(
    sp: spotipy.Spotify,
    seed_artists: List[Dict],
    seed_artist_ids: Set[str],
    known_ids: Set[str],
    lastfm_key: str,
) -> List[Dict]:
    """
    For each seed artist, fetch similar artists from last.fm's artist.getSimilar
    API (co-listening patterns).  Resolve on Spotify, pull their top tracks.
    Score = sum(lastfm_match × seed_freq) across all seeds that named the artist.
    """
    similar_scores: Dict[str, float] = defaultdict(float)
    similar_names:  Dict[str, str]   = {}

    for artist in seed_artists[:TOP_SEED_ARTISTS]:
        url = (
            "https://ws.audioscrobbler.com/2.0/"
            "?method=artist.getsimilar"
            f"&artist={urllib.parse.quote(artist['name'])}"
            f"&api_key={lastfm_key}"
            f"&limit={LASTFM_SIMILAR_LIMIT}"
            "&format=json"
        )
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except Exception:
            log.warning("  [lastfm] API call failed for '%s'.", artist["name"])
            continue

        similar = (data.get("similarartists") or {}).get("artist") or []
        resolved = []
        for sim in similar:
            match = float(sim.get("match") or 0)
            name  = (sim.get("name") or "").strip()
            if not name or match < 0.05:
                continue
            try:
                res = sp_call(sp.search, q=f'artist:"{name}"', type="artist", limit=3)
                items = (res.get("artists") or {}).get("items") or []
                for candidate in items:
                    if (candidate.get("name") or "").strip().lower() == name.lower():
                        aid = candidate["id"]
                        if aid not in seed_artist_ids:
                            similar_scores[aid] += match * artist["freq"]
                            similar_names[aid] = candidate["name"]
                            resolved.append(name)
                        break
            except Exception:
                continue

        log.info(
            "  [lastfm] %s → %s",
            artist["name"],
            ", ".join(resolved) if resolved else "(none resolved)",
        )

    log.info("[lastfm] %d unique similar artists resolved.", len(similar_scores))

    track_scores: Dict[str, float] = defaultdict(float)
    track_meta:   Dict[str, Dict]  = {}

    top_similar = sorted(similar_scores.items(), key=lambda x: -x[1])[:LASTFM_TOP_SIMILAR]
    for aid, score in top_similar:
        try:
            res = sp_call(sp.artist_top_tracks, aid, country=MARKET)
            tracks = res.get("tracks") or []
        except Exception:
            continue
        for track in tracks:
            tid = track.get("id")
            if not tid or tid in known_ids:
                continue
            artists = track.get("artists") or []
            if {a.get("id") for a in artists if a.get("id")} & seed_artist_ids:
                continue
            primary = artists[0] if artists else {}
            track_scores[tid] += score
            if tid not in track_meta:
                track_meta[tid] = {
                    "id":          tid,
                    "name":        track.get("name", ""),
                    "popularity":  track.get("popularity", 0),
                    "artist_id":   primary.get("id") or tid,
                    "artist_name": primary.get("name", ""),
                    "uri":         track.get("uri") or f"spotify:track:{tid}",
                }

    candidates = [{**track_meta[tid], "artist_score": track_scores[tid]} for tid in track_meta]
    log.info("[lastfm] candidates: %d tracks.", len(candidates))
    return candidates

# ── approach D: last.fm track.getSimilar ─────────────────────────────────────

def gather_lastfm_track_similar(
    sp: spotipy.Spotify,
    seed_tracks: List[Dict],
    seed_artist_ids: Set[str],
    known_ids: Set[str],
    lastfm_key: str,
) -> List[Dict]:
    """
    For each of your top tracks, ask last.fm which tracks people commonly listen
    to alongside that specific song.  More precise than artist-level similarity —
    "what goes with Maggot Brain" vs "what goes with Funkadelic in general".

    Score = sum(lastfm_match × position_weight) across all your seed tracks
    that nominated this track as similar.  Tracks similar to multiple of your
    top songs score highest.
    """
    track_scores: Dict[str, float] = defaultdict(float)
    track_meta:   Dict[str, Dict]  = {}
    n = min(TOP_SEED_TRACKS, len(seed_tracks))

    for i, seed in enumerate(seed_tracks[:n]):
        track_name  = seed.get("name", "")
        artists     = seed.get("artists") or [{}]
        artist_name = artists[0].get("name", "")
        # Tracks ranked higher in your top 50 get a stronger weight
        weight = 1.0 - (i / n) * 0.5   # decays from 1.0 → 0.5 across top N

        url = (
            "https://ws.audioscrobbler.com/2.0/"
            "?method=track.getsimilar"
            f"&artist={urllib.parse.quote(artist_name)}"
            f"&track={urllib.parse.quote(track_name)}"
            f"&api_key={lastfm_key}"
            f"&limit={LASTFM_TRACK_SIMILAR}"
            "&format=json"
        )
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except Exception:
            log.warning("  [track] last.fm failed for '%s'.", track_name)
            continue

        similar = (data.get("similartracks") or {}).get("track") or []
        resolved = []
        for sim in similar:
            match     = float(sim.get("match") or 0)
            sim_name  = (sim.get("name") or "").strip()
            sim_artist = ((sim.get("artist") or {}).get("name") or "").strip()
            if not sim_name or not sim_artist or match < 0.05:
                continue

            # Resolve to Spotify track (exact name + artist match)
            try:
                query = f'track:"{sim_name}" artist:"{sim_artist}"'
                res   = sp_call(sp.search, q=query, type="track", limit=3)
                items = (res.get("tracks") or {}).get("items") or []
                for candidate in items:
                    c_name   = (candidate.get("name") or "").strip().lower()
                    c_arts   = candidate.get("artists") or []
                    c_artist = (c_arts[0].get("name") or "").strip().lower() if c_arts else ""
                    if c_name == sim_name.lower() and c_artist == sim_artist.lower():
                        tid = candidate.get("id")
                        if not tid or tid in known_ids:
                            break
                        c_artist_ids = {a.get("id") for a in c_arts if a.get("id")}
                        if c_artist_ids & seed_artist_ids:
                            break
                        primary = c_arts[0] if c_arts else {}
                        track_scores[tid] += match * weight
                        if tid not in track_meta:
                            track_meta[tid] = {
                                "id":          tid,
                                "name":        candidate.get("name", ""),
                                "popularity":  candidate.get("popularity", 0),
                                "artist_id":   primary.get("id") or tid,
                                "artist_name": primary.get("name", ""),
                                "uri":         candidate.get("uri") or f"spotify:track:{tid}",
                            }
                        resolved.append(sim_name)
                        break
            except Exception:
                continue

        log.info(
            "  [track] '%s' – %s → %d similar resolved",
            track_name[:40], artist_name[:20], len(resolved),
        )

    candidates = [{**track_meta[tid], "artist_score": track_scores[tid]} for tid in track_meta]
    log.info("[track] candidates: %d tracks.", len(candidates))
    return candidates

# ── selection (shared) ────────────────────────────────────────────────────────

def select_tracks(
    candidates: List[Dict],
    library_ids: Set[str],
    label: str = "",
) -> List[str]:
    """
    Remove owned tracks, apply popularity window, score, cap per artist.

    Score = co-occurrence/similarity score × 2 + popularity curve
    Popularity curve peaks at 55 to favour mid-tier discovery.
    """

    def pop_score(pop: int) -> float:
        return max(0.0, 1.0 - abs(pop - 55) / 55.0)

    before = len(candidates)
    candidates = [t for t in candidates if t["id"] not in library_ids]
    log.info(
        "%s Library filter: removed %d (%d remain).",
        label, before - len(candidates), len(candidates),
    )

    artist_counts: Dict[str, int] = defaultdict(int)
    selected_uris: List[str] = []

    scored = [
        (t["artist_score"] * 2.0 + pop_score(t["popularity"]), t)
        for t in candidates
        if POPULARITY_MIN <= t.get("popularity", 0) <= POPULARITY_MAX
    ]
    scored.sort(key=lambda x: x[0], reverse=True)

    for score, track in scored:
        if len(selected_uris) >= TARGET_TRACK_COUNT:
            break
        if artist_counts[track["artist_id"]] >= MAX_PER_ARTIST:
            continue
        selected_uris.append(track["uri"])
        artist_counts[track["artist_id"]] += 1
        log.info(
            "  %s + %-48s  artist=%-22s  pop=%2d  score=%.2f",
            label,
            track["name"][:48],
            track["artist_name"][:22],
            track["popularity"],
            score,
        )

    log.info("%s Selected %d / %d.", label, len(selected_uris), TARGET_TRACK_COUNT)
    return selected_uris

# ── playlist management ───────────────────────────────────────────────────────

def find_or_create_playlist(sp: spotipy.Spotify, user_id: str, name: str) -> str:
    res = sp_call(sp.current_user_playlists, limit=50)
    while True:
        for pl in res.get("items", []):
            if pl.get("name") == name and (pl.get("owner") or {}).get("id") == user_id:
                log.info("Found existing '%s' — overwriting.", name)
                return pl["id"]
        if not res.get("next"):
            break
        res = sp_call(sp.next, res)
    new_pl = sp_call(
        sp.user_playlist_create,
        user=user_id, name=name, public=False,
        description="Auto-generated monthly picks. [spotipy:monthly_recommend] -*",
    )
    log.info("Created playlist '%s' (%s).", name, new_pl["id"])
    return new_pl["id"]


def overwrite_playlist(sp: spotipy.Spotify, playlist_id: str, track_uris: List[str]) -> None:
    sp_call(sp.playlist_replace_items, playlist_id, track_uris[:100])
    for chunk in chunked(track_uris[100:], 100):
        sp_call(sp.playlist_add_items, playlist_id, chunk)

# ── entry point ───────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("Monthly Recommend run — %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 60)

    load_dotenv(dotenv_path=ENV_PATH)
    lastfm_key = os.getenv("LASTFM_API_KEY", "")

    sp      = get_client()
    user_id = sp_call(sp.me)["id"]
    log.info("Authenticated as: %s", user_id)

    month = datetime.now().strftime("%Y-%m")
    log.info("Target month: %s", month)

    known_ids   = gather_known_track_ids(sp)
    library_ids = gather_library_track_ids(sp, user_id)

    seed_tracks, seed_artists, seed_artist_ids = gather_seeds(sp)
    if not seed_artists:
        log.error("No top tracks found — have you been listening recently?")
        sys.exit(1)

    combined_exclusions = library_ids | known_ids

    # ── Approach A: named playlists only ─────────────────────────────────────
    log.info("-" * 60)
    log.info("Approach [named]: co-occurrence, artist-named playlists only")
    name_a  = f"{PLAYLIST_NAME_PREFIX} {month} [named]"
    cands_a = gather_named_playlist_tracks(sp, seed_artists, seed_artist_ids, known_ids)
    uris_a  = select_tracks(cands_a, combined_exclusions, "[named]")
    if uris_a:
        pid_a = find_or_create_playlist(sp, user_id, name_a)
        overwrite_playlist(sp, pid_a, uris_a)
        log.info("[named] Done — '%s' updated with %d tracks.", name_a, len(uris_a))
    else:
        log.warning("[named] No tracks selected.")

    # ── Approach B: mixed playlists + ≥2 seed contributors ───────────────────
    log.info("-" * 60)
    log.info("Approach [multi]: mixed playlists, requires ≥2 seed artists contributing")
    name_b  = f"{PLAYLIST_NAME_PREFIX} {month} [multi]"
    cands_b = gather_multi_seed_tracks(sp, seed_artists, seed_artist_ids, known_ids)
    uris_b  = select_tracks(cands_b, combined_exclusions, "[multi]")
    if uris_b:
        pid_b = find_or_create_playlist(sp, user_id, name_b)
        overwrite_playlist(sp, pid_b, uris_b)
        log.info("[multi] Done — '%s' updated with %d tracks.", name_b, len(uris_b))
    else:
        log.warning("[multi] No tracks selected — try increasing TOP_SEED_ARTISTS.")

    # ── Approaches C + D require last.fm ─────────────────────────────────────
    log.info("-" * 60)
    if not lastfm_key:
        log.warning(
            "[lastfm/track] LASTFM_API_KEY not set in .env — skipping both last.fm approaches."
        )
    else:
        # ── Approach C: last.fm artist.getSimilar ────────────────────────────
        log.info("Approach [lastfm]: last.fm artist.getSimilar → Spotify top tracks")
        name_c  = f"{PLAYLIST_NAME_PREFIX} {month} [lastfm]"
        cands_c = gather_lastfm_tracks(sp, seed_artists, seed_artist_ids, known_ids, lastfm_key)
        uris_c  = select_tracks(cands_c, combined_exclusions, "[lastfm]")
        if uris_c:
            pid_c = find_or_create_playlist(sp, user_id, name_c)
            overwrite_playlist(sp, pid_c, uris_c)
            log.info("[lastfm] Done — '%s' updated with %d tracks.", name_c, len(uris_c))
        else:
            log.warning("[lastfm] No tracks selected.")

        # ── Approach D: last.fm track.getSimilar ─────────────────────────────
        log.info("-" * 60)
        log.info("Approach [track]: last.fm track.getSimilar on your top songs directly")
        name_d  = f"{PLAYLIST_NAME_PREFIX} {month} [track]"
        cands_d = gather_lastfm_track_similar(sp, seed_tracks, seed_artist_ids, known_ids, lastfm_key)
        uris_d  = select_tracks(cands_d, combined_exclusions, "[track]")
        if uris_d:
            pid_d = find_or_create_playlist(sp, user_id, name_d)
            overwrite_playlist(sp, pid_d, uris_d)
            log.info("[track] Done — '%s' updated with %d tracks.", name_d, len(uris_d))
        else:
            log.warning("[track] No tracks selected.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.warning("Aborted.")
        sys.exit(130)
    except Exception:
        log.exception("Unhandled error — full traceback:")
        sys.exit(1)
