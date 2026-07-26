const LIVE_MUTATION_LEASE_PREFIX = "topsignal.liveMutationLease:";
const LIVE_MUTATION_CHANNEL_NAME = "topsignal:live-mutation-state";
const LIVE_MUTATION_LEASE_TTL_MS = 2 * 60_000;
const LIVE_MUTATION_HEARTBEAT_MS = 20_000;

interface LiveMutationLease {
  owner_id: string;
  active_count: number;
  expires_at_ms: number;
}

type LiveMutationBroadcast =
  | { type: "lease"; lease: LiveMutationLease }
  | { type: "release"; owner_id: string };

let activeLiveMutationRequests = 0;
let heartbeatId: ReturnType<typeof setInterval> | null = null;
let volatileTabId: string | null = null;

function createTabId(): string {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function getTabId(): string {
  if (!volatileTabId) {
    // Deliberately do not persist this identifier in sessionStorage: browsers
    // clone sessionStorage into newly opened tabs, which could let one tab
    // release another tab's lease.
    volatileTabId = createTabId();
  }
  return volatileTabId;
}

function leaseStorageKey(ownerId: string): string {
  return `${LIVE_MUTATION_LEASE_PREFIX}${ownerId}`;
}

function broadcastLiveMutationState(message: LiveMutationBroadcast): void {
  if (typeof BroadcastChannel !== "function") {
    return;
  }
  try {
    const channel = new BroadcastChannel(LIVE_MUTATION_CHANNEL_NAME);
    channel.postMessage(message);
    channel.close();
  } catch {
    // localStorage remains the authoritative cross-tab lease when broadcast is unavailable.
  }
}

function currentLease(): LiveMutationLease {
  return {
    owner_id: getTabId(),
    active_count: activeLiveMutationRequests,
    expires_at_ms: Date.now() + LIVE_MUTATION_LEASE_TTL_MS,
  };
}

function publishCurrentLease(): void {
  if (activeLiveMutationRequests <= 0) {
    return;
  }
  const lease = currentLease();
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(leaseStorageKey(lease.owner_id), JSON.stringify(lease));
    }
  } catch {
    // Same-tab tracking still prevents a local transition when storage is unavailable.
  }
  broadcastLiveMutationState({ type: "lease", lease });
}

function releaseCurrentLease(): void {
  const ownerId = getTabId();
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.removeItem(leaseStorageKey(ownerId));
    }
  } catch {
    // The lease expires if storage cleanup is unavailable.
  }
  broadcastLiveMutationState({ type: "release", owner_id: ownerId });
}

function startHeartbeat(): void {
  if (heartbeatId !== null || typeof setInterval !== "function") {
    return;
  }
  heartbeatId = setInterval(publishCurrentLease, LIVE_MUTATION_HEARTBEAT_MS);
}

function stopHeartbeat(): void {
  if (heartbeatId === null) {
    return;
  }
  clearInterval(heartbeatId);
  heartbeatId = null;
}

function parseLease(value: string | null): LiveMutationLease | null {
  if (!value) {
    return null;
  }
  try {
    const candidate = JSON.parse(value) as Partial<LiveMutationLease>;
    if (
      typeof candidate.owner_id !== "string" ||
      candidate.owner_id.length === 0 ||
      typeof candidate.active_count !== "number" ||
      !Number.isInteger(candidate.active_count) ||
      candidate.active_count <= 0 ||
      typeof candidate.expires_at_ms !== "number" ||
      !Number.isFinite(candidate.expires_at_ms)
    ) {
      return null;
    }
    return candidate as LiveMutationLease;
  } catch {
    return null;
  }
}

export function beginLiveMutationRequest(): () => void {
  activeLiveMutationRequests += 1;
  publishCurrentLease();
  startHeartbeat();
  let finished = false;

  return () => {
    if (finished) {
      return;
    }
    finished = true;
    activeLiveMutationRequests = Math.max(0, activeLiveMutationRequests - 1);
    if (activeLiveMutationRequests > 0) {
      publishCurrentLease();
      return;
    }
    stopHeartbeat();
    releaseCurrentLease();
  };
}

export function hasActiveLiveMutationRequests(): boolean {
  if (activeLiveMutationRequests > 0) {
    return true;
  }
  if (typeof localStorage === "undefined") {
    return false;
  }

  const now = Date.now();
  const staleKeys: string[] = [];
  let foundActiveLease = false;
  try {
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (!key?.startsWith(LIVE_MUTATION_LEASE_PREFIX)) {
        continue;
      }
      const lease = parseLease(localStorage.getItem(key));
      if (!lease || lease.expires_at_ms <= now) {
        staleKeys.push(key);
        continue;
      }
      foundActiveLease = true;
    }
    for (const key of staleKeys) {
      localStorage.removeItem(key);
    }
  } catch {
    return false;
  }
  return foundActiveLease;
}

export const liveMutationLeaseInternals = {
  storagePrefix: LIVE_MUTATION_LEASE_PREFIX,
  channelName: LIVE_MUTATION_CHANNEL_NAME,
  leaseTtlMs: LIVE_MUTATION_LEASE_TTL_MS,
};
