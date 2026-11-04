import { useEffect, useMemo, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ExternalLink, Music2, RefreshCw } from "lucide-react";
import { fetchPlaylistFreshness, runArchiveStale } from "@/lib/api";
import { toast } from "@/components/ui/use-toast";

type FreshPlaylist = {
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
};

function freshnessStyle(score: number) {
  const clamped = Math.max(0, Math.min(score, 100));
  const red = Math.round(220 - clamped * 1.6);
  const green = Math.round(60 + clamped * 1.4);
  return {
    backgroundColor: `rgba(${red}, ${green}, 90, 0.18)`,
    borderColor: `rgba(${red}, ${green}, 90, 0.45)`,
  };
}

function freshnessLabel(daysSinceActivity: number) {
  if (daysSinceActivity > 900) return "Last activity unknown";
  if (daysSinceActivity === 0) return "Active today";
  if (daysSinceActivity === 1) return "Active 1 day ago";
  return `Active ${daysSinceActivity} days ago`;
}

function updatedLabel(lastAddedAt: string | null) {
  if (!lastAddedAt) return "No recent add date";
  return `Updated ${new Date(lastAddedAt).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  })}`;
}

export default function Library() {
  const [playlists, setPlaylists] = useState<FreshPlaylist[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [threshold, setThreshold] = useState("30");
  const [prefix, setPrefix] = useState("[Archive]");
  const [archiving, setArchiving] = useState(false);

  function load() {
    setLoading(true);
    setError("");
    fetchPlaylistFreshness()
      .then((resp) => setPlaylists(resp.data.playlists || []))
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Failed to load playlist freshness.";
        setError(msg);
      })
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
  }, []);

  const staleCount = useMemo(() => {
    const t = Number(threshold);
    if (Number.isNaN(t)) return 0;
    return playlists.filter((p) => p.freshness_score <= t).length;
  }, [playlists, threshold]);

  function handleArchive() {
    const t = Number(threshold);
    if (Number.isNaN(t) || t < 0 || t > 100) {
      toast({ title: "Invalid threshold", description: "Use a value between 0 and 100.", variant: "destructive" });
      return;
    }
    setArchiving(true);
    runArchiveStale(t, prefix || "[Archive]")
      .then((resp) => {
        toast({
          title: "Archive run complete",
          description: `Archived ${resp.result.archived_count} playlists.`,
        });
        load();
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Archive run failed.";
        toast({ title: "Archive failed", description: msg, variant: "destructive" });
      })
      .finally(() => setArchiving(false));
  }

  return (
    <div className="space-y-5 animate-fade-in">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold">Library Freshness</h1>
          <p className="text-sm text-muted-foreground">
            Playlist activity scored from fresh (100) to stale (0) using last-added track time.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          <RefreshCw className="h-4 w-4 mr-1" />
          Refresh
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Archive By Freshness Score</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground">Archive playlists with score ≤</p>
              <Input value={threshold} onChange={(e) => setThreshold(e.target.value)} placeholder="30" />
            </div>
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground">Archive prefix</p>
              <Input value={prefix} onChange={(e) => setPrefix(e.target.value)} placeholder="[Archive]" />
            </div>
            <div className="flex items-end">
              <Button onClick={handleArchive} disabled={archiving || loading} className="w-full sm:w-auto">
                {archiving ? "Running..." : `Archive ${staleCount} Playlist(s)`}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Playlists</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {loading ? <p className="text-sm text-muted-foreground">Loading playlists...</p> : null}
          {error ? <p className="text-sm text-destructive">{error}</p> : null}
          {!loading && !error && playlists.length === 0 ? (
            <p className="text-sm text-muted-foreground">No playlists found.</p>
          ) : null}
          {!loading && !error ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {playlists.map((pl) => (
                <a
                  key={pl.id}
                  href={pl.spotify_url}
                  target="_blank"
                  rel="noreferrer"
                  className="group overflow-hidden rounded-xl border transition-all hover:-translate-y-0.5 hover:shadow-lg"
                  style={freshnessStyle(pl.freshness_score)}
                >
                  <div className="relative aspect-square overflow-hidden border-b border-white/10 bg-gradient-to-br from-emerald-500/20 via-zinc-950 to-zinc-900">
                    {pl.image_url ? (
                      <img
                        src={pl.image_url}
                        alt={pl.name}
                        className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
                      />
                    ) : (
                      <div className="flex h-full w-full items-center justify-center">
                        <div className="flex h-20 w-20 items-center justify-center rounded-3xl border border-white/10 bg-black/20 backdrop-blur-sm">
                          <Music2 className="h-10 w-10 text-zinc-200" />
                        </div>
                      </div>
                    )}
                    <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 via-black/35 to-transparent p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate text-base font-semibold text-white">{pl.name}</p>
                          <p className="mt-1 text-xs text-zinc-300">{pl.track_count} tracks</p>
                        </div>
                        <span className="inline-flex items-center gap-1 rounded-full bg-black/40 px-2.5 py-1 text-[11px] font-medium text-white">
                          {pl.freshness_score}
                        </span>
                      </div>
                    </div>
                  </div>

                  <div className="space-y-3 p-4">
                    <div className="flex flex-wrap gap-2">
                      {pl.is_vaulted_tagged ? (
                        <span className="rounded-full bg-primary/15 px-2.5 py-1 text-[11px] font-medium text-primary">
                          Vaulted
                        </span>
                      ) : null}
                      {pl.is_liked_tagged ? (
                        <span className="rounded-full bg-sky-500/15 px-2.5 py-1 text-[11px] font-medium text-sky-300">
                          Liked Mirror
                        </span>
                      ) : null}
                      {!pl.is_vaulted_tagged && !pl.is_liked_tagged ? (
                        <span className="rounded-full bg-white/6 px-2.5 py-1 text-[11px] font-medium text-zinc-300">
                          Standard playlist
                        </span>
                      ) : null}
                    </div>

                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <div className="rounded-lg bg-black/15 p-2.5">
                        <p className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">Freshness</p>
                        <p className="mt-1 text-sm font-semibold text-foreground">{pl.freshness_score}/100</p>
                      </div>
                      <div className="rounded-lg bg-black/15 p-2.5">
                        <p className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">Activity</p>
                        <p className="mt-1 text-sm font-semibold text-foreground">
                          {pl.days_since_activity > 900 ? "Unknown" : `${pl.days_since_activity}d`}
                        </p>
                      </div>
                    </div>

                    <div className="space-y-1">
                      <p className="text-xs text-muted-foreground">{freshnessLabel(pl.days_since_activity)}</p>
                      <p className="text-xs text-muted-foreground">{updatedLabel(pl.last_added_at)}</p>
                      {pl.description ? (
                        <p className="line-clamp-2 text-xs text-zinc-300/90">{pl.description}</p>
                      ) : (
                        <p className="text-xs text-zinc-400">No playlist description.</p>
                      )}
                    </div>

                    <div className="inline-flex items-center gap-1 text-xs font-medium text-primary">
                      Open in Spotify
                      <ExternalLink className="h-3 w-3" />
                    </div>
                  </div>
                </a>
              ))}
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
