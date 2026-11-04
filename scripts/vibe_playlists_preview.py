# vibe_playlists_tracktext.py
# Build "vibe" playlists using ONLY track-level data (no artist calls, no audio features, no previews).
# Signals: TF-IDF of track title + album title, title cues (remix/live/acoustic/etc),
#          duration, explicit, popularity, album/track position.
# Clustering excludes recency by default to avoid "recent likes" groupings.

import re
import time
from collections import defaultdict, Counter
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.decomposition import TruncatedSVD
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from scipy.sparse import hstack, csr_matrix

# =========================
# Config
# =========================
SCOPE = "user-library-read playlist-modify-private playlist-modify-public"
PLAYLIST_PREFIX = "Vibes • "
PUBLIC_PLAYLISTS = False
MAX_TRACKS_PER_PLAYLIST = 1000  # you can bump this
MAX_TRACKS = None               # e.g., 1200 for faster tests

AUTO_K = True
K_CANDIDATES = [8, 10, 12, 14]  # pick by silhouette
FIXED_K = 10                    # used if AUTO_K=False

INCLUDE_RECENCY_IN_CLUSTERING = False  # <- key change

TFIDF_MAX_FEATURES = 5000
SVD_DIM = 64                    # compress text features

RANDOM_STATE = 42
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
CACHE_PATH = PROJECT_ROOT / ".cache" / "spotipy_general.cache"

# =========================
# Auth
# =========================
def get_spotify():
    load_dotenv(dotenv_path=ENV_PATH)
    # If your environment injects proxies you want to ignore, uncomment:
    # import requests
    # sess = requests.Session(); sess.trust_env = False
    # return Spotify(auth_manager=SpotifyOAuth(scope=SCOPE), requests_session=sess)
    return Spotify(auth_manager=SpotifyOAuth(scope=SCOPE, cache_path=str(CACHE_PATH)))

# =========================
# Fetch liked tracks (track-only fields)
# =========================
def fetch_all_liked_tracks(sp: Spotify):
    items = []
    results = sp.current_user_saved_tracks(limit=50)
    while True:
        for it in results.get("items", []):
            t = it.get("track") or {}
            tid = t.get("id"); uri = t.get("uri")
            if not tid or not uri or len(tid) != 22:
                continue
            album = t.get("album") or {}
            items.append({
                "id": tid,
                "uri": uri,
                "name": t.get("name", "") or "",
                "album_name": (album.get("name") or ""),
                "popularity": int(t.get("popularity", 0) or 0),
                "explicit": bool(t.get("explicit", False)),
                "duration_ms": int(t.get("duration_ms", 0) or 0),
                "release_date": album.get("release_date", "") or "",
                "album_type": (album.get("album_type") or "").lower(),  # album/single/compilation
                "track_number": int(t.get("track_number", 0) or 0),
                "total_tracks": int((album.get("total_tracks") or 0) or 0),
                "added_at": it.get("added_at", "") or "",
            })
        if results.get("next"):
            results = sp.next(results)
        else:
            break
    items.sort(key=lambda x: x["added_at"], reverse=True)
    if MAX_TRACKS:
        items = items[:MAX_TRACKS]
    return items

# =========================
# Track-only features
# =========================
TITLE_PATTERNS = [
    (r"\bacoustic\b",          "acoustic",   +2.0),
    (r"\binstrumental\b",      "instrument", +2.0),
    (r"\blive\b",              "live",       +1.6),
    (r"\bremix\b",             "remix",      +2.0),
    (r"\bedit\b",              "edit",       +1.2),
    (r"\bmix\b",               "mix",        +1.2),
    (r"\bradio edit\b",        "radio",      +1.2),
    (r"\bextended\b",          "extended",   +1.0),
    (r"\bversion\b",           "version",    +0.8),
    (r"\bdemo\b",              "demo",       +1.0),
    (r"\bunplugged\b",         "acoustic",   +2.0),
    (r"\bfeat\.|\bft\b|\bfeaturing\b", "collab", +1.0),
    (r"\bremaster(ed)?\b",     "remaster",   +0.8),
    (r"\bdub\b",               "dub",        +1.0),
    (r"\bclub\b",              "club",       +1.2),
    (r"\bsad|dark|blue\b",     "mood_neg",   +1.0),
    (r"\bhappy|sun|summer|love\b", "mood_pos", +1.0),
    (r"\bchill|calm|soft\b",   "chill",      +1.0),
    (r"\bnight|midnight\b",    "night",      +0.8),
]

CUE_KEYS = ["acoustic","instrument","live","remix","edit","mix","radio","extended",
            "version","demo","collab","remaster","dub","club","mood_pos","mood_neg","chill","night"]

