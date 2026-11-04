import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Settings from "@/pages/Settings";

const { api, toast } = vi.hoisted(() => ({
  api: {
    clearSessionToken: vi.fn(() => localStorage.removeItem("spotify_session_token")),
    fetchMe: vi.fn(),
    getApiBaseUrl: vi.fn(() => "https://backend.example"),
    getSessionToken: vi.fn(() => localStorage.getItem("spotify_session_token") || ""),
    getSpotifyLoginUrl: vi.fn(() => "https://backend.example/auth/login"),
    logoutSession: vi.fn(),
  },
  toast: vi.fn(),
}));

vi.mock("@/lib/api", () => api);
vi.mock("@/components/ui/use-toast", () => ({ toast }));

describe("Settings disconnect", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("spotify_session_token", "test-session");
    api.fetchMe.mockResolvedValue({ spotify_user_id: "user-1", display_name: "Listener" });
    api.logoutSession.mockReset();
    api.clearSessionToken.mockClear();
    toast.mockClear();
  });

  it("revokes the server session before clearing the browser session", async () => {
    api.logoutSession.mockResolvedValue(undefined);
    render(<Settings />);

    fireEvent.click(await screen.findByRole("button", { name: "Disconnect" }));

    await waitFor(() => expect(api.clearSessionToken).toHaveBeenCalledOnce());
    expect(api.logoutSession).toHaveBeenCalledOnce();
    expect(localStorage.getItem("spotify_session_token")).toBeNull();
    expect(toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Disconnected" }),
    );
  });

  it("keeps the browser token when server revocation fails and reports failure", async () => {
    api.logoutSession.mockRejectedValue(new Error("Network unavailable"));
    render(<Settings />);

    fireEvent.click(await screen.findByRole("button", { name: "Disconnect" }));

    await waitFor(() => expect(toast).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "Disconnect failed",
        description: expect.stringContaining("Network unavailable"),
        variant: "destructive",
      }),
    ));
    expect(api.clearSessionToken).not.toHaveBeenCalled();
    expect(localStorage.getItem("spotify_session_token")).toBe("test-session");
  });
});
