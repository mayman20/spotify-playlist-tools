#!/usr/bin/env python3
"""
liked_add.py

Strictly mirror your Spotify Liked Songs into a playlist in the SAME order as the Liked page:
  • newest → oldest (newest at the top)
  • removes tracks not in Liked Songs
  • shows a single-line progress bar (no wrapping)

Usage:
  python3 scripts/liked_add/liked_add.py

Optional env vars:
  PLAYLIST_NAME=Liked Songs Mirror
  PLAYLIST_PUBLIC=false

Requires:
  pip install spotipy python-dotenv

.env must contain:
  SPOTIPY_CLIENT_ID=...
  SPOTIPY_CLIENT_SECRET=...
  SPOTIPY_REDIRECT_URI=http://127.0.0.1:8080/callback

Scopes:
  user-library-read playlist-read-private playlist-modify-private playlist-modify-public
"""

import os
import sys
import time
import shutil
import logging
from pathlib import Path
from typing import List, Tuple

from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

SCOPES = (
    "user-library-read "
    "playlist-read-private "
    "playlist-modify-private "
    "playlist-modify-public"
)

DEFAULT_PLAYLIST_NAME = "Liked Songs Mirror"
DEFAULT_PLAYLIST_PUBLIC = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ──────────────────────────────────────────────────────────────────────────────
# Auth & helpers
# ──────────────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_CACHE_PATH = PROJECT_ROOT / ".cache" / "liked_mirror.cache"

def auth_spotify(cache_path: str | None = None) -> spotipy.Spotify:
    load_dotenv(dotenv_path=ENV_PATH)
    resolved_cache_path = cache_path or str(DEFAULT_CACHE_PATH)
    Path(resolved_cache_path).parent.mkdir(parents=True, exist_ok=True)
    return spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            scope=SCOPES,
            cache_path=resolved_cache_path,
            open_browser=True,
        )
    )

def backoff(call, *args, **kwargs):
    """Retry/backoff for rate limits & transient errors."""
    delay = 1.0
    for attempt in range(6):
        try:
            return call(*args, **kwargs)
        except SpotifyException as e:
            if attempt == 5:
                raise
            if e.http_status == 429:
                retry_after = 2
                try:
                    retry_after = int(getattr(e, "headers", {}).get("Retry-After", 2))
                except Exception:
                    pass
                logging.warning("Rate limited. Retrying after %ss…", retry_after)
                time.sleep(max(retry_after, 2))
            else:
                raise
        except (TimeoutError, ConnectionError):
            if attempt == 5:
                raise
            logging.warning("Connection failed. Retrying in %.1fs…", delay)
            time.sleep(delay)
            delay = min(delay * 2, 16)

# ──────────────────────────────────────────────────────────────────────────────
# Progress bar (single-line, non-wrapping)
# ──────────────────────────────────────────────────────────────────────────────