NUMERIC_NAMES = ["len_norm","popularity_norm","explicit","is_single","pos_in_album"]
RECENCY_NAME = "recency"  # calculated but optionally excluded from clustering

def parse_year(release_date: str) -> int:
    if not release_date:
        return 0
    try:
        return int(release_date[:4])
    except Exception:
        return 0

def build_track_features(tracks):
    """
    Returns:
      texts: list[str]  (track title + album title)
      num:   np.ndarray shape (n, len(NUMERIC_NAMES) [+1 if recency included])
      cues:  np.ndarray shape (n, len(CUE_KEYS))
    """
    now_year = time.gmtime().tm_year
    texts = []
    num_rows = []
    cue_rows = []

    for t in tracks:
        # Text: track + album title
        texts.append(f"{t['name']} {t['album_name']}".lower())

        # Numeric
        dur_sec = t["duration_ms"] / 1000.0
        len_norm = (dur_sec - 120.0) / (420.0 - 120.0)
        len_norm = max(0.0, min(1.0, len_norm))

        popularity_norm = t["popularity"] / 100.0
        explicit = 1.0 if t["explicit"] else 0.0
        is_single = 1.0 if t["album_type"] == "single" else 0.0

        pos_in_album = 0.0
        if t["track_number"] and t["total_tracks"]:
            pos_in_album = (t["track_number"] - 1) / max(1, t["total_tracks"] - 1)

        recency = 0.0
        yr = parse_year(t["release_date"])
        if yr:
            recency = (yr - 1980) / max(1.0, (now_year - 1980))
            recency = max(0.0, min(1.0, recency))

        base = [len_norm, popularity_norm, explicit, is_single, pos_in_album]
        if INCLUDE_RECENCY_IN_CLUSTERING:
            base.append(recency)
        num_rows.append(base)

        # Cues from title/album text
        cue_map = {k: 0.0 for k in CUE_KEYS}
        name = texts[-1]
        for pat, key, w in TITLE_PATTERNS:
            if re.search(pat, name):
                cue_map[key] += w
        cue_rows.append([cue_map[k] for k in CUE_KEYS])

    num = np.array(num_rows, dtype=float) if num_rows else np.zeros((0, len(NUMERIC_NAMES)+(1 if INCLUDE_RECENCY_IN_CLUSTERING else 0)))
    cues = np.array(cue_rows, dtype=float) if cue_rows else np.zeros((0, len(CUE_KEYS)))
    return texts, num, cues

# =========================
# Clustering utilities
# =========================
def choose_k(X):
    n = len(X)
    cands = [k for k in K_CANDIDATES if 2 <= k < n]
    if not cands:
        return 2
    best_k, best_s = cands[0], -1
    for k in cands:
        km = KMeans(n_clusters=k, n_init=10, random_state=RANDOM_STATE)
        labels = km.fit_predict(X)
        if len(set(labels)) < 2:
            continue
        s = silhouette_score(X, labels)
        if s > best_s:
            best_s, best_k = s, k
    return best_k

def top_tokens_for_cluster(tfidf, labels, vocab, lbl, topn=5):
    # mean TF-IDF for this cluster
    mask = (labels == lbl)
    if mask.sum() == 0:
        return []
    sub = tfidf[mask]
    means = np.asarray(sub.mean(axis=0)).ravel()
    idx = means.argsort()[::-1][:topn]
    tokens = [vocab[i] for i in idx if means[i] > 0]
    return tokens

def name_cluster(tokens, cue_center):
    # Simple name generator from tokens + cue tendencies
    cue = {k: v for k, v in zip(CUE_KEYS, cue_center)}
    energetic = cue["remix"] + cue["mix"] + cue["club"] + cue["live"]
    chillness = cue["acoustic"] + cue["instrument"] + cue["chill"]
    darkness = cue["mood_neg"]
    brightness = cue["mood_pos"]

    if cue["instrument"] >= 1.5 and energetic < 1.2:
        base = "Wordless Focus 🎧"
    elif energetic >= 2.5 and brightness >= 0.6:
        base = "Neon Night Drive 🚗💡"
    elif energetic >= 2.2 and darkness >= 0.8:
        base = "Adrenaline & Grit 🔥"
    elif cue["acoustic"] >= 1.5 and chillness >= 2.0:
        base = "Campfire & Cocoa 🔥☕"
    elif chillness >= 2.0 and darkness >= 0.6:
        base = "Blue Nights 🌙"
    elif energetic >= 1.8:
        base = "Easy Motion 🕺"
    elif chillness >= 1.8 and energetic < 1.0:
        base = "Downtempo Drift 🌊"
    else:
        base = "Daylight Grooves 🌼"

    # Add token flavor if unique tokens exist
    extras = [t for t in tokens if len(t) >= 3 and t not in {"remix","mix","edit","version","radio","feat"}]
    if extras:
        extra = " • ".join(extras[:2])
        return f"{base} — {extra}"
    return base

