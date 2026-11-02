import type { Session } from "@supabase/supabase-js";

const KEY = "topsignal.local-session.v1";
const MODE_KEY = "topsignal.auth-mode";
const BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
export const localAuthConfigured = import.meta.env.VITE_LOCAL_AUTH_ENABLED === "true";
const listeners = new Set<(session: Session | null) => void>();
let session: Session | null = null;
let expiryTimer: ReturnType<typeof setTimeout> | undefined;

export function usesLocalAuth(): boolean {
  if (typeof sessionStorage === "undefined") return false;
  const mode = sessionStorage.getItem(MODE_KEY);
  return mode === "local" || (mode === null && localAuthConfigured);
}

function publish(next: Session | null): void {
  session = next;
  clearTimeout(expiryTimer);
  if (next) {
    sessionStorage.setItem(KEY, JSON.stringify(next));
    expiryTimer = setTimeout(() => publish(null), Math.max(0, (next.expires_at! * 1000) - Date.now()));
  } else {
    sessionStorage.removeItem(KEY);
  }
  listeners.forEach((listener) => listener(next));
}

export function subscribeLocalAuth(listener: (next: Session | null) => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

async function request(path: string, body?: unknown, token?: string): Promise<Response> {
  return fetch(new URL(`/auth/local/${path}`, BASE), {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(path === "device" ? { "X-TopSignal-Local-Access": "device" } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    cache: "no-store",
    signal: AbortSignal.timeout(10000),
  });
}

export type LocalAuthStatus = { enabled: boolean; needs_setup: boolean; mode?: "password" | "trusted_device" };

export async function localAuthStatus(): Promise<LocalAuthStatus> {
  const response = await request("status");
  if (response.status === 404) return { enabled: false, needs_setup: false };
  if (!response.ok) throw new Error("Cannot reach local sign-in. Check the worker connection and retry.");
  return response.json();
}

export async function restoreLocalSession(): Promise<Session | null> {
  if (!usesLocalAuth()) return null;
  let saved: Session | null = null;
  try { saved = JSON.parse(sessionStorage.getItem(KEY) ?? "null") as Session | null; } catch { /* Invalid local cache. */ }
  if (!saved?.access_token || !saved.expires_at || saved.expires_at * 1000 <= Date.now()) {
    publish(null);
    return null;
  }
  const response = await request("session", undefined, saved.access_token);
  if (response.status === 401) { publish(null); return null; }
  if (!response.ok) throw new Error("Cannot verify local sign-in. Check the worker connection and retry.");
  const verified = await response.json() as Pick<Session, "user" | "expires_at">;
  publish({ ...saved, ...verified });
  return session;
}

export async function signInLocally(password: string, setupCode?: string): Promise<Session> {
  const response = await request(setupCode === undefined ? "login" : "setup",
    { password, ...(setupCode === undefined ? {} : { setup_code: setupCode }) });
  if (!response.ok) {
    if (response.status === 429) throw new Error("Too many sign-in attempts. Wait five minutes and try again.");
    if (response.status === 401) throw new Error(setupCode === undefined ? "Incorrect password." : "Invalid or expired setup code.");
    throw new Error("Local sign-in failed. Check your connection and try again.");
  }
  const next = await response.json() as Session;
  sessionStorage.setItem(MODE_KEY, "local");
  publish(next);
  return next;
}

export async function continueOnThisDevice(): Promise<Session> {
  const response = await request("device", {});
  if (!response.ok) throw new Error("Could not connect. Open the app through your local worker connection and try again.");
  const next = await response.json() as Session;
  sessionStorage.setItem(MODE_KEY, "local");
  publish(next);
  return next;
}

export async function signOutLocally(): Promise<void> {
  const token = session?.access_token;
  if (token) {
    const response = await request("logout", {}, token);
    if (!response.ok && response.status !== 401) throw new Error("Could not revoke the local session. Try again.");
  }
  publish(null);
}

export function localSessionSync(): Session | null {
  if (session?.expires_at && session.expires_at * 1000 <= Date.now()) publish(null);
  return session;
}

export function selectGoogleAuth(): void {
  sessionStorage.setItem(MODE_KEY, "google");
  publish(null);
}
