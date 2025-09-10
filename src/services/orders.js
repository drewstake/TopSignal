import { apiPost } from './http';

// --- enums/helpers (best-effort maps; fall back to raw code) ---
export const ORDER_SIDE = { 0: 'Buy', 1: 'Sell' };              // 0=Bid/Buy, 1=Ask/Sell
export const ORDER_TYPE = { 1: 'Limit', 2: 'Market', 4: 'Stop', 5: 'TrailingStop', 6: 'JoinBid', 7: 'JoinAsk' };
// Common status guesses used by many gateways; adjust if docs specify differently:
export const ORDER_STATUS = {
  0: 'New',
  1: 'Working',
  2: 'Filled',
  3: 'Cancelled',
  4: 'Rejected',
  5: 'PartiallyFilled',
  6: 'Expired',
};

const mapOrCode = (map, code) => map?.[code] ?? String(code);

/** Convert API order to a UI-friendly shape */
export function normalizeOrder(o) {
  const order = {
    id: o.id,
    accountId: o.accountId,
    contractId: o.contractId,
    symbolId: o.symbolId ?? null,
    createdAt: o.creationTimestamp,
    updatedAt: o.updateTimestamp ?? o.creationTimestamp,
    statusCode: o.status,
    status: mapOrCode(ORDER_STATUS, o.status),
    typeCode: o.type,
    type: mapOrCode(ORDER_TYPE, o.type),
    sideCode: o.side,
    side: mapOrCode(ORDER_SIDE, o.side),
    size: o.size,
    limitPrice: o.limitPrice ?? null,
    stopPrice: o.stopPrice ?? null,
    trailPrice: o.trailPrice ?? null,
    fillVolume: o.fillVolume ?? null,
    filledPrice: o.filledPrice ?? null,
    customTag: o.customTag ?? null,
  };

  // If the API reports a market order as still "Working" but it has
  // already been completely filled, force the status to "Filled" so that
  // the UI doesn't continue to treat it as an open order.
  if (
    order.typeCode === 2 &&
    order.fillVolume !== null &&
    order.fillVolume >= order.size &&
    order.status !== ORDER_STATUS[2]
  ) {
    order.statusCode = 2;
    order.status = ORDER_STATUS[2];
  }

  return order;
}

/**
 * Search for orders in a time window.
 * @param {{accountId:number, startTimestamp:string, endTimestamp?:string, signal?:AbortSignal}} p
 * @returns {Promise<Array<ReturnType<typeof normalizeOrder>>>}
 */
export async function searchOrders({ accountId, startTimestamp, endTimestamp, signal } = {}) {
  if (!accountId || !startTimestamp) throw new Error('accountId and startTimestamp are required');
  const data = await apiPost('/api/Order/search', { accountId, startTimestamp, endTimestamp }, { signal });
  if (!data?.success) throw new Error(data?.errorMessage || `Order search failed (code ${data?.errorCode})`);
  return (data.orders || []).map(normalizeOrder);
}

/**
 * Search for open orders.
 * @param {{accountId:number, signal?:AbortSignal}} p
 * @returns {Promise<Array<ReturnType<typeof normalizeOrder>>>}
 */
export async function searchOpenOrders({ accountId, signal } = {}) {
  if (!accountId) throw new Error('accountId is required');
  const data = await apiPost('/api/Order/searchOpen', { accountId }, { signal });
  if (!data?.success) throw new Error(data?.errorMessage || `Open order search failed (code ${data?.errorCode})`);
  const orders = (data.orders || []).map(normalizeOrder);
  // Filter out any orders that are no longer active. Some gateways may
  // briefly report recently filled market orders as "Working"; because
  // `normalizeOrder` fixes their status to "Filled", this filter keeps the
  // open orders list consistent with the actual state on the exchange.
  return orders.filter((o) => ![2, 3, 4, 6].includes(o.statusCode));
}

/**
 * Place an order.
 * @param {{
 *  accountId:number,
 *  contractId:string,
 *  type:1|2|4|5|6|7,
 *  side:0|1,
 *  size:number,
 *  limitPrice?:number|null,
 *  stopPrice?:number|null,
 *  trailPrice?:number|null,
 *  customTag?:string|null,
 *  linkedOrderId?:number|null,
 *  signal?:AbortSignal
 * }} p
 * @returns {Promise<number>} orderId
 */
export async function placeOrder(p) {
  const {
    accountId, contractId, type, side, size,
    limitPrice = null, stopPrice = null, trailPrice = null,
    customTag = null, linkedOrderId = null, signal,
  } = p || {};
  ['accountId', 'contractId', 'type', 'side', 'size'].forEach((k) => {
    if (p?.[k] === undefined || p?.[k] === null) throw new Error(`${k} is required`);
  });

  const data = await apiPost('/api/Order/place', {
    accountId, contractId, type, side, size,
    limitPrice, stopPrice, trailPrice, customTag, linkedOrderId,
  }, { signal });

  if (!data?.success) throw new Error(data?.errorMessage || `Place order failed (code ${data?.errorCode})`);
  return data.orderId;
}

/**
 * Cancel an order.
 * @param {{accountId:number, orderId:number, signal?:AbortSignal}} p
 */
export async function cancelOrder({ accountId, orderId, signal } = {}) {
  if (!accountId || !orderId) throw new Error('accountId and orderId are required');
  const data = await apiPost('/api/Order/cancel', { accountId, orderId }, { signal });
  if (!data?.success) throw new Error(data?.errorMessage || `Cancel order failed (code ${data?.errorCode})`);
  return true;
}

/**
 * Modify an open order.
 * Send only fields you want to change.
 * @param {{accountId:number, orderId:number, size?:number, limitPrice?:number|null, stopPrice?:number|null, trailPrice?:number|null, signal?:AbortSignal}} p
 */
export async function modifyOrder({ accountId, orderId, size, limitPrice, stopPrice, trailPrice, signal } = {}) {
  if (!accountId || !orderId) throw new Error('accountId and orderId are required');

  const body = { accountId, orderId };
  if (size !== undefined) body.size = size;
  if (limitPrice !== undefined) body.limitPrice = limitPrice;
  if (stopPrice !== undefined) body.stopPrice = stopPrice;
  if (trailPrice !== undefined) body.trailPrice = trailPrice;

  const data = await apiPost('/api/Order/modify', body, { signal });
  if (!data?.success) throw new Error(data?.errorMessage || `Modify order failed (code ${data?.errorCode})`);
  return true;
}
