export type TopstepCredentials = {
  username: string;
  apiKey: string;
  gatewayUrl?: string;
};

const STORAGE_KEY = "topsignal.topstep.credentials.v1";

export function loadTopstepCreds(): TopstepCredentials | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as TopstepCredentials;
    if (!parsed || typeof parsed.apiKey !== "string") return null;
    return parsed;
  } catch {
    return null;
  }
}

export function saveTopstepCreds(creds: TopstepCredentials) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(creds));
}

export function clearTopstepCreds() {
  localStorage.removeItem(STORAGE_KEY);
}

export function hasTopstepApiKey() {
  const c = loadTopstepCreds();
  return Boolean(c && c.apiKey && c.apiKey.trim().length > 0);
}
