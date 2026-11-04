import { afterEach, describe, expect, it, vi } from "vitest";
import { captureSessionTokenFromUrl, runScript } from "@/lib/api";

describe("runScript preview mode", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("sends dry_run=true when preview is requested", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchMock);

    await runScript("vaulted", undefined, true);

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ dry_run: true });
  });

  it("defaults normal runs to dry_run=false", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchMock);

    await runScript("liked", { target_playlist_name: "Liked Songs Mirror" });

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      target_playlist_name: "Liked Songs Mirror",
      dry_run: false,
    });
  });

  it("stores a callback fragment and replaces the sensitive history URL in one step", () => {
    window.history.replaceState(
      {},
      "",
      "/?keep=yes#?session_token=fragment-token&spotify_user_id=abc",
    );
    const popstate = vi.fn();
    window.addEventListener("popstate", popstate);

    expect(captureSessionTokenFromUrl()).toBe(true);
    window.removeEventListener("popstate", popstate);
    expect(popstate).toHaveBeenCalledOnce();
    expect(localStorage.getItem("spotify_session_token")).toBe("fragment-token");
    expect(window.location.hash).toBe("#/dashboard");
    expect(window.location.search).toBe("?keep=yes");
    expect(window.location.href).not.toContain("fragment-token");
  });

  it("supports a legacy query callback while cleaning it from history", () => {
    window.history.replaceState({}, "", "/?session_token=query-token&spotify_user_id=abc");

    expect(captureSessionTokenFromUrl()).toBe(true);
    expect(localStorage.getItem("spotify_session_token")).toBe("query-token");
    expect(window.location.href).not.toContain("query-token");
  });

  it("posts the current session token to the backend logout endpoint", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.test");
    vi.resetModules();
    const { logoutSession } = await import("@/lib/api");
    localStorage.setItem("spotify_session_token", "logout-session");
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);

    await logoutSession();

    expect(fetchMock).toHaveBeenCalledWith("https://api.example.test/logout", {
      method: "POST",
      headers: { Authorization: "Bearer logout-session" },
    });
    expect(localStorage.getItem("spotify_session_token")).toBe("logout-session");
  });
});
