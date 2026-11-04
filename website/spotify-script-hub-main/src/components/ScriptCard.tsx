import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Star, Heart, Archive, Copy, Lock, Zap } from "lucide-react";
import { Link } from "react-router-dom";
import { useNavigate } from "react-router-dom";
import { Script } from "@/lib/types";

const iconMap: Record<string, React.ComponentType<{ className?: string }>> = {
  Star, Heart, Archive, Copy, Lock, Zap,
};

interface ScriptCardProps {
  script: Script;
  showActions?: boolean;
}

export function ScriptCard({ script, showActions = true }: ScriptCardProps) {
  const Icon = iconMap[script.icon] || Zap;
  const isEnabled = script.enabled !== false;
  const navigate = useNavigate();

  function goToScript() {
    if (!isEnabled) return;
    navigate(`/scripts/${script.id}`);
  }

  return (
    <Card
      className={`group transition-all duration-300 ${isEnabled ? "hover:border-primary/30 cursor-pointer" : "opacity-60"}`}
      onClick={goToScript}
      onKeyDown={(e) => {
        if (!isEnabled) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          goToScript();
        }
      }}
      role={isEnabled ? "button" : undefined}
      tabIndex={isEnabled ? 0 : -1}
    >
      <CardHeader className="pb-3">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-primary/10">
            <Icon className="h-5 w-5 text-primary" />
          </div>
          <CardTitle className="text-base">{script.name}</CardTitle>
        </div>
      </CardHeader>
      <CardContent className="pb-3">
        <p className="text-sm text-muted-foreground">{script.description}</p>
        {!isEnabled && script.disabledReason ? (
          <p className="text-xs text-muted-foreground mt-2">{script.disabledReason}</p>
        ) : null}
      </CardContent>
      {showActions && (
        <CardFooter className="gap-2">
          <Button variant="outline" size="sm" asChild disabled={!isEnabled}>
            {isEnabled ? <Link to={`/scripts/${script.id}`}>Configure</Link> : <span>Configure</span>}
          </Button>
          <Button size="sm" asChild disabled={!isEnabled}>
            {isEnabled ? <Link to={`/scripts/${script.id}`}>Run</Link> : <span>Run</span>}
          </Button>
        </CardFooter>
      )}
    </Card>
  );
}
