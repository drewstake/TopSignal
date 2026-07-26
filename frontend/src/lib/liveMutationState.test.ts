// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  beginLiveMutationRequest,
  hasActiveLiveMutationRequests,
  liveMutationLeaseInternals,
} from "./liveMutationState";

describe("live mutation state", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    window.localStorage.clear();
    vi.unstubAllGlobals();
  });

  it("tracks overlapping requests and makes completion idempotent", () => {
    const finishFirst = beginLiveMutationRequest();
    const finishSecond = beginLiveMutationRequest();

    expect(hasActiveLiveMutationRequests()).toBe(true);
    finishFirst();
    expect(hasActiveLiveMutationRequests()).toBe(true);
    finishFirst();
    expect(hasActiveLiveMutationRequests()).toBe(true);
    finishSecond();
    expect(hasActiveLiveMutationRequests()).toBe(false);
  });

  it("honors fresh cross-tab leases and removes expired leases", () => {
    const remoteKey = `${liveMutationLeaseInternals.storagePrefix}remote-tab`;
    window.localStorage.setItem(
      remoteKey,
      JSON.stringify({ owner_id: "remote-tab", active_count: 2, expires_at_ms: Date.now() + 30_000 }),
    );

    expect(hasActiveLiveMutationRequests()).toBe(true);

    window.localStorage.setItem(
      remoteKey,
      JSON.stringify({ owner_id: "remote-tab", active_count: 1, expires_at_ms: Date.now() - 1 }),
    );
    expect(hasActiveLiveMutationRequests()).toBe(false);
    expect(window.localStorage.getItem(remoteKey)).toBeNull();
  });

  it("never releases another document owner's lease", () => {
    const remoteKey = `${liveMutationLeaseInternals.storagePrefix}second-document`;
    window.localStorage.setItem(
      remoteKey,
      JSON.stringify({ owner_id: "second-document", active_count: 1, expires_at_ms: Date.now() + 30_000 }),
    );

    const finishLocal = beginLiveMutationRequest();
    const leaseKeys = Array.from({ length: window.localStorage.length }, (_, index) => window.localStorage.key(index))
      .filter((key): key is string => Boolean(key?.startsWith(liveMutationLeaseInternals.storagePrefix)));
    expect(leaseKeys).toHaveLength(2);

    finishLocal();
    expect(window.localStorage.getItem(remoteKey)).not.toBeNull();
    expect(hasActiveLiveMutationRequests()).toBe(true);
  });

  it("publishes lease and release messages over BroadcastChannel", () => {
    const postMessage = vi.fn();
    const close = vi.fn();
    const BroadcastChannelMock = vi.fn(function BroadcastChannelMock() {
      return { postMessage, close };
    });
    vi.stubGlobal("BroadcastChannel", BroadcastChannelMock);

    const finish = beginLiveMutationRequest();
    finish();

    expect(BroadcastChannelMock).toHaveBeenCalledWith(liveMutationLeaseInternals.channelName);
    expect(postMessage).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({
        type: "lease",
        lease: expect.objectContaining({ active_count: 1 }),
      }),
    );
    expect(postMessage).toHaveBeenLastCalledWith(expect.objectContaining({ type: "release" }));
    expect(close).toHaveBeenCalledTimes(2);
  });
});
