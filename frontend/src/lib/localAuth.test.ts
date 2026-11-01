// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const token = "local-test-token";
const nextSession = () => ({ access_token: token, expires_at: Math.floor(Date.now() / 1000) + 43200,
  user: { id: "existing-owner", email: "owner@example.test" } });

describe("local authentication", () => {
  beforeEach(() => { vi.resetModules(); sessionStorage.clear(); vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

  it("prefers local sign-in on a configured fresh tab without contacting Google", async () => {
    vi.stubEnv("VITE_LOCAL_AUTH_ENABLED", "true");
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    const auth = await import("./supabase");
    expect(await auth.bootstrapSupabaseSession()).toBeNull();
    const unsubscribe = auth.subscribeSupabaseAuthChanges(vi.fn());
    expect(await auth.getAccessToken()).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    unsubscribe();
  });

  it("uses the existing API token path, restores after refresh, and revokes logout", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(nextSession()), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    let local = await import("./localAuth");
    await local.signInLocally("my long passphrase", "one-time-code");
    const apiAuth = await import("./supabase");
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
    const freshApiAuth = await import("./supabase");
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
  });
});
