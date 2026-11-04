import { ScriptCard } from "@/components/ScriptCard";
import { scripts } from "@/lib/mock-data";

export default function Scripts() {
  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h1 className="text-2xl font-bold">Scripts</h1>
        <p className="text-muted-foreground">Configure and run your Spotify automations.</p>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {scripts.map((script) => (
          <ScriptCard key={script.id} script={script} />
        ))}
      </div>
    </div>
  );
}
