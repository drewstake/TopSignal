// Simple token storage helpers
const TOKEN_KEY = 'topsignal.token';

export const getToken = () => {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
};

export const setToken = (token) => {
  try { localStorage.setItem(TOKEN_KEY, token); } catch { /* ignore */ }
};

export const clearToken = () => {
  try { localStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ }
};

// Optional stored credentials (remember me)
const CRED_KEY = 'topsignal.creds';

export const getCreds = () => {
  try {
    const raw = localStorage.getItem(CRED_KEY);
    if (!raw) return null;
    const obj = JSON.parse(raw);
    if (obj?.apiKey) obj.apiKey = atob(obj.apiKey);
    return obj;
  } catch {
    return null;
  }
};

export const setCreds = (creds) => {
  try {
    const data = { ...creds };
    if (data.apiKey) data.apiKey = btoa(data.apiKey);
    localStorage.setItem(CRED_KEY, JSON.stringify(data));
  } catch {
    /* ignore */
  }
};

export const clearCreds = () => {
  try { localStorage.removeItem(CRED_KEY); } catch { /* ignore */ }
};
