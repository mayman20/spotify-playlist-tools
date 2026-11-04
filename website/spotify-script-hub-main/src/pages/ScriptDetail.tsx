import { useParams, Link } from "react-router-dom";
import { scripts } from "@/lib/mock-data";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ArrowLeft, Play, Eye } from "lucide-react";
import { useEffect, useState } from "react";
import { Separator } from "@/components/ui/separator";
import { toast } from "@/components/ui/use-toast";
import { fetchAutomationTargets, getSessionToken, runScript } from "@/lib/api";
import { finishLocalRun, startLocalRun } from "@/lib/run-history";

export default function ScriptDetail() {
  const { scriptId } = useParams();
  const script = scripts.find((s) => s.id === scriptId);
  const [isRunning, setIsRunning] = useState(false);
  const [dryRun, setDryRun] = useState(false);
  const [lastRunAt, setLastRunAt] = useState<string>("");
  const [lastActionWasPreview, setLastActionWasPreview] = useState(false);
  const [lastOutput, setLastOutput] = useState<string>("");
  const [lastError, setLastError] = useState<string>("");
  const runnableScript = scriptId === "vaulted-add" ? "vaulted" : scriptId === "liked-songs-mirror" ? "liked" : undefined;
  const usesTargetSelection = scriptId === "vaulted-add" || scriptId === "liked-songs-mirror";
  const [targetPlaylistOptions, setTargetPlaylistOptions] = useState<Array<{ value: string; label: string }>>([
    { value: "create_new", label: "Create New" },
  ]);
  const [targetPlaylistSelection, setTargetPlaylistSelection] = useState<string>("create_new");
  const [targetMatchSource, setTargetMatchSource] = useState<"tag" | "name" | "none">("none");
  const [loadingTargets, setLoadingTargets] = useState(false);
  const isEnabled = script?.enabled !== false;

  useEffect(() => {
    if (!usesTargetSelection) return;
    const token = getSessionToken();
    if (!token) return;

    let cancelled = false;
    setLoadingTargets(true);
    fetchAutomationTargets()
      .then((data) => {
        if (cancelled) return;
        const targets = data.targets;
        const selectedTarget = scriptId === "liked-songs-mirror" ? targets.liked : targets.vaulted;
        const options: Array<{ value: string; label: string }> = [
          { value: "create_new", label: "Create New" },
          ...targets.playlists
            .filter((p) => p.id && p.name)
            .map((p) => ({ value: `id:${p.id}`, label: p.name })),
        ];
        if (!options.some((o) => o.label.toLowerCase() === selectedTarget.name_fallback.toLowerCase())) {
          options.push({ value: `name:${selectedTarget.name_fallback}`, label: selectedTarget.name_fallback });
        }
        setTargetPlaylistOptions(options);
        setTargetPlaylistSelection(selectedTarget.default_playlist_id ? `id:${selectedTarget.default_playlist_id}` : `name:${selectedTarget.name_fallback}`);
        setTargetMatchSource(selectedTarget.matched_by);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const defaultName = scriptId === "liked-songs-mirror" ? "Liked Songs Mirror" : "_vaulted";
        setTargetPlaylistOptions([
          { value: "create_new", label: "Create New" },
          { value: `name:${defaultName}`, label: defaultName },
        ]);
        setTargetPlaylistSelection(`name:${defaultName}`);
        const msg = err instanceof Error ? err.message : "Could not load playlist targets.";
        toast({ title: "Target detection fallback", description: msg });
      })
      .finally(() => {
        if (!cancelled) setLoadingTargets(false);
      });

    return () => {
      cancelled = true;
    };
  }, [scriptId, usesTargetSelection]);

  if (!script) {
    return (
      <div className="flex flex-col items-center justify-center h-64 space-y-4">
        <p className="text-muted-foreground">Script not found.</p>
        <Button variant="outline" asChild>
          <Link to="/scripts">Back to Scripts</Link>
        </Button>
      </div>
    );
  }

  const handleRun = (previewOnly = false) => {
    const isPreview = previewOnly || dryRun;
    if (!isEnabled) {
      toast({
        title: "Temporarily unavailable",
        description: script.disabledReason || "This script is currently disabled.",
      });
      return;
    }

    if (!runnableScript) {
      toast({
        title: "Not wired yet",
        description: "This script is not connected to backend execution yet.",
      });
      return;
    }

    const token = getSessionToken();
    if (!token) {
      toast({
        title: "Spotify login required",
        description: "Connect Spotify in Settings before running scripts.",
      });
      return;
    }

    setIsRunning(true);
    setLastError("");
    setLastOutput("");
    setLastActionWasPreview(isPreview);
    const localRunId = isPreview ? undefined : startLocalRun(scriptId || script.id, script.name);
    let payload: { target_playlist_id?: string; target_playlist_name?: string } | undefined;
    if (usesTargetSelection && targetPlaylistSelection !== "create_new") {
      if (targetPlaylistSelection.startsWith("id:")) {
        payload = { target_playlist_id: targetPlaylistSelection.slice(3) };
      } else if (targetPlaylistSelection.startsWith("name:")) {
        payload = { target_playlist_name: targetPlaylistSelection.slice(5) };
      }
    }

    runScript(runnableScript, payload, isPreview)
      .then((data) => {
        const text = JSON.stringify(data);
        setLastRunAt(new Date().toISOString());
        setLastOutput(text);
        if (localRunId) finishLocalRun(localRunId, "succeeded", text);
        toast({
          title: isPreview ? "Preview ready; no changes made" : "Run completed",
          description: text.length > 180 ? `${text.slice(0, 180)}...` : text,
        });
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Failed to dispatch workflow.";
        setLastRunAt(new Date().toISOString());
        setLastError(msg);
        if (localRunId) finishLocalRun(localRunId, "failed", msg);
        toast({
          title: "Run failed",
          description: msg,
          variant: "destructive",
        });
      })
      .finally(() => setIsRunning(false));
  };

  return (
    <div className="space-y-6 max-w-3xl animate-fade-in px-1 sm:px-0">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" asChild>
          <Link to="/scripts">
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <div>
          <h1 className="text-xl sm:text-2xl font-bold">{script.name}</h1>
          <p className="text-muted-foreground">{script.description}</p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Configuration</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          {script.configFields.map((field) => (
            <div key={field.name} className="space-y-2">
              <Label htmlFor={field.name}>{field.label}</Label>
              {field.type === "text" && (
                <Input
                  id={field.name}
                  placeholder={field.placeholder}
                  defaultValue={field.defaultValue as string}
                />
              )}
              {field.type === "number" && (
                <Input
                  id={field.name}
                  type="number"
                  defaultValue={field.defaultValue as number}
                />
              )}
              {field.type === "select" && (
                field.name === "targetPlaylist" && usesTargetSelection ? (
                  <>
                    <Select value={targetPlaylistSelection} onValueChange={setTargetPlaylistSelection}>
                      <SelectTrigger>
                        <SelectValue placeholder={loadingTargets ? "Loading playlists..." : "Select target playlist"} />
                      </SelectTrigger>
                      <SelectContent>
                        {targetPlaylistOptions.map((opt) => (
                          <SelectItem key={opt.value} value={opt.value}>
                            {opt.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <p className="text-xs text-muted-foreground">
                      Auto-target rule: tag first, then case-insensitive name match, then Create New.
                      {targetMatchSource === "tag"
                        ? " Matched by tag."
                        : targetMatchSource === "name"
                          ? " Matched by playlist name."
                          : " No existing match found."}
                    </p>
                  </>
                ) : (
                  <Select defaultValue={field.options?.[0]}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {field.options?.map((opt) => (
                        <SelectItem key={opt} value={opt}>
                          {opt}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )
              )}
              {field.type === "toggle" && (
                <>
                  <Switch
                    id={field.name}
                    defaultChecked={field.defaultValue as boolean}
                  />
                  {scriptId === "vaulted-add" && field.name === "autoRemove" ? (
                    <p className="text-xs text-muted-foreground">
                      Planned behavior: remove songs from source playlists after they are vaulted.
                      Current status: not active in backend execution yet.
                    </p>
                  ) : null}
                </>
              )}
            </div>
          ))}

          {scriptId === "vaulted-add" ? (
            <p className="text-xs text-muted-foreground">
              Vaulted Add builds a vault playlist from your eligible playlists and liked tracks, so tracks can be vaulted even when they are not liked.
              The old "Minimum Plays" field was removed because it was not used by the backend logic.
            </p>
          ) : null}
          {scriptId === "liked-songs-mirror" ? (
            <p className="text-xs text-muted-foreground">
              Liked Songs Mirror copies your liked songs into a playlist so you can share your liked collection publicly.
            </p>
          ) : null}

          <Separator />

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-2">
              <Switch id="dry-run" checked={dryRun} onCheckedChange={setDryRun} disabled={isRunning} />
              <Label htmlFor="dry-run" className="flex items-center gap-1.5">
                <Eye className="h-3.5 w-3.5" /> Dry Run: Run Script only previews
              </Label>
            </div>
            <div className="flex flex-wrap gap-2 sm:justify-end">
              <Button variant="outline" onClick={() => handleRun(true)} disabled={isRunning || !isEnabled || !runnableScript}>
                Preview Changes
              </Button>
              <Button onClick={() => handleRun(false)} disabled={isRunning || !isEnabled}>
                {isRunning ? (
                  <>{lastActionWasPreview ? "Previewing..." : "Running..."}</>
                ) : (
                  <>
                    {dryRun ? <Eye className="h-4 w-4 mr-1.5" /> : <Play className="h-4 w-4 mr-1.5" />}
                    {dryRun ? "Preview Only" : "Run Script"}
                  </>
                )}
              </Button>
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            {runnableScript
              ? "Run Script executes via backend API using your connected Spotify account."
              : "This script is UI-only right now; no workflow is connected yet."}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Output</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="bg-background rounded-lg p-4 font-mono text-sm text-muted-foreground border min-h-[120px]">
            {isRunning ? (
              <div className="space-y-1">
                <p>$ {lastActionWasPreview ? "Previewing" : "Running"} {script.name}...</p>
                <p className="text-primary">→ Connecting to Spotify API...</p>
                <p className="text-primary">→ Fetching library data...</p>
                <p className="animate-pulse">▌</p>
              </div>
            ) : (lastRunAt || lastOutput || lastError) ? (
              <div className="space-y-1">
                {lastRunAt ? <p className="text-muted-foreground/60">Last {lastActionWasPreview ? "preview" : "run"}: {new Date(lastRunAt).toLocaleString()}</p> : null}
                {lastError ? <p className="text-destructive">Error: {lastError}</p> : null}
                {lastOutput ? <pre className="whitespace-pre-wrap break-all">{lastOutput}</pre> : null}
              </div>
            ) : (
              <p>No runs yet. Configure and click "Run Script" to get started.</p>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
