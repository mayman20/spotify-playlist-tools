import { Link } from "react-router-dom";
import { ExternalLink } from "lucide-react";
import { getLocalRuns } from "@/lib/run-history";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";

const statusStyles: Record<string, string> = {
  running: "bg-info/10 text-info",
  succeeded: "bg-primary/10 text-primary",
  failed: "bg-destructive/10 text-destructive",
};

function durationLabel(startedAt: string, finishedAt?: string): string {
  if (!finishedAt) return "In progress";
  const seconds = Math.max(0, Math.round((Date.parse(finishedAt) - Date.parse(startedAt)) / 1000));
  return `${seconds}s`;
}

export default function Runs() {
  const runs = getLocalRuns();

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h1 className="text-2xl font-bold">Run History</h1>
        <p className="text-muted-foreground">
          Actual runs started in this browser. Previews are not recorded; this history does not sync across browsers.
        </p>
      </div>

      {runs.length ? (
        <div className="border rounded-lg overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Script</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Started</TableHead>
                <TableHead>Duration</TableHead>
                <TableHead>Result</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.map((run) => (
                <TableRow key={run.id}>
                  <TableCell className="font-medium">{run.scriptName}</TableCell>
                  <TableCell>
                    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${statusStyles[run.status]}`}>
                      {run.status}
                    </span>
                  </TableCell>
                  <TableCell className="text-muted-foreground text-sm">
                    {new Date(run.startedAt).toLocaleString()}
                  </TableCell>
                  <TableCell className="text-muted-foreground text-sm">
                    {durationLabel(run.startedAt, run.finishedAt)}
                  </TableCell>
                  <TableCell className="max-w-sm truncate text-muted-foreground text-sm" title={run.summary}>
                    {run.summary || "—"}
                  </TableCell>
                  <TableCell>
                    <Button variant="ghost" size="icon" asChild>
                      <Link to={`/scripts/${run.scriptId}`} aria-label={`Open ${run.scriptName}`}>
                        <ExternalLink className="h-4 w-4" />
                      </Link>
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <div className="rounded-lg border p-8 text-center text-muted-foreground">
          No runs have been started in this browser yet.
        </div>
      )}
    </div>
  );
}
