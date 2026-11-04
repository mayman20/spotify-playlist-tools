export type LocalRunStatus = "running" | "succeeded" | "failed";

export type LocalRun = {
  id: string;
  scriptId: string;
  scriptName: string;
  status: LocalRunStatus;
  startedAt: string;
  finishedAt?: string;
  summary?: string;
};

const STORAGE_KEY = "spotify_local_run_history";
const MAX_RUNS = 50;

export function getLocalRuns(): LocalRun[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((run): run is LocalRun =>
      Boolean(
        run &&
          typeof run.id === "string" &&
          typeof run.scriptId === "string" &&
          typeof run.scriptName === "string" &&
          ["running", "succeeded", "failed"].includes(run.status) &&
          typeof run.startedAt === "string",
      ),
    );
  } catch {
    return [];
  }
}

function saveLocalRuns(runs: LocalRun[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(runs.slice(0, MAX_RUNS)));
  } catch {
    // Run history is best-effort and never blocks script execution.
  }
}

export function startLocalRun(scriptId: string, scriptName: string): string {
  const id = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
  const run: LocalRun = { id, scriptId, scriptName, status: "running", startedAt: new Date().toISOString() };
  saveLocalRuns([run, ...getLocalRuns()]);
  return id;
}

export function finishLocalRun(id: string, status: Exclude<LocalRunStatus, "running">, summary: string): void {
  const finishedAt = new Date().toISOString();
  saveLocalRuns(
    getLocalRuns().map((run) =>
      run.id === id ? { ...run, status, finishedAt, summary: summary.slice(0, 500) } : run,
    ),
  );
}
