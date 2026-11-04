import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Disc3, Star, Heart, Archive, Copy, Lock, Zap, ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";
import { scripts } from "@/lib/mock-data";
import { getSpotifyLoginUrl } from "@/lib/api";

const iconMap: Record<string, React.ComponentType<{ className?: string }>> = {
  Star, Heart, Archive, Copy, Lock, Zap,
};

export default function Index() {
  const loginUrl = getSpotifyLoginUrl();

  return (
    <div className="min-h-screen flex flex-col">
      <div className="flex-1 flex flex-col items-center justify-center px-6 py-24 text-center">
        <div className="flex items-center gap-3 mb-8">
          <Disc3 className="h-10 w-10 text-primary" />
          <h1 className="text-4xl md:text-5xl font-bold tracking-tight">
            Spotify Library <span className="text-primary">Toolkit</span>
          </h1>
        </div>
        <p className="text-lg text-muted-foreground max-w-lg mb-10">
          Run automations to organize playlists, liked songs, and your library.
          Connect your Spotify account to get started.
        </p>
        {loginUrl ? (
          <Button size="lg" asChild className="text-base px-8">
            <a href={loginUrl}>
              Login with Spotify <ArrowRight className="ml-2 h-4 w-4" />
            </a>
          </Button>
        ) : (
          <Button size="lg" asChild className="text-base px-8">
            <Link to="/settings">
              Configure API First <ArrowRight className="ml-2 h-4 w-4" />
            </Link>
          </Button>
        )}
      </div>

      <div className="border-t">
        <div className="max-w-5xl mx-auto px-6 py-16">
          <h2 className="text-xl font-semibold mb-8 text-center">Available Automations</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {scripts.map((script) => {
              const Icon = iconMap[script.icon] || Zap;
              return (
                <Card key={script.id} className="hover:border-primary/30 transition-colors">
                  <CardContent className="flex items-start gap-3 p-5">
                    <div className="p-2 rounded-lg bg-primary/10 shrink-0">
                      <Icon className="h-4 w-4 text-primary" />
                    </div>
                    <div>
                      <p className="font-medium text-sm">{script.name}</p>
                      <p className="text-xs text-muted-foreground mt-1">{script.description}</p>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
