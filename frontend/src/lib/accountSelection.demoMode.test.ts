// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { getAccessTokenSyncMock } = vi.hoisted(() => ({
  getAccessTokenSyncMock: vi.fn<() => string | null>(() => null),
}));

vi.mock("./supabase", () => ({
  getAccessTokenSync: getAccessTokenSyncMock,
}));

import {
  ACTIVE_ACCOUNT_STORAGE_KEY,
  DEMO_RETURN_SCOPE_STORAGE_KEY,
  DEMO_RETURN_SNAPSHOT_STORAGE_KEY,
  MAIN_ACCOUNT_STORAGE_KEY,
  captureLiveModeReturnSnapshot,
  readLiveModeReturnSnapshot,
  readStoredAccountId,
  readStoredMainAccountId,
  writeStoredAccountId,
  writeStoredMainAccountId,
} from "./accountSelection";
import { setDemoModeEnabled } from "./demoMode";

function jwt(subject: string): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const payload = btoa(JSON.stringify({ iss: "https://auth.example.test", sub: subject }));
  return `${header}.${payload}.signature`;
}

describe("Demo/live account selection isolation", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    getAccessTokenSyncMock.mockReset();
    getAccessTokenSyncMock.mockReturnValue(jwt("demo-round-trip-user"));
    setDemoModeEnabled(false);
  });

  afterEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    vi.clearAllMocks();
  });

  it("round-trips the exact live route and non-main account without auth access in Demo", () => {
    writeStoredMainAccountId(7001);
    writeStoredAccountId(7002);
    const captured = captureLiveModeReturnSnapshot(
      { pathname: "/trades", search: "?range=30d", hash: "#fills" },
      7002,
    );
    expect(captured?.path).toBe("/trades?range=30d&account=7002#fills");
    expect(window.localStorage.getItem(DEMO_RETURN_SCOPE_STORAGE_KEY)).toBeNull();
    expect(window.sessionStorage.getItem(DEMO_RETURN_SCOPE_STORAGE_KEY)).toBe(captured?.scope);
    expect(
      window.sessionStorage.getItem(`${DEMO_RETURN_SNAPSHOT_STORAGE_KEY}:${captured?.scope}`),
    ).toContain("/trades?range=30d&account=7002#fills");

    setDemoModeEnabled(true);
    getAccessTokenSyncMock.mockClear();

    expect(readStoredAccountId()).toBeNull();
    expect(readStoredMainAccountId()).toBeNull();
    writeStoredMainAccountId(910001);
    writeStoredAccountId(910002);
    expect(readLiveModeReturnSnapshot()).toEqual(captured);
    expect(getAccessTokenSyncMock).not.toHaveBeenCalled();

    setDemoModeEnabled(false);
    expect(readStoredMainAccountId()).toBe(7001);
    expect(readStoredAccountId()).toBe(7002);
  });

  it("never migrates an unscoped real account preference into the Demo lane", () => {
    window.localStorage.setItem(ACTIVE_ACCOUNT_STORAGE_KEY, "7333");
    window.localStorage.setItem(MAIN_ACCOUNT_STORAGE_KEY, "7222");
    setDemoModeEnabled(true);

    expect(readStoredAccountId()).toBeNull();
    expect(readStoredMainAccountId()).toBeNull();
    expect(window.localStorage.getItem(ACTIVE_ACCOUNT_STORAGE_KEY)).toBe("7333");
    expect(window.localStorage.getItem(MAIN_ACCOUNT_STORAGE_KEY)).toBe("7222");

    setDemoModeEnabled(false);
    expect(readStoredAccountId()).toBe(7333);
    expect(readStoredMainAccountId()).toBe(7222);
    expect(window.localStorage.getItem(ACTIVE_ACCOUNT_STORAGE_KEY)).toBeNull();
    expect(window.localStorage.getItem(MAIN_ACCOUNT_STORAGE_KEY)).toBeNull();
  });
});
