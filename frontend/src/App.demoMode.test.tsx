// @vitest-environment jsdom

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
});
