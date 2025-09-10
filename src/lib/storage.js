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
