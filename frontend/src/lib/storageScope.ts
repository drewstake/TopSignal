import { isDemoModeEnabled } from "./demoMode";
import { getAccessTokenSync } from "./supabase";

function decodeJwtPayload(accessToken: string): Record<string, unknown> | null {
  const payload = accessToken.split(".")[1];
  if (!payload) {
    return null;
  }

  try {
    const normalized = payload.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
    const decoded = typeof atob === "function" ? atob(padded) : "";
    const value = JSON.parse(decoded) as unknown;
    return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}
function stableHash(value: string) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

/**
 * Returns a non-secret storage lane. Unscoped legacy keys are deliberately not
 * read so one signed-in user can never inherit another user's browser state.
 */
export function getBrowserStorageScope(): string {
  if (isDemoModeEnabled()) {
    return "demo";
  }

  const accessToken = getAccessTokenSync();
  if (!accessToken) {
    return "anonymous";
  }

  const payload = decodeJwtPayload(accessToken);
  const subject = typeof payload?.sub === "string" ? payload.sub : null;
  const issuer = typeof payload?.iss === "string" ? payload.iss : "supabase";
  if (subject) {
    return `user-${stableHash(`${issuer}|${subject}`)}`;
  }

  // Supabase access tokens are JWTs. This fallback keeps an unexpected opaque
  // token isolated without placing the credential itself in the key.
  return `session-${stableHash(accessToken)}`;
}

export function getScopedStorageKey(baseKey: string): string {
  return `${baseKey}:${getBrowserStorageScope()}`;
}
