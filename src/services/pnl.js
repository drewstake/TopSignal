import { apiPost } from './http';

/**
 * Fetch account PnL summary.
 * @param {{accountId:number, signal?:AbortSignal}} p
 * @returns {Promise<{unrealizedPnl:number, realizedPnl:number}>}
 */
export async function getAccountPnl({ accountId, signal } = {}) {
  if (!accountId) throw new Error('accountId is required');
  const data = await apiPost('/api/Account/getPnl', { accountId }, { signal });
  if (!data?.success) throw new Error(data?.errorMessage || `PnL fetch failed (code ${data?.errorCode})`);
  return {
    unrealizedPnl: data.unrealizedPnl ?? 0,
    realizedPnl: data.realizedPnl ?? 0,
  };
}
