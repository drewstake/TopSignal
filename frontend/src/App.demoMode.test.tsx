// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const authMocks = vi.hoisted(() => ({
  demoEnabled: vi.fn(() => true),
  bootstrap: vi.fn(),
  subscribe: vi.fn(() => vi.fn()),
  signIn: vi.fn(),
}));

vi.mock("./lib/demoMode", () => ({
  isDemoModeEnabled: authMocks.demoEnabled,
}));

vi.mock("./lib/supabase", () => ({
  hasSupabaseConfig: true,
  bootstrapSupabaseSession: authMocks.bootstrap,
  subscribeSupabaseAuthChanges: authMocks.subscribe,
  signInWithGoogle: authMocks.signIn,
}));

vi.mock("./app/routes", () => ({ router: {} }));
vi.mock("react-router-dom", () => ({
  RouterProvider: () => <div>TopSignal routes</div>,
}));

import App from "./App";

describe("App Demo authentication isolation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authMocks.demoEnabled.mockReturnValue(true);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllEnvs();
  });

  it("renders the persisted Demo without creating or reading a Supabase session", () => {
    render(<App />);

    expect(screen.getByText("TopSignal routes")).toBeTruthy();
    expect(authMocks.bootstrap).not.toHaveBeenCalled();
    expect(authMocks.subscribe).not.toHaveBeenCalled();
  });

  it("retains the normal authentication bootstrap outside Demo Mode", async () => {
    authMocks.demoEnabled.mockReturnValue(false);
    authMocks.bootstrap.mockResolvedValue({ user: { id: "live-user" } });

    render(<App />);

    await waitFor(() => expect(screen.getByText("TopSignal routes")).toBeTruthy());
    expect(authMocks.bootstrap).toHaveBeenCalledTimes(1);
    expect(authMocks.subscribe).toHaveBeenCalledTimes(1);
  });

  it("offers the separate offline workspace below Google without bypassing cloud authentication", async () => {
    authMocks.demoEnabled.mockReturnValue(false);
    authMocks.bootstrap.mockResolvedValue(null);
    vi.stubEnv("DEV", true);
    render(<App />);

    const google = await screen.findByRole("button", { name: "Continue with Google" });
    const offline = screen.getByRole("link", { name: "Continue offline" });
    expect(offline.getAttribute("href")).toBe("http://127.0.0.1:5174");
    expect(google.compareDocumentPosition(offline) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText(/does not automatically sync to the cloud/)).toBeTruthy();
    expect(screen.queryByText("TopSignal routes")).toBeNull();
    expect(authMocks.signIn).not.toHaveBeenCalled();
  });

  it("does not advertise the development-only workspace in a production build", async () => {
    authMocks.demoEnabled.mockReturnValue(false);
    authMocks.bootstrap.mockResolvedValue(null);
    vi.stubEnv("DEV", false);
    render(<App />);

    await screen.findByRole("button", { name: "Continue with Google" });
    expect(screen.queryByRole("link", { name: "Continue offline" })).toBeNull();
  });
});
