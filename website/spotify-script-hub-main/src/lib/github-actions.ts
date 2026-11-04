const REPO_OWNER = "mayman20";
const REPO_NAME = "spotipy_scripts";
const WORKFLOW_FILE = "run-spotify-script.yml";

export type RunnableScript = "vaulted_add" | "liked_add";

const SCRIPT_MAP: Record<string, RunnableScript | undefined> = {
  "vaulted-add": "vaulted_add",
  "liked-songs-mirror": "liked_add",
};

export function resolveRunnableScript(scriptId: string): RunnableScript | undefined {
  return SCRIPT_MAP[scriptId];
}

export function getActionsRunPageUrl(): string {
  return `https://github.com/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW_FILE}`;
}

export async function dispatchScriptRun(script: RunnableScript, token: string): Promise<void> {
  const resp = await fetch(
    `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW_FILE}/dispatches`,
    {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${token}`,
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ref: "main",
        inputs: { script },
      }),
    },
  );

  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const json = await resp.json();
      if (json?.message) {
        detail = `${detail}: ${json.message}`;
      }
    } catch {
      // keep default detail
    }
    throw new Error(detail);
  }
}
