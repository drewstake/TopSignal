// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const token = "local-test-token";
const nextSession = () => ({ access_token: token, expires_at: Math.floor(Date.now() / 1000) + 43200,
  user: { id: "existing-owner", email: "owner@example.test" } });

describe("local authentication", () => {
  beforeEach(() => { vi.resetModules(); sessionStorage.clear(); vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

  it("requires local sign-in in a production build without cloud configuration", async () => {
    vi.stubEnv("PROD", true);
    vi.stubEnv("VITE_LOCAL_AUTH_ENABLED", "false");
    vi.stubEnv("VITE_SUPABASE_URL", "");
    const local = await import("./localAuth");
    expect(local.localAuthConfigured).toBe(true);
    expect(local.usesLocalAuth()).toBe(true);
    expect(await local.restoreLocalSession()).toBeNull();
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(nextSession())));
    vi.stubGlobal("fetch", fetchMock);
    await local.continueOnThisDevice();
    const auth = await import("./auth");
    expect(await auth.getAccessToken()).toBe(token);
  });

  it("ignores obsolete cloud configuration and cached auth mode in production", async () => {
    vi.stubEnv("PROD", true);
    vi.stubEnv("VITE_LOCAL_AUTH_ENABLED", "false");
    vi.stubEnv("VITE_SUPABASE_URL", "https://example.supabase.co");
    const local = await import("./localAuth");
    expect(local.localAuthConfigured).toBe(true);
    expect(local.usesLocalAuth()).toBe(true);
  });

  it("exchanges only the trusted connection for a session, then restores and logs out", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify(nextSession()))));
    vi.stubGlobal("fetch", fetchMock);
    const local = await import("./localAuth");
    await local.continueOnThisDevice();
    expect(fetchMock.mock.calls[0][0].pathname).toBe("/auth/local/device");
    expect(fetchMock.mock.calls[0][1].headers["X-TopSignal-Local-Access"]).toBe("device");
    expect(fetchMock.mock.calls[0][1].body).toBe("{}");
    const apiAuth = await import("./auth");
    expect(await apiAuth.getAccessToken()).toBe(token);
    expect((await local.restoreLocalSession())?.user.id).toBe("existing-owner");
    await local.signOutLocally();
    expect(await apiAuth.getAccessToken()).toBeNull();
  });

  it("prefers local sign-in on a configured fresh tab without contacting Google", async () => {
    vi.stubEnv("VITE_LOCAL_AUTH_ENABLED", "true");
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    const auth = await import("./auth");
    expect(await auth.bootstrapSession()).toBeNull();
    const unsubscribe = auth.subscribeAuthChanges(vi.fn());
    expect(await auth.getAccessToken()).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    unsubscribe();
  });

  it("uses the existing API token path, restores after refresh, and revokes logout", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(nextSession()), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    let local = await import("./localAuth");
    await local.signInLocally("my long passphrase", "one-time-code");
    const apiAuth = await import("./auth");
    expect(await apiAuth.getAccessToken()).toBe(token);
    expect(apiAuth.getAccessTokenSync()).toBe(token);
    expect(apiAuth.getCurrentUserEmailSync()).toBe("owner@example.test");
    expect(sessionStorage.getItem("topsignal.local-session.v1")).not.toContain("passphrase");
    expect(sessionStorage.getItem("topsignal.local-session.v1")).not.toContain("one-time-code");
    vi.resetModules();
    local = await import("./localAuth");
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(nextSession()), { status: 200 }));
    await local.restoreLocalSession();
    expect(local.localSessionSync()?.user.id).toBe("existing-owner");
    fetchMock.mockResolvedValueOnce(new Response("{}", { status: 200 }));
    await local.signOutLocally();
    expect(fetchMock.mock.lastCall?.[0].pathname).toBe("/auth/local/logout");
    expect(local.localSessionSync()).toBeNull();
    expect(local.usesLocalAuth()).toBe(true); // Never falls back to a cached Google identity.
    const freshApiAuth = await import("./auth");
    expect(await freshApiAuth.getAccessToken()).toBeNull();
  });

  it("expires sessions and notifies the UI without refreshing Google", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(nextSession()))));
    const local = await import("./localAuth");
    const changed = vi.fn(); local.subscribeLocalAuth(changed);
    await local.signInLocally("my long passphrase");
    vi.advanceTimersByTime(43200001);
    expect(changed).toHaveBeenLastCalledWith(null);
    expect(local.localSessionSync()).toBeNull();
    expect(local.usesLocalAuth()).toBe(true);
  });

  it("does not accept a revoked stored session", async () => {
    sessionStorage.setItem("topsignal.auth-mode", "local");
    sessionStorage.setItem("topsignal.local-session.v1", JSON.stringify(nextSession()));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 401 })));
    const local = await import("./localAuth");
    expect(await local.restoreLocalSession()).toBeNull();
    expect(sessionStorage.getItem("topsignal.local-session.v1")).toBeNull();
  });

  it("keeps logout failures visible instead of claiming server revocation", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(nextSession())))
      .mockResolvedValueOnce(new Response("{}", { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);
    const local = await import("./localAuth");
    await local.signInLocally("my long passphrase");
    await expect(local.signOutLocally()).rejects.toThrow("Could not revoke");
    expect(local.localSessionSync()?.access_token).toBe(token);
    expect(local.canAutoSignInOnThisDevice()).toBe(true);
  });

  it("keeps explicit sign-out across refreshes until manually reconnecting", async () => {
    vi.stubEnv("VITE_LOCAL_AUTH_ENABLED", "true");
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify(nextSession())))));
    let local = await import("./localAuth");
    expect(local.canAutoSignInOnThisDevice()).toBe(true);
    await local.continueOnThisDevice();
    await local.signOutLocally();
    expect(local.canAutoSignInOnThisDevice()).toBe(false);
    vi.resetModules();
    local = await import("./localAuth");
    expect(local.canAutoSignInOnThisDevice()).toBe(false);
    expect(await local.restoreLocalSession()).toBeNull();
    await local.continueOnThisDevice();
    expect(local.canAutoSignInOnThisDevice()).toBe(true);

  });

  it("shares concurrent device exchanges and permits retry after failure", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response("{}", { status: 503 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(nextSession())));
    vi.stubGlobal("fetch", fetchMock);
    const local = await import("./localAuth");
    const first = local.continueOnThisDevice();
    const second = local.continueOnThisDevice();
    expect(first).toBe(second);
    await expect(first).rejects.toThrow("Could not connect");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect((await local.continueOnThisDevice()).access_token).toBe(token);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
