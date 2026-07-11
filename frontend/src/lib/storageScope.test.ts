import { afterEach, describe, expect, it, vi } from "vitest";

import { getBrowserStorageScope, getScopedStorageKey } from "./storageScope";

vi.mock("./demoMode", () => ({ isDemoModeEnabled: vi.fn(() => false) }));
vi.mock("./supabase", () => ({ getAccessTokenSync: vi.fn(() => null) }));

import { isDemoModeEnabled } from "./demoMode";
import { getAccessTokenSync } from "./supabase";

function jwt(payload: Record<string, unknown>) {
  const encoded = btoa(JSON.stringify(payload)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `header.${encoded}.signature`;
}

describe("browser storage scope", () => {
  afterEach(() => {
    vi.mocked(isDemoModeEnabled).mockReturnValue(false);
    vi.mocked(getAccessTokenSync).mockReturnValue(null);
  });

  it("uses a stable user lane across token refreshes", () => {
    vi.mocked(getAccessTokenSync).mockReturnValue(jwt({ iss: "https://example.supabase.co/auth/v1", sub: "user-1", exp: 1 }));
    const first = getBrowserStorageScope();
    vi.mocked(getAccessTokenSync).mockReturnValue(jwt({ iss: "https://example.supabase.co/auth/v1", sub: "user-1", exp: 2 }));

    expect(getBrowserStorageScope()).toBe(first);
    expect(getScopedStorageKey("topsignal.settings")).toBe(`topsignal.settings:${first}`);
  });

  it("isolates different users and demo mode", () => {
    vi.mocked(getAccessTokenSync).mockReturnValue(jwt({ iss: "issuer", sub: "user-1" }));
    const first = getBrowserStorageScope();
    vi.mocked(getAccessTokenSync).mockReturnValue(jwt({ iss: "issuer", sub: "user-2" }));
    expect(getBrowserStorageScope()).not.toBe(first);

    vi.mocked(isDemoModeEnabled).mockReturnValue(true);
    expect(getBrowserStorageScope()).toBe("demo");
  });
});