# =========================
# Playlist helpers
# =========================
def ensure_playlist(sp: Spotify, user_id: str, name: str, public: bool):
    results = sp.current_user_playlists(limit=50)
    while True:
        for pl in results.get("items", []):
            if pl.get("name") == name:
                return pl.get("id")
        if results.get("next"):
            results = sp.next(results)
        else:
            break
    created = sp.user_playlist_create(
        user_id, name, public=public, description="Auto-clustered vibe playlist (track text + cues)"
    )
    return created["id"]

def replace_playlist_tracks(sp: Spotify, playlist_id: str, uris):
    uris = list(uris)
    if not uris:
        sp.playlist_replace_items(playlist_id, [])
        return
    sp.playlist_replace_items(playlist_id, uris[:100])
    for i in range(100, len(uris), 100):
        sp.playlist_add_items(playlist_id, uris[i:i+100])

# =========================
# Main
# =========================
def main():
    sp = get_spotify()
    me = sp.current_user()["id"]
    print(f"Hello {me}! ✅ User auth OK")

    print("Fetching Liked Songs…")
    tracks = fetch_all_liked_tracks(sp)
    print(f"Liked Songs: {len(tracks)}")
    if not tracks:
        return

    print("Building features (text + cues + numeric)…")
    texts, num, cues = build_track_features(tracks)

    # TF-IDF on titles (track + album), then SVD to compress
    vect = TfidfVectorizer(
        max_features=TFIDF_MAX_FEATURES,
        ngram_range=(1,2),
        min_df=3,
        stop_words='english',   # keeps domain tokens like 'remix', 'live' generally
        token_pattern=r"(?u)\b[\w']+\b"
    )
    T = vect.fit_transform(texts)           # (n, V)
    vocab = np.array(vect.get_feature_names_out())

    svd = TruncatedSVD(n_components=min(SVD_DIM, max(2, min(T.shape)-1)), random_state=RANDOM_STATE)
    T_svd = svd.fit_transform(T)            # (n, d_svd)

    # scale numeric + cues; concatenate with SVD features
    scaler_num = StandardScaler()
    scaler_cue = StandardScaler(with_mean=True, with_std=True)

    num_s = scaler_num.fit_transform(num) if num.size else np.zeros((len(tracks), 0))
    cues_s = scaler_cue.fit_transform(cues) if cues.size else np.zeros((len(tracks), 0))

    X = np.hstack([T_svd, num_s, cues_s])   # dense (n, d)

    # pick k
    k = choose_k(X) if AUTO_K else FIXED_K
    k = max(2, min(k, len(tracks)-1))
    print(f"Clustering into k={k} vibes…")
    km = KMeans(n_clusters=k, n_init=10, random_state=RANDOM_STATE)
    labels = km.fit_predict(X)

    # name clusters from top tokens + cue centers
    groups = defaultdict(list)
    for lbl, t in zip(labels, tracks):
        groups[lbl].append(t)

    cue_centers = {}
    for lbl in range(k):
        cue_centers[lbl] = cues[labels == lbl].mean(axis=0) if (labels == lbl).any() else np.zeros(cues.shape[1])

    # For ordering tracks by cohesiveness: sort by distance to centroid
    dists = km.transform(X)  # (n, k)
    order_idx = np.argsort(dists[np.arange(len(tracks)), labels])  # global order by closeness
    # but we need per-cluster sorting:
    per_cluster_sorted = {lbl: [] for lbl in range(k)}
    for idx in order_idx:
        per_cluster_sorted[labels[idx]].append(idx)

    # Generate distinct names
    used_names = set()
    label_to_name = {}
    for lbl in range(k):
        tokens = top_tokens_for_cluster(T, labels, vocab, lbl, topn=6)
        base = name_cluster(tokens, cue_centers[lbl])
        name = base
        suffix = 2
        while name in used_names:
            name = f"{base} #{suffix}"; suffix += 1
        used_names.add(name)
        label_to_name[lbl] = name

    # Create/update playlists with cohesive ordering
    for lbl in range(k):
        idxs = per_cluster_sorted[lbl]
        if not idxs:
            continue
        vibe_name = label_to_name[lbl]
        playlist_name = f"{PLAYLIST_PREFIX}{vibe_name}"
        uris = [tracks[i]["uri"] for i in idxs][:MAX_TRACKS_PER_PLAYLIST]
        print(f"Updating: {playlist_name}  ({len(uris)} tracks)")
        pl_id = ensure_playlist(sp, me, playlist_name, PUBLIC_PLAYLISTS)
        replace_playlist_tracks(sp, pl_id, uris)

    print("Done. Enjoy your (actually cohesive) vibes! ✨")

if __name__ == "__main__":
    main()
