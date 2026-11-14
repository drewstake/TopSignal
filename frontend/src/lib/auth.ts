import { observeAuthIdentity } from "./authIdentity";
import { localSessionSync, restoreLocalSession, signOutLocally, subscribeLocalAuth, type Session } from "./localAuth";

export async function bootstrapSession(): Promise<Session | null> {
  const session = await restoreLocalSession();
  observeAuthIdentity(session?.access_token ?? null);
  return session;
}

export function subscribeAuthChanges(onChange: (session: Session | null) => void): () => void {
  return subscribeLocalAuth((session) => {
    observeAuthIdentity(session?.access_token ?? null);
    onChange(session);
  });
}

export const signOut = signOutLocally;
export function getAccessTokenSync(): string | null { return localSessionSync()?.access_token ?? null; }
export async function getAccessToken(): Promise<string | null> { return getAccessTokenSync(); }
export function getCurrentUserEmailSync(): string | null { return localSessionSync()?.user.email ?? null; }
