const API_BASE = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/+$/, "");
const SESSION_STORAGE_KEY = "spotify_session_token";
export type TimeRange = "short_term" | "medium_term" | "long_term";

export function getApiBaseUrl(): string {
  return API_BASE;
}

export function getSessionToken(): string {
  return localStorage.getItem(SESSION_STORAGE_KEY) || "";
}

export function setSessionToken(token: string): void {
  if (token) {
    localStorage.setItem(SESSION_STORAGE_KEY, token);
  }
}

export function clearSessionToken(): void {
  localStorage.removeItem(SESSION_STORAGE_KEY);
}

export async function logoutSession(): Promise<void> {
  const token = getSessionToken();
  if (!token) return;
  if (!API_BASE) throw new Error("Backend URL is not configured; the server session could not be revoked.");

  const resp = await fetch(`${API_BASE}/logout`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(detail || `Server logout failed: ${resp.status}`);
  }
}

export function getSpotifyLoginUrl(): string {
  if (!API_BASE) return "";
  const returnTo = `${window.location.origin}${window.location.pathname}`;
  return `${API_BASE}/auth/login?return_to=${encodeURIComponent(returnTo)}`;
}

export async function fetchMe(): Promise<{ spotify_user_id: string; display_name: string }> {
  const token = getSessionToken();
  const resp = await fetch(`${API_BASE}/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) {
    throw new Error(`Failed to fetch profile: ${resp.status}`);
  }
  return resp.json();
}

export async function runScript(
  script: "vaulted" | "liked",
  payload?: { target_playlist_id?: string; target_playlist_name?: string },
  dryRun = false,
): Promise<unknown> {
  const token = getSessionToken();
  const path = script === "vaulted" ? "/run/vaulted" : "/run/liked";
  const resp = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ...(payload || {}), dry_run: dryRun }),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text || `Run failed: ${resp.status}`);
  }
  return resp.json();
}

export async function fetchAutomationTargets(): Promise<{
  ok: boolean;
  targets: {
    playlists: Array<{ id: string; name: string; description: string }>;
    liked: {
      tag: string;
      name_fallback: string;
      matched_by: "tag" | "name" | "none";
      default_playlist_id: string | null;
      default_playlist_name: string | null;
    };
    vaulted: {
      tag: string;
      name_fallback: string;
      matched_by: "tag" | "name" | "none";
      default_playlist_id: string | null;
      default_playlist_name: string | null;
    };
  };
}> {
  const token = getSessionToken();
  const resp = await fetch(`${API_BASE}/automation/targets`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (resp.status === 404) {
    // Compatibility fallback for older backend deploys.
    return {
      ok: true,
      targets: {
        playlists: [],
        liked: {
          tag: "[spotipy:liked_mirror]",
          name_fallback: "Liked Songs Mirror",
          matched_by: "none",
          default_playlist_id: null,
          default_playlist_name: null,
        },
        vaulted: {
          tag: "[spotipy:vaulted_add]",
          name_fallback: "_vaulted",
          matched_by: "none",
          default_playlist_id: null,
          default_playlist_name: null,
        },
      },
    };
  }
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text || `Targets fetch failed: ${resp.status}`);
  }
  return resp.json();
}

export async function fetchOverviewStats(timeRange: TimeRange): Promise<{
  ok: boolean;
  overview: {
    time_range: TimeRange;
    counts: {
      playlists_total: number;
      playlists_owned: number;
      saved_tracks_total: number;
      added_7d: number;
      added_30d: number;
    };
    top_artists: Array<{
      id: string;
      name: string;
      genres: string[];
      popularity: number;
      image_url: string | null;
    }>;
    top_tracks: Array<{
      id: string;
      name: string;
      artists: string[];
      popularity: number;
      image_url: string | null;
    }>;
  };
}> {
  const token = getSessionToken();
  const resp = await fetch(`${API_BASE}/stats/overview?time_range=${encodeURIComponent(timeRange)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) {
    const text = await resp.text();
    if (resp.status === 404) {
      throw new Error("Backend endpoint /stats/overview not found. Redeploy Render backend from latest main.");
    }
    throw new Error(text || `Stats fetch failed: ${resp.status}`);
  }
  return resp.json();
}

export async function fetchTopStats(timeRange: TimeRange): Promise<{
  ok: boolean;
  data: {
    time_range: TimeRange;
    top_artists: Array<{
      id: string;
      name: string;
      genres: string[];
      popularity: number;
      image_url: string | null;
    }>;
    top_tracks: Array<{
      id: string;
      name: string;
      artists: string[];
      popularity: number;
      image_url: string | null;
    }>;
  };
}> {
  const token = getSessionToken();
  const resp = await fetch(`${API_BASE}/stats/top?time_range=${encodeURIComponent(timeRange)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) {
    const text = await resp.text();
    if (resp.status === 404) {
      throw new Error("Backend endpoint /stats/top not found. Redeploy Render backend from latest main.");
    }
    throw new Error(text || `Top stats fetch failed: ${resp.status}`);
  }
  return resp.json();
}

export async function fetchTrackLongevity(): Promise<{
  ok: boolean;
  data: {
    tracks: Array<{
      id: string;
      name: string;
      artists: string[];
      image_url: string | null;
      popularity: number;
      overlap_count: number;
      present_in: Array<"short_term" | "medium_term" | "long_term">;
      ranks: Partial<Record<"short_term" | "medium_term" | "long_term", number>>;
      longevity_score: number;
    }>;
    scoring: {
      base_per_range: number;
      rank_formula: string;
      weights: Record<string, number>;
    };
  };
}> {
  const token = getSessionToken();
  const resp = await fetch(`${API_BASE}/stats/track-longevity`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error(`Track longevity fetch failed: ${resp.status}`);
  return resp.json();
}

export async function fetchGenrePlaylistRecommendations(timeRange: TimeRange): Promise<{
  ok: boolean;
  data: {
    time_range: TimeRange;
    genres: string[];
    recommendations: Array<{
      genre: string;
      playlists: Array<{
        id: string;
        name: string;
        description: string;
        owner_name: string;
        url: string;
        open_url: string;
        image_url: string | null;
      }>;
    }>;
  };
}> {
  const token = getSessionToken();
  const resp = await fetch(`${API_BASE}/recommendations/genre-playlists?time_range=${encodeURIComponent(timeRange)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) {
    const text = await resp.text();
    if (resp.status === 404) {
      throw new Error("Backend endpoint /recommendations/genre-playlists not found. Redeploy Render backend from latest main.");
    }
    throw new Error(text || `Genre recommendations fetch failed: ${resp.status}`);
  }
  return resp.json();
}

export async function fetchRecentlyPlayed(): Promise<{
  ok: boolean;
  data: {
    tracks: Array<{
      id: string;
      name: string;
      artists: string[];
      image_url: string | null;
      played_at: string;
    }>;
  };
}> {
  const token = getSessionToken();
  const resp = await fetch(`${API_BASE}/stats/recently-played`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error(`Recently played fetch failed: ${resp.status}`);
  return resp.json();
}

export async function fetchListeningPattern(): Promise<{
  ok: boolean;
  data: {
    source: "recently_played" | "saved_tracks_added_at";
    note: string | null;
    timezone: string;
    total_events: number;
    max_cell: number;
    has_enough_data: boolean;
    day_labels: string[];
    hours: number[];
    grid: number[][];
  };
}> {
  const token = getSessionToken();
  const resp = await fetch(`${API_BASE}/stats/listening-pattern`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error(`Listening pattern fetch failed: ${resp.status}`);
  return resp.json();
}

export async function searchArtists(q: string): Promise<{
  ok: boolean;
  data: {
    artists: Array<{
      id: string;
      name: string;
      genres: string[];
      popularity: number;
      image_url: string | null;
    }>;
  };
}> {
  const token = getSessionToken();
  const resp = await fetch(`${API_BASE}/search/artists?q=${encodeURIComponent(q)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error(`Artist search failed: ${resp.status}`);
  return resp.json();
}

export async function fetchArtistCatalog(artistId: string): Promise<{
  ok: boolean;
  data: {
    artist_id: string;
    artist_name: string;
    total_albums: number;
    saved_albums: number;
    total_tracks: number;
    saved_tracks_est: number;
    pct: number;
    albums: Array<{
      id: string;
      name: string;
      year: string;
      total_tracks: number;
      image_url: string | null;
      saved: boolean;
    }>;
  };
}> {
  const token = getSessionToken();
  const resp = await fetch(`${API_BASE}/stats/artist-catalog?artist_id=${encodeURIComponent(artistId)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error(`Artist catalog fetch failed: ${resp.status}`);
  return resp.json();
}

export async function fetchGenreBreakdown(): Promise<{
  ok: boolean;
  data: {
    genres: Array<{ genre: string; count: number; pct: number }>;
    total_artists: number;
    songs_scanned: number;
  };
}> {
  const token = getSessionToken();
  const resp = await fetch(`${API_BASE}/stats/genre-breakdown`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error(`Genre breakdown fetch failed: ${resp.status}`);
  return resp.json();
}

export async function fetchMoodTimeline(): Promise<{
  ok: boolean;
  data: {
    mode: "audio_features" | "proxy";
    timeline: Array<{
      time_range: TimeRange;
      energy: number | null;
      valence: number | null;
      danceability: number | null;
      acousticness: number | null;
    }>;
    proxy_timeline: Array<{
      time_range: TimeRange;
      popularity: number | null;
      explicitness: number | null;
      freshness: number | null;
      length: number | null;
    }>;
    error: string | null;
  };
}> {
  const token = getSessionToken();
  const resp = await fetch(`${API_BASE}/stats/mood-timeline`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error(`Mood timeline fetch failed: ${resp.status}`);
  return resp.json();
}

export async function fetchPlaylistFreshness(): Promise<{
  ok: boolean;
  data: {
    playlists: Array<{
      id: string;
      name: string;
      description: string;
      track_count: number;
      last_added_at: string | null;
      days_since_activity: number;
      freshness_score: number;
      image_url: string | null;
      spotify_url: string;
      is_vaulted_tagged: boolean;
      is_liked_tagged: boolean;
    }>;
    scoring: {
      method: string;
      description: string;
    };
  };
}> {
  const token = getSessionToken();
  const resp = await fetch(`${API_BASE}/stats/playlist-freshness`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error(`Playlist freshness fetch failed: ${resp.status}`);
  return resp.json();
}

export async function runArchiveStale(
  maxFreshnessScore: number,
  prefix = "[Archive]",
): Promise<{
  ok: boolean;
  script: string;
  result: {
    threshold: number;
    prefix: string;
    archived_count: number;
    archived: Array<{
      id: string;
      old_name: string;
      new_name: string;
      freshness_score: number;
    }>;
  };
}> {
  const token = getSessionToken();
  const resp = await fetch(`${API_BASE}/run/archive-stale`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ max_freshness_score: maxFreshnessScore, prefix }),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text || `Archive stale run failed: ${resp.status}`);
  }
  return resp.json();
}

export function captureSessionTokenFromUrl(): boolean {
  const url = new URL(window.location.href);
  let token = url.searchParams.get("session_token");
  if (!token && url.hash.includes("?")) {
    const hashQuery = url.hash.slice(url.hash.indexOf("?") + 1);
    token = new URLSearchParams(hashQuery).get("session_token");
  }
  if (!token) return false;
  setSessionToken(token);
  url.searchParams.delete("session_token");
  url.searchParams.delete("spotify_user_id");
  url.searchParams.delete("login_error");
  // Replace the token-bearing history entry directly; assigning location.hash
  // would leave the sensitive callback URL in the back stack.
  url.hash = "#/dashboard";
  window.history.replaceState({}, "", url.toString());
  window.dispatchEvent(new PopStateEvent("popstate", { state: window.history.state }));
  return true;
}
