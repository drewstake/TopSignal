const TOKEN_KEY = "topsignal.topstep.sessionToken.v1";

export function loadSessionToken(): string | null {
  try {
    const t = sessionStorage.getItem(TOKEN_KEY);
    return t && t.trim().length > 0 ? t : null;
  } catch {
    return null;
  }
}

export function saveSessionToken(token: string) {
  sessionStorage.setItem(TOKEN_KEY, token);
}

export function clearSessionToken() {
  sessionStorage.removeItem(TOKEN_KEY);
}

export function hasSessionToken() {
  return Boolean(loadSessionToken());
}
