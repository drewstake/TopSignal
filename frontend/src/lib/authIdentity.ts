/** Identity only: token refreshes must not invalidate same-user operations. */
export function tokenIdentity(token: string | null): string | null {
  if (!token) return null;
  try {
    const payload = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const value = JSON.parse(atob(payload.padEnd(Math.ceil(payload.length / 4) * 4, "=")));
    if (typeof value.sub === "string") return JSON.stringify([value.iss ?? "supabase", value.sub]);
  } catch { /* Opaque tokens remain distinct, in memory only. */ }
  return token;
}

let identity: string | null = null;
let revision = 0;
const listeners = new Set<() => void>();

export function observeAuthIdentity(token: string | null): void {
  const next = tokenIdentity(token);
  if (identity === next) return;
  identity = next;
  revision += 1;
  for (const listener of listeners) listener();
}

export function authIdentityRevision(): number { return revision; }
export function subscribeToAuthIdentityChanges(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