def render_progress(current: int, total: int, label: str = "Collecting Liked Songs"):
    """Render a single-line progress bar sized to terminal, clearing the line each write."""
    if total <= 0:
        return
    width = shutil.get_terminal_size(fallback=(80, 20)).columns
    ratio = max(0.0, min(1.0, current / total))
    pct_text = f"{ratio*100:5.1f}%"
    prefix = f"{label} "
    counts = f"{current}/{total}"
    fixed = len(prefix) + 2 + 1 + len(counts) + 2 + len(pct_text)  # text + [bar] + spaces
    bar_len = max(10, min(40, width - fixed - 2))
    filled = int(ratio * bar_len)
    bar = "█" * filled + " " * (bar_len - filled)
    sys.stdout.write("\r\033[K" + f"{prefix}[{bar}] {counts} ({pct_text})")
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\r\033[K" + f"{prefix}[{bar}] {counts} ({pct_text})\n")
        sys.stdout.flush()

# ──────────────────────────────────────────────────────────────────────────────
# Data fetch
# ──────────────────────────────────────────────────────────────────────────────

def fetch_all_saved_tracks(sp: spotipy.Spotify) -> List[Tuple[str, str]]:
    """
    Return list of (track_id, added_at) for all Liked Songs, sorted NEWEST → OLDEST
    (same as the Liked Songs page). Shows a loading bar. Skips local/missing tracks.
    """
    items: List[Tuple[str, str]] = []
    limit = 50
    offset = 0

    resp = backoff(sp.current_user_saved_tracks, limit=limit, offset=offset)
    total = int(resp.get("total", 0))
    fetched = 0
    render_progress(0, total)

    while True:
        batch = resp.get("items", [])
        fetched += len(batch)

        for it in batch:
            track = it.get("track") or {}
            tid = track.get("id")
            added_at = it.get("added_at") or ""
            if tid:
                items.append((tid, added_at))

        render_progress(min(fetched, total), total)

        if resp.get("next") is None or not batch:
            break
        offset += limit
        resp = backoff(sp.current_user_saved_tracks, limit=limit, offset=offset)

    # NEWEST → OLDEST (top to bottom)
    items.sort(key=lambda x: x[1], reverse=True)
    return items

# ──────────────────────────────────────────────────────────────────────────────
# Playlist helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_or_create_playlist(sp: spotipy.Spotify, user_id: str, name: str, public: bool) -> str:
    """Find playlist by exact name owned by user or create it (no description)."""
    limit = 50
    offset = 0
    while True:
        resp = backoff(sp.current_user_playlists, limit=limit, offset=offset)
        for pl in resp.get("items", []):
            if pl.get("name") == name and pl.get("owner", {}).get("id") == user_id:
                # Ensure description is cleared
                try:
                    backoff(sp.playlist_change_details, pl["id"], description="")
                except Exception:
                    logging.warning("Could not clear the playlist description.")
                return pl["id"]
        if resp.get("next") is None:
            break
        offset += limit

    created = backoff(
        sp.user_playlist_create,
        user=user_id,
        name=name,
        public=public,
        # intentionally omit description to keep it blank
    )
    # Make sure any default description is cleared
    try:
        backoff(sp.playlist_change_details, created["id"], description="")
    except Exception:
        pass
    return created["id"]

def set_playlist_exact(sp: spotipy.Spotify, playlist_id: str, desired_ids: List[str]) -> None:
    """
    Set playlist contents to exactly desired_ids (order preserved: top→bottom).
    Removes any tracks not in desired and adds missing ones.
    """
    if len(desired_ids) == 0:
        # Clear playlist
        try:
            backoff(sp.playlist_replace_items, playlist_id, [])
            return
        except Exception:
            # Fallback: remove all existing items in chunks
            existing = fetch_playlist_track_ids(sp, playlist_id)
            for i in range(0, len(existing), 100):
                batch = existing[i:i+100]
                backoff(sp.playlist_remove_all_occurrences_of_items, playlist_id, batch)
            return

    head = desired_ids[:100]
    backoff(sp.playlist_replace_items, playlist_id, head)
    for i in range(100, len(desired_ids), 100):
        back = desired_ids[i:i+100]
        backoff(sp.playlist_add_items, playlist_id, back)

def fetch_playlist_track_ids(sp: spotipy.Spotify, playlist_id: str) -> List[str]:
    """Return playlist track IDs in current order (top → bottom)."""
    ids: List[str] = []
    limit = 100
    offset = 0
    fields = "items(track(id)),next"
    while True:
        resp = backoff(sp.playlist_items, playlist_id, fields=fields, limit=limit, offset=offset)
        batch = resp.get("items", [])
        for it in batch:
            tid = (it.get("track") or {}).get("id")
            if tid:
                ids.append(tid)
        if resp.get("next") is None or not batch:
            break
        offset += limit
    return ids

# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    load_dotenv(dotenv_path=ENV_PATH)
    playlist_name = os.getenv("PLAYLIST_NAME", DEFAULT_PLAYLIST_NAME)
    playlist_public = os.getenv("PLAYLIST_PUBLIC", str(DEFAULT_PLAYLIST_PUBLIC)).lower() == "true"

    sp = auth_spotify()
    me = backoff(sp.me)
    user_id = me["id"]

    logging.info("Using playlist name: %s (public=%s)", playlist_name, playlist_public)
    playlist_id = get_or_create_playlist(sp, user_id, playlist_name, playlist_public)

    logging.info("Fetching all Liked Songs…")
    liked = fetch_all_saved_tracks(sp)  # NEWEST → OLDEST
    liked_ids = [tid for (tid, _) in liked]
    logging.info("Liked Songs loaded: %d (newest → oldest)", len(liked))

    logging.info("Mirroring playlist to exactly match Liked Songs (newest → oldest)…")
    set_playlist_exact(sp, playlist_id, liked_ids)

    # (Optional) sanity log
    final_count = len(fetch_playlist_track_ids(sp, playlist_id))
    logging.info("✅ Mirror complete — %d tracks in '%s'.", final_count, playlist_name)

if __name__ == "__main__":
    main()
