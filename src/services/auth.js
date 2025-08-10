import { setToken, clearToken } from '../lib/storage';

const API_BASE = import.meta.env.VITE_API_BASE || 'https://api.topstepx.com';

/**
 * Login with API key -> stores JWT on success.
 * @param {{ userName: string, apiKey: string }} body
 */
export async function loginWithKey(body) {
  const res = await fetch(`${API_BASE}/api/Auth/loginKey`, {
    method: 'POST',
    headers: {
      'accept': 'text/plain',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    throw new Error(`Auth HTTP ${res.status}`);
  }

  const data = await res.json();
  if (!data?.success || !data?.token) {
    throw new Error(data?.errorMessage || 'Auth failed');
  }

  setToken(data.token);
  return data;
}

export function logout() {
  clearToken();
}
