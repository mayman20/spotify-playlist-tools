import { useEffect, useMemo, useRef, useState } from "react";
import { ScriptCard } from "@/components/ScriptCard";
import { scripts } from "@/lib/mock-data";
import { useCurrentUser } from "@/hooks/use-current-user";
import {
  fetchGenrePlaylistRecommendations,
  fetchOverviewStats,
  fetchTopStats,
  fetchTrackLongevity,
  fetchRecentlyPlayed,
  fetchListeningPattern,
  searchArtists,
  fetchArtistCatalog,
  fetchGenreBreakdown,
  TimeRange,
} from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Disc3, ExternalLink, Heart, ListMusic, Plus, Search, Music2, Clock } from "lucide-react";
import {
  PieChart,
  Pie,
  Cell,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";

// ── Types ──────────────────────────────────────────────────────────────────

type Overview = {
  time_range: TimeRange;
  counts: {
    playlists_total: number;
    playlists_owned: number;
    saved_tracks_total: number;
    added_7d: number;
    added_30d: number;
  };
};

type TopStats = {
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

type LongevityTrack = {
  id: string;
  name: string;
  artists: string[];
  image_url: string | null;
  popularity: number;
  overlap_count: number;
  present_in: Array<"short_term" | "medium_term" | "long_term">;
  ranks: Partial<Record<"short_term" | "medium_term" | "long_term", number>>;
  longevity_score: number;
};

type GenrePlaylistRecommendations = {
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

type RecentTrack = {
  id: string;
  name: string;
  artists: string[];
  image_url: string | null;
  played_at: string;
};

type ListeningPattern = {
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

type ArtistResult = {
  id: string;
  name: string;
  genres: string[];
  popularity: number;
  image_url: string | null;
};

type CatalogAlbum = {
  id: string;
  name: string;
  year: string;
  total_tracks: number;
  image_url: string | null;
  saved: boolean;
};

type CatalogData = {
  artist_id: string;
  artist_name: string;
  total_albums: number;
  saved_albums: number;
  total_tracks: number;
  saved_tracks_est: number;
  pct: number;
  source?: "vaulted_playlist" | "liked_songs";
  source_playlist_name?: string | null;
  albums: CatalogAlbum[];
};

type GenreBreakdown = {
  genres: Array<{ genre: string; count: number; pct: number }>;
  total_artists: number;
  songs_scanned: number;
  source?: "vaulted_playlist" | "liked_songs";
  source_playlist_name?: string | null;
};

// ── Constants ──────────────────────────────────────────────────────────────

const ranges: Array<{ value: TimeRange; label: string }> = [
  { value: "short_term", label: "4 Weeks" },
  { value: "medium_term", label: "6 Months" },
  { value: "long_term", label: "1 Year" },
];

const PIE_COLORS = [
  "#6366f1", "#ec4899", "#f59e0b", "#10b981", "#3b82f6",
  "#f97316", "#8b5cf6", "#14b8a6", "#ef4444", "#84cc16",
];

const LIST_ROW_CLASS = "rounded-md border border-zinc-800/70 bg-zinc-950/25 px-2.5 py-2";
const GENRE_GROUP_CLASS = "space-y-2 rounded-lg border border-zinc-700/70 bg-zinc-950/35 p-3";
const RANGE_BADGE_LABELS: Record<"short_term" | "medium_term" | "long_term", string> = {
  short_term: "4W",
  medium_term: "6M",
  long_term: "1Y",
};

function formatHourLabel(hour: number): string {
  const normalized = ((hour % 24) + 24) % 24;
  const suffix = normalized >= 12 ? "PM" : "AM";
  const base = normalized % 12 || 12;
  return `${base}${suffix}`;
}

// ── Component ──────────────────────────────────────────────────────────────

export default function Dashboard() {
  const { user } = useCurrentUser();
  const [timeRange, setTimeRange] = useState<TimeRange>("short_term");

  // Core stats
  const [overview, setOverview] = useState<Overview | null>(null);
  const [topStats, setTopStats] = useState<TopStats | null>(null);
  const [genreRecs, setGenreRecs] = useState<GenrePlaylistRecommendations | null>(null);
  const [loadingCounts, setLoadingCounts] = useState(false);
  const [loadingTop, setLoadingTop] = useState(false);
  const [loadingGenre, setLoadingGenre] = useState(false);
  const [error, setError] = useState("");
  const [showAllArtists, setShowAllArtists] = useState(false);
  const [showAllTracks, setShowAllTracks] = useState(false);
  const [longevityTracks, setLongevityTracks] = useState<LongevityTrack[]>([]);
  const [loadingLongevity, setLoadingLongevity] = useState(false);
  const [showAllLongevity, setShowAllLongevity] = useState(false);

  // Recently played
  const [recentTracks, setRecentTracks] = useState<RecentTrack[]>([]);
  const [loadingRecent, setLoadingRecent] = useState(false);
  const [recentError, setRecentError] = useState("");
  const [showAllRecent, setShowAllRecent] = useState(false);
  const [listeningPattern, setListeningPattern] = useState<ListeningPattern | null>(null);
  const [loadingPattern, setLoadingPattern] = useState(false);

  // Artist catalog search
  const [artistQuery, setArtistQuery] = useState("");
  const [artistResults, setArtistResults] = useState<ArtistResult[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [selectedArtist, setSelectedArtist] = useState<ArtistResult | null>(null);
  const [catalogData, setCatalogData] = useState<CatalogData | null>(null);
  const [loadingCatalog, setLoadingCatalog] = useState(false);
  const [loadingSearch, setLoadingSearch] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);

  // Genre breakdown
  const [genreBreakdown, setGenreBreakdown] = useState<GenreBreakdown | null>(null);
  const [loadingGenreBreakdown, setLoadingGenreBreakdown] = useState(false);

  // ── Effects ──────────────────────────────────────────────────────────────

  // Time-range-dependent data
  useEffect(() => {
    if (!user) {
      setOverview(null);
      setTopStats(null);
      setGenreRecs(null);
      return;
    }
    setShowAllArtists(false);
    setShowAllTracks(false);
    setShowAllLongevity(false);
    setError("");

    setLoadingCounts(true);
    fetchOverviewStats(timeRange)
      .then((resp) => setOverview(resp.overview))
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Failed to load counts.";
        setError(msg);
      })
      .finally(() => setLoadingCounts(false));

    setLoadingTop(true);
    fetchTopStats(timeRange)
      .then((resp) => setTopStats(resp.data))
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Failed to load top artists and tracks.";
        setError((prev) => (prev ? `${prev} ${msg}` : msg));
      })
      .finally(() => setLoadingTop(false));

    setLoadingLongevity(true);
    fetchTrackLongevity()
      .then((resp) => setLongevityTracks(resp.data.tracks || []))
      .catch(() => setLongevityTracks([]))
      .finally(() => setLoadingLongevity(false));

    setLoadingGenre(true);
    fetchGenrePlaylistRecommendations(timeRange)
      .then((resp) => setGenreRecs(resp.data))
      .catch(() => setGenreRecs(null))
      .finally(() => setLoadingGenre(false));
  }, [timeRange, user]);

  // One-time loads (not time-range-dependent)
  useEffect(() => {
    if (!user) return;

    setLoadingRecent(true);
    setRecentError("");
    fetchRecentlyPlayed()
      .then((resp) => setRecentTracks(resp.data.tracks))
      .catch((err: unknown) => {
        setRecentTracks([]);
        const msg = err instanceof Error ? err.message : "Failed to load recently played tracks.";
        setRecentError(msg);
      })
      .finally(() => setLoadingRecent(false));

    setLoadingPattern(true);
    fetchListeningPattern()
      .then((resp) => setListeningPattern(resp.data))
      .catch(() => setListeningPattern(null))
      .finally(() => setLoadingPattern(false));

    setLoadingGenreBreakdown(true);
    fetchGenreBreakdown()
      .then((resp) => setGenreBreakdown(resp.data))
      .catch(() => setGenreBreakdown(null))
      .finally(() => setLoadingGenreBreakdown(false));

  }, [user]);

  // Debounced artist search
  useEffect(() => {
    if (!artistQuery.trim()) {
      setArtistResults([]);
      setShowDropdown(false);
      return;
    }
    const timer = setTimeout(() => {
      setLoadingSearch(true);
      searchArtists(artistQuery)
        .then((resp) => {
          setArtistResults(resp.data.artists);
          setShowDropdown(true);
        })
        .catch(() => setArtistResults([]))
        .finally(() => setLoadingSearch(false));
    }, 300);
    return () => clearTimeout(timer);
  }, [artistQuery]);

  // Click-outside to close dropdown
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  // ── Derived data ──────────────────────────────────────────────────────────

  const visibleScripts = useMemo(() => scripts, []);
  const visibleArtists = showAllArtists
    ? (topStats?.top_artists || []).slice(0, 25)
    : (topStats?.top_artists || []).slice(0, 5);
  const visibleTracks = showAllTracks
    ? (topStats?.top_tracks || []).slice(0, 25)
    : (topStats?.top_tracks || []).slice(0, 5);
  const visibleLongevity = showAllLongevity ? longevityTracks.slice(0, 25) : longevityTracks.slice(0, 5);
  const visibleRecent = showAllRecent ? recentTracks.slice(0, 25) : recentTracks.slice(0, 5);
  const listeningDayTotals = listeningPattern
    ? listeningPattern.day_labels.map((day, idx) => ({
        day,
        total: (listeningPattern.grid[idx] || []).reduce((acc, n) => acc + n, 0),
      }))
    : [];
  const maxDayTotal = Math.max(...listeningDayTotals.map((d) => d.total), 1);

  // ── Handlers ─────────────────────────────────────────────────────────────

  function selectArtist(artist: ArtistResult) {
    setSelectedArtist(artist);
    setArtistQuery(artist.name);
    setShowDropdown(false);
    setLoadingCatalog(true);
    setCatalogData(null);
    fetchArtistCatalog(artist.id)
      .then((resp) => setCatalogData(resp.data))
      .catch(() => setCatalogData(null))
      .finally(() => setLoadingCatalog(false));
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-8 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold">
            {user ? `Welcome back, ${user.display_name}` : "Welcome"}
          </h1>
          <p className="text-muted-foreground">
            {user
              ? "Quick stats from your Spotify account."
              : "Connect your Spotify account in Settings to load dashboard stats."}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {ranges.map((r) => (
            <Button
              key={r.value}
              size="sm"
              variant={timeRange === r.value ? "default" : "outline"}
              onClick={() => setTimeRange(r.value)}
            >
              {r.label}
            </Button>
          ))}
        </div>
      </div>

      {user ? (
        <>
          {/* ── Stat Cards ─────────────────────────────────────────────── */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-muted-foreground">Saved Tracks</CardTitle>
              </CardHeader>
              <CardContent className="flex items-center gap-3">
                <Heart className="h-5 w-5 text-primary" />
                <span className="text-xl sm:text-2xl font-bold">
                  {loadingCounts ? "..." : overview?.counts.saved_tracks_total ?? "—"}
                </span>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-muted-foreground">Playlists (Owned)</CardTitle>
              </CardHeader>
              <CardContent className="flex items-center gap-3">
                <ListMusic className="h-5 w-5 text-primary" />
                <span className="text-xl sm:text-2xl font-bold">
                  {loadingCounts ? "..." : overview?.counts.playlists_owned ?? "—"}
                </span>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-muted-foreground">Added Last 7 Days</CardTitle>
              </CardHeader>
              <CardContent className="flex items-center gap-3">
                <Plus className="h-5 w-5 text-primary" />
                <span className="text-xl sm:text-2xl font-bold">
                  {loadingCounts ? "..." : overview?.counts.added_7d ?? "—"}
                </span>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-muted-foreground">Added Last 30 Days</CardTitle>
              </CardHeader>
              <CardContent className="flex items-center gap-3">
                <Disc3 className="h-5 w-5 text-primary" />
                <span className="text-xl sm:text-2xl font-bold">
                  {loadingCounts ? "..." : overview?.counts.added_30d ?? "—"}
                </span>
              </CardContent>
            </Card>
          </div>

          {/* ── Top Artists / Tracks / Longevity ───────────────────────── */}
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Top Artists</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {loadingTop ? <p className="text-sm text-muted-foreground">Loading...</p> : null}
                {!loadingTop && visibleArtists.map((artist, idx) => (
                  <div
                    key={artist.id || artist.name}
                    className={`flex items-center gap-3 ${LIST_ROW_CLASS}`}
                  >
                    <span className="w-5 text-xs text-muted-foreground">{idx + 1}</span>
                    {artist.image_url ? (
                      <img src={artist.image_url} alt={artist.name} className="h-8 w-8 rounded object-cover" />
                    ) : (
                      <div className="h-8 w-8 rounded bg-muted" />
                    )}
                    <div className="min-w-0">
                      <p className="text-sm font-medium truncate">{artist.name}</p>
                      <p className="text-xs text-muted-foreground truncate">
                        {artist.genres?.slice(0, 2).join(", ") || "No genres"}
                      </p>
                    </div>
                  </div>
                ))}
                {!loadingTop && (topStats?.top_artists?.length || 0) > 5 ? (
                  <Button variant="outline" size="sm" onClick={() => setShowAllArtists((v) => !v)}>
                    {showAllArtists ? "Show Less" : "Show More (25)"}
                  </Button>
                ) : null}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">Top Tracks</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {loadingTop ? <p className="text-sm text-muted-foreground">Loading...</p> : null}
                {!loadingTop && visibleTracks.map((track, idx) => (
                  <div
                    key={track.id || track.name}
                    className={`flex items-center gap-3 ${LIST_ROW_CLASS}`}
                  >
                    <span className="w-5 text-xs text-muted-foreground">{idx + 1}</span>
                    {track.image_url ? (
                      <img src={track.image_url} alt={track.name} className="h-8 w-8 rounded object-cover" />
                    ) : (
                      <div className="h-8 w-8 rounded bg-muted" />
                    )}
                    <div className="min-w-0">
                      <p className="text-sm font-medium truncate">{track.name}</p>
                      <p className="text-xs text-muted-foreground truncate">{track.artists?.join(", ")}</p>
                    </div>
                  </div>
                ))}
                {!loadingTop && (topStats?.top_tracks?.length || 0) > 5 ? (
                  <Button variant="outline" size="sm" onClick={() => setShowAllTracks((v) => !v)}>
                    {showAllTracks ? "Show Less" : "Show More (25)"}
                  </Button>
                ) : null}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Track Longevity Score</CardTitle>
                <p className="text-xs text-muted-foreground">
                  Tracks that persist across 4-week, 6-month, and 1-year top lists.
                </p>
              </CardHeader>
              <CardContent className="space-y-3">
                {loadingLongevity ? <p className="text-sm text-muted-foreground">Loading longevity scores...</p> : null}
                {!loadingLongevity && visibleLongevity.map((track, idx) => (
                  <div key={track.id || `${track.name}-${idx}`} className={`flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 ${LIST_ROW_CLASS}`}>
                    <span className="w-5 text-xs text-muted-foreground">{idx + 1}</span>
                    {track.image_url ? (
                      <img src={track.image_url} alt={track.name} className="h-8 w-8 rounded object-cover" />
                    ) : (
                      <div className="h-8 w-8 rounded bg-muted" />
                    )}
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium truncate">{track.name}</p>
                      <p className="text-xs text-muted-foreground truncate">{track.artists?.join(", ")}</p>
                    </div>
                    <div className="flex flex-col sm:items-end gap-1 flex-shrink-0">
                      <p className="text-sm font-semibold text-primary">{track.longevity_score}</p>
                      <div className="flex items-center gap-1 flex-wrap">
                        {(["short_term", "medium_term", "long_term"] as const).map((r) => (
                          <span
                            key={`${track.id}-${r}`}
                            className={`text-[10px] px-1.5 py-0.5 rounded border ${
                              track.present_in.includes(r)
                                ? "border-primary/60 text-primary bg-primary/10"
                                : "border-zinc-700 text-zinc-500"
                            }`}
                          >
                            {RANGE_BADGE_LABELS[r]}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                ))}
                {!loadingLongevity && longevityTracks.length > 5 ? (
                  <Button variant="outline" size="sm" onClick={() => setShowAllLongevity((v) => !v)}>
                    {showAllLongevity ? "Show Less" : "Show More (25)"}
                  </Button>
                ) : null}
                {!loadingLongevity && longevityTracks.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No longevity track data available yet.</p>
                ) : null}
              </CardContent>
            </Card>
          </div>

          {/* ── Genre Breakdown + Listening Pattern Explorer ────────────────────────── */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Genre Breakdown Pie */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Genre Breakdown</CardTitle>
                <p className="text-xs text-muted-foreground">
                  From your library source
                  {genreBreakdown ? ` · ${genreBreakdown.songs_scanned.toLocaleString()} songs scanned` : ""}
                  {genreBreakdown?.source === "vaulted_playlist"
                    ? ` · Source: ${genreBreakdown.source_playlist_name || "Vaulted playlist"}`
                    : genreBreakdown?.source === "liked_songs"
                      ? " · Source: Liked songs"
                      : ""}
                </p>
              </CardHeader>
              <CardContent>
                {loadingGenreBreakdown ? (
                  <p className="text-sm text-muted-foreground">Scanning liked songs...</p>
                ) : genreBreakdown?.genres?.length ? (
                  <ResponsiveContainer width="100%" height={260}>
                    <PieChart>
                      <Pie
                        data={genreBreakdown.genres}
                        dataKey="count"
                        nameKey="genre"
                        cx="50%"
                        cy="50%"
                        outerRadius={90}
                        labelLine={false}
                      >
                        {genreBreakdown.genres.map((_, i) => (
                          <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                        ))}
                      </Pie>
                      <Tooltip
                        formatter={(val: number, name: string) => [
                          `${val} artists`,
                          name,
                        ]}
                      />
                      <Legend
                        formatter={(value) => (
                          <span className="text-xs capitalize">{value}</span>
                        )}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                ) : (
                  <p className="text-sm text-muted-foreground">No genre data available.</p>
                )}
              </CardContent>
            </Card>

            {/* Listening Pattern Explorer */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Listening Pattern Explorer</CardTitle>
                <p className="text-xs text-muted-foreground">
                  Heatmap of play events by weekday/hour ({listeningPattern?.timezone || "UTC"}).
                </p>
              </CardHeader>
              <CardContent>
                {loadingPattern ? (
                  <p className="text-sm text-muted-foreground">Loading listening pattern...</p>
                ) : listeningPattern ? (
                  <>
                    {listeningPattern.note ? (
                      <p className="text-xs text-muted-foreground">{listeningPattern.note}</p>
                    ) : null}
                    {!listeningPattern.has_enough_data ? (
                      <p className="text-xs text-muted-foreground">
                        Limited recent history ({listeningPattern.total_events} events). Heatmap may be sparse.
                      </p>
                    ) : null}
                    <div className="md:hidden space-y-2">
                      {listeningDayTotals.map((d) => (
                        <div key={d.day} className="flex items-center gap-2">
                          <span className="w-8 text-xs text-muted-foreground">{d.day}</span>
                          <div className="flex-1 h-2 rounded bg-zinc-900/70 overflow-hidden">
                            <div
                              className="h-full bg-green-500/70"
                              style={{ width: `${Math.round((d.total / maxDayTotal) * 100)}%` }}
                            />
                          </div>
                          <span className="w-7 text-right text-xs text-muted-foreground">{d.total}</span>
                        </div>
                      ))}
                      <p className="text-[11px] text-muted-foreground">
                        Mobile summary view shown. Expand on desktop for hour-by-hour heatmap.
                      </p>
                    </div>
                    <div className="hidden md:block overflow-x-auto">
                      <div className="min-w-[760px]">
                        <div className="grid grid-cols-[70px_repeat(24,minmax(20px,1fr))] gap-1 items-center text-[10px] text-muted-foreground mb-1">
                          <div />
                          {Array.from({ length: 24 }).map((_, hour) => (
                            <div key={`h-${hour}`} className="text-center">
                              {hour % 3 === 0 ? formatHourLabel(hour) : ""}
                            </div>
                          ))}
                        </div>

                        {listeningPattern.day_labels.map((day, dayIdx) => (
                          <div key={day} className="grid grid-cols-[70px_repeat(24,minmax(20px,1fr))] gap-1 items-center mb-1">
                            <div className="text-xs text-muted-foreground pr-2">{day}</div>
                            {(listeningPattern.grid[dayIdx] || []).map((count, hourIdx) => {
                              const max = Math.max(listeningPattern.max_cell, 1);
                              const alpha = count > 0 ? 0.15 + (count / max) * 0.75 : 0.05;
                              return (
                                <div
                                  key={`${day}-${hourIdx}`}
                                  title={`${day} ${formatHourLabel(hourIdx)} - ${count} plays`}
                                  className="h-4 rounded-sm border border-zinc-800"
                                  style={{ backgroundColor: `rgba(34, 197, 94, ${alpha.toFixed(3)})` }}
                                />
                              );
                            })}
                          </div>
                        ))}
                      </div>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Source: {listeningPattern.source === "recently_played" ? "Recently played" : "Liked track add-times"} ·
                      Total events analyzed: {listeningPattern.total_events}. Each cell = number of plays in that day/hour slot.
                    </p>
                  </>
                ) : (
                  <p className="text-sm text-muted-foreground">Listening pattern unavailable right now.</p>
                )}
              </CardContent>
            </Card>
          </div>

          {/* ── Artist Catalog Depth ────────────────────────────────────── */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Artist Catalog Depth</CardTitle>
              <p className="text-xs text-muted-foreground">
                See how much of an artist's studio discography you've saved
              </p>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Search */}
              <div ref={searchRef} className="relative max-w-sm">
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
                  <Input
                    placeholder="Search for an artist..."
                    value={artistQuery}
                    onChange={(e) => {
                      setArtistQuery(e.target.value);
                      if (!e.target.value) {
                        setSelectedArtist(null);
                        setCatalogData(null);
                      }
                    }}
                    onFocus={() => artistResults.length > 0 && setShowDropdown(true)}
                    className="pl-9"
                  />
                  {loadingSearch && (
                    <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">
                      ...
                    </span>
                  )}
                </div>

                {/* Dropdown */}
                {showDropdown && artistResults.length > 0 && (
                  <div className="absolute z-50 top-full left-0 right-0 mt-1 rounded-md border border-border bg-popover shadow-lg overflow-hidden">
                    {artistResults.map((artist) => (
                      <button
                        key={artist.id}
                        className="w-full flex items-center gap-3 px-3 py-2 hover:bg-muted/60 text-left transition-colors"
                        onMouseDown={() => selectArtist(artist)}
                      >
                        {artist.image_url ? (
                          <img src={artist.image_url} alt={artist.name} className="h-8 w-8 rounded-full object-cover" />
                        ) : (
                          <div className="h-8 w-8 rounded-full bg-muted flex items-center justify-center">
                            <Music2 className="h-4 w-4 text-muted-foreground" />
                          </div>
                        )}
                        <div className="min-w-0">
                          <p className="text-sm font-medium truncate">{artist.name}</p>
                          <p className="text-xs text-muted-foreground truncate">
                            {artist.genres?.slice(0, 2).join(", ") || "No genres"}
                          </p>
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {/* Catalog Results */}
              {loadingCatalog && (
                <p className="text-sm text-muted-foreground">Loading catalog...</p>
              )}
              {!loadingCatalog && catalogData && (
                <div className="space-y-4">
                  <div className="flex items-center gap-4 flex-wrap">
                    <div>
                      <p className="text-2xl font-bold">{catalogData.pct}%</p>
                      <p className="text-xs text-muted-foreground">of studio albums saved</p>
                    </div>
                    <div className="text-sm text-muted-foreground space-y-0.5">
                      <p>{catalogData.saved_albums} / {catalogData.total_albums} albums</p>
                      <p>~{catalogData.saved_tracks_est.toLocaleString()} / {catalogData.total_tracks.toLocaleString()} tracks</p>
                      <p>
                        Source: {catalogData.source === "vaulted_playlist"
                          ? catalogData.source_playlist_name || "Vaulted playlist"
                          : "Liked songs"}
                      </p>
                    </div>
                  </div>
                  <Progress value={catalogData.pct} className="h-2" />

                  <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3 mt-2">
                    {catalogData.albums.map((album) => (
                      <div
                        key={album.id}
                        className={`rounded-md border p-2 space-y-1 transition-opacity ${
                          album.saved ? "border-primary/40 opacity-100" : "border-border opacity-50"
                        }`}
                      >
                        {album.image_url ? (
                          <img src={album.image_url} alt={album.name} className="w-full aspect-square rounded object-cover" />
                        ) : (
                          <div className="w-full aspect-square rounded bg-muted" />
                        )}
                        <p className="text-xs font-medium truncate">{album.name}</p>
                        <p className="text-xs text-muted-foreground">{album.year}</p>
                        <p className={`text-xs font-medium ${album.saved ? "text-primary" : "text-muted-foreground"}`}>
                          {album.saved ? "Saved" : "Not saved"}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {!loadingCatalog && selectedArtist && !catalogData && (
                <p className="text-sm text-muted-foreground">No catalog data found.</p>
              )}
              {!selectedArtist && !loadingCatalog && (
                <p className="text-sm text-muted-foreground">Search for an artist above to see their discography coverage.</p>
              )}
            </CardContent>
          </Card>

          {/* ── Genre Playlist Picks + Recently Played ──────────────────── */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {/* Genre Playlist Picks — 2/3 width */}
            <div className="lg:col-span-2">
              <Card className="h-full">
                <CardHeader>
                  <CardTitle className="text-base">Genre Playlist Picks</CardTitle>
                  <p className="text-xs text-muted-foreground">
                    Genres are inferred from your top artists for the selected time range, then matched to Spotify playlists.
                  </p>
                </CardHeader>
                <CardContent className="space-y-4">
                  {loadingGenre ? (
                    <p className="text-sm text-muted-foreground">Loading recommendations...</p>
                  ) : genreRecs?.recommendations?.length ? (
                    <div className="space-y-4">
                      {genreRecs.recommendations.map((group) => (
                        <div
                          key={group.genre}
                          className={GENRE_GROUP_CLASS}
                        >
                          <p className="text-base font-semibold tracking-wide capitalize text-zinc-100">{group.genre}</p>
                          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                            {group.playlists.slice(0, 4).map((pl) => (
                              <a
                                key={pl.id}
                                href={pl.open_url || pl.url || `https://open.spotify.com/playlist/${pl.id}`}
                                target="_blank"
                                rel="noreferrer"
                                className="flex items-center gap-3 rounded-md border border-zinc-700/60 bg-zinc-950/45 p-2 hover:bg-zinc-900/55 transition-colors"
                              >
                                {pl.image_url ? (
                                  <img src={pl.image_url} alt={pl.name} className="h-10 w-10 rounded object-cover flex-shrink-0" />
                                ) : (
                                  <div className="h-10 w-10 rounded bg-muted flex-shrink-0" />
                                )}
                                <div className="min-w-0">
                                  <p className="text-sm font-medium truncate">{pl.name}</p>
                                  <p className="text-xs text-muted-foreground truncate">{pl.owner_name || "Spotify"}</p>
                                  <p className="text-xs text-primary underline inline-flex items-center gap-1">
                                    <ExternalLink className="h-3 w-3" />
                                    Open in Spotify
                                  </p>
                                </div>
                              </a>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      No genre-based playlist suggestions available yet for this account/time range.
                    </p>
                  )}
                </CardContent>
              </Card>
            </div>

            {/* Recently Played — 1/3 width side card */}
            <div className="lg:col-span-1">
              <Card className="h-full">
                <CardHeader className="pb-2">
                  <CardTitle className="text-base flex items-center gap-2">
                    <Clock className="h-4 w-4 text-muted-foreground" />
                    Recently Played
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  {loadingRecent ? (
                    <p className="text-xs text-muted-foreground">Loading...</p>
                  ) : visibleRecent.length ? (
                    <>
                      {visibleRecent.map((track, idx) => (
                        <div key={`${track.id}-${idx}`} className="flex items-center gap-2">
                          {track.image_url ? (
                            <img src={track.image_url} alt={track.name} className="h-8 w-8 rounded object-cover flex-shrink-0" />
                          ) : (
                            <div className="h-8 w-8 rounded bg-muted flex-shrink-0" />
                          )}
                          <div className="min-w-0">
                            <p className="text-xs font-medium truncate">{track.name}</p>
                            <p className="text-xs text-muted-foreground truncate">{track.artists?.join(", ")}</p>
                          </div>
                        </div>
                      ))}
                      {recentTracks.length > 5 && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="w-full text-xs mt-1"
                          onClick={() => setShowAllRecent((v) => !v)}
                        >
                          {showAllRecent ? "Show less" : `Show more (${Math.min(recentTracks.length, 25)})`}
                        </Button>
                      )}
                    </>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      {recentError
                        ? `Could not load recently played: ${recentError}`
                        : "No recent tracks found."}
                    </p>
                  )}
                </CardContent>
              </Card>
            </div>
          </div>

          {error ? <p className="text-sm text-destructive">{error}</p> : null}
        </>
      ) : null}

      {/* ── Quick Actions ─────────────────────────────────────────────── */}
      <div>
        <h2 className="text-lg font-semibold mb-4">Quick Actions</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {visibleScripts.map((script) => (
            <ScriptCard key={script.id} script={script} />
          ))}
        </div>
      </div>
    </div>
  );
}
