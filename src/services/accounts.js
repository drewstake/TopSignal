import { getToken } from '../lib/storage';

const API_BASE = import.meta.env.VITE_API_BASE || 'https://api.topstepx.com';

/**
 * Search accounts (requires JWT).
 * Returns only active accounts when onlyActiveAccounts === true.
 */
export async function searchAccounts({ onlyActiveAccounts = true } = {}) {
  const token = getToken();
  if (!token) throw new Error('Not authenticated');

  const res = await fetch(`${API_BASE}/api/Account/search`, {
    method: 'POST',
    headers: {
      accept: 'text/plain',
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    // IMPORTANT: send the flag in the JSON body
    body: JSON.stringify({ onlyActiveAccounts }),
  });

  if (!res.ok) throw new Error(`Accounts HTTP ${res.status}`);

  const data = await res.json();
  if (!data?.success) throw new Error(data?.errorMessage || 'Account search failed');

  // Normalize + hard filter as a safety net
  let list = (data.accounts || []).map(a => ({
    id: a.id,
    name: a.name,
    balance: a.balance,
    canTrade: a.canTrade,
    isVisible: a.isVisible,
  }));

  if (onlyActiveAccounts) {
    list = list.filter(a => a.canTrade && a.isVisible);
  }

  return list;
}
