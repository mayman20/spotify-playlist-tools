import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { toast } from "@/components/ui/use-toast";
import { LogOut, Shield, Info } from "lucide-react";
import { clearSessionToken, fetchMe, getApiBaseUrl, getSessionToken, getSpotifyLoginUrl, logoutSession } from "@/lib/api";

type MeResponse = { spotify_user_id: string; display_name: string };

export default function Settings() {
  const [profile, setProfile] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const apiBase = getApiBaseUrl();
  const loginUrl = getSpotifyLoginUrl();

  useEffect(() => {
    const token = getSessionToken();
    if (!token || !apiBase) {
      return;
    }
    setLoading(true);
    fetchMe()
      .then((data) => setProfile(data))
      .catch(() => setProfile(null))
      .finally(() => setLoading(false));
  }, [apiBase]);

  const disconnect = async () => {
    setDisconnecting(true);
    try {
      await logoutSession();
      clearSessionToken();
      setProfile(null);
      toast({ title: "Disconnected", description: "Server authorization revoked and this browser session cleared." });
    } catch (error) {
      const message = error instanceof Error ? error.message : "The server could not revoke this session.";
      toast({
        title: "Disconnect failed",
        description: `${message} This browser still holds its session until the server confirms logout.`,
        variant: "destructive",
      });
    } finally {
      setDisconnecting(false);
    }
  };

  return (
    <div className="space-y-6 max-w-2xl animate-fade-in">
      <div>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-muted-foreground">Manage your Spotify connection and preferences.</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Shield className="h-4 w-4" /> Spotify Connection
          </CardTitle>
          <CardDescription>
            {profile ? `Connected as ${profile.display_name}` : "Connect your Spotify account to run scripts."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium">Connection Status</p>
              <p className="text-xs text-muted-foreground font-mono">
                {apiBase ? apiBase : "Missing VITE_API_BASE_URL"}
              </p>
            </div>
            <Badge variant="outline" className="text-primary border-primary/30">
              {loading ? "Checking..." : profile ? "Connected" : "Not Connected"}
            </Badge>
          </div>

          {profile && (
            <div className="text-sm text-muted-foreground">
              User ID: <span className="font-mono">{profile.spotify_user_id}</span>
            </div>
          )}

          <Separator />

          <div className="flex gap-2">
            <Button asChild disabled={!apiBase}>
              <a href={loginUrl}>Login with Spotify</a>
            </Button>
            <Button variant="destructive" onClick={disconnect} disabled={disconnecting}>
              <LogOut className="h-4 w-4 mr-2" /> {disconnecting ? "Disconnecting..." : "Disconnect"}
            </Button>
          </div>
          {!apiBase && (
            <p className="text-xs text-destructive">
              Set `VITE_API_BASE_URL` in frontend build environment to your Render backend URL.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Info className="h-4 w-4" /> About
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <div className="flex justify-between">
            <span>App Version</span>
            <span className="font-mono">1.1.0</span>
          </div>
          <div className="flex justify-between">
            <span>Execution Mode</span>
            <span className="font-mono">Backend API</span>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
