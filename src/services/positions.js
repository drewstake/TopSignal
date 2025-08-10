import { apiPost } from './http';

export function normalizePosition(p) {
  return {
    id: p.id,
    accountId: p.accountId,
    contractId: p.contractId,
    createdAt: p.creationTimestamp,
    type: p.type,         // (if enum exists, map here later)
    size: p.size,
    averagePrice: p.averagePrice,
  };
}

/**
 * Search for open positions.
 * @param {{accountId:number, signal?:AbortSignal}} p
 */
export async function searchOpenPositions({ accountId, signal } = {}) {
  if (!accountId) throw new Error('accountId is required');
  const data = await apiPost('/api/Position/searchOpen', { accountId }, { signal });
  if (!data?.success) throw new Error(data?.errorMessage || `Open positions failed (code ${data?.errorCode})`);
  return (data.positions || []).map(normalizePosition);
}

/**
 * Close a position for a contract.
 * @param {{accountId:number, contractId:string, signal?:AbortSignal}} p
 */
export async function closeContract({ accountId, contractId, signal } = {}) {
  if (!accountId || !contractId) throw new Error('accountId and contractId are required');
  const data = await apiPost('/api/Position/closeContract', { accountId, contractId }, { signal });
  if (!data?.success) throw new Error(data?.errorMessage || `Close position failed (code ${data?.errorCode})`);
  return true;
}

/**
 * Partially close a position for a contract.
 * @param {{accountId:number, contractId:string, size:number, signal?:AbortSignal}} p
 */
export async function partialCloseContract({ accountId, contractId, size, signal } = {}) {
  if (!accountId || !contractId || !size) throw new Error('accountId, contractId, and size are required');
  const data = await apiPost('/api/Position/partialCloseContract', { accountId, contractId, size }, { signal });
  if (!data?.success) throw new Error(data?.errorMessage || `Partial close failed (code ${data?.errorCode})`);
  return true;
}
