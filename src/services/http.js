import { getToken } from '../lib/storage';

const API_BASE = import.meta.env.VITE_API_BASE || 'https://api.topstepx.com';

/**
 * POST helper with JWT + simple 429 backoff.
 * @param {string} path - e.g. "/api/Order/place"
 * @param {object} body
 * @param {{signal?:AbortSignal, retries?:number}} opts
 * @returns {Promise<any>}
 */
export async function apiPost(path, body, { signal, retries = 2 } = {}) {
  const token = getToken();
  if (!token) throw new Error('Not authenticated');

  let attempt = 0;
  let lastErr;

  while (attempt <= retries) {
    try {
      const res = await fetch(`${API_BASE}${path}`, {
        method: 'POST',
        headers: {
          accept: 'text/plain',
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(body ?? {}),
        signal,
      });

      if (res.status === 429 && attempt < retries) {
        const retryAfter = res.headers.get('Retry-After');
        const ms = retryAfter ? Number(retryAfter) * 1000 : 500 * 2 ** attempt;
        await new Promise((r) => setTimeout(r, ms + Math.random() * 200));
        attempt++;
        continue;
      }

      if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new Error(`HTTP ${res.status}${text ? `: ${text}` : ''}`);
      }

      return res.json();
    } catch (err) {
      if (err?.name === 'AbortError') throw err;
      lastErr = err;
      if (attempt >= retries) break;
      await new Promise((r) => setTimeout(r, 500 * 2 ** attempt));
      attempt++;
    }
  }

  throw lastErr || new Error('Request failed');
}
