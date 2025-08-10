import { useEffect, useRef, useState } from "react";
import { searchOpenOrders, placeOrder, cancelOrder, modifyOrder, ORDER_SIDE, ORDER_TYPE } from "../services/orders";

export function useOrders({ accountId, pollMs = 3000 } = {}) {
  const [openOrders, setOpenOrders] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const timerRef = useRef(null);

  async function refresh() {
    if (!accountId) return;
    setLoading(true);
    try {
      const list = await searchOpenOrders({ accountId: Number(accountId) });
      setOpenOrders(list);
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }

  async function place(p) {
    const orderId = await placeOrder({ ...p, accountId: Number(accountId) });
    // small delay to let backend index order, then refresh
    setTimeout(refresh, 300);
    return orderId;
  }

  async function cancel(orderId) {
    await cancelOrder({ accountId: Number(accountId), orderId });
    setTimeout(refresh, 200);
  }

  async function modify(patch) {
    await modifyOrder({ accountId: Number(accountId), ...patch });
    setTimeout(refresh, 200);
  }

  useEffect(() => {
    if (!accountId) return;
    refresh();
    if (pollMs > 0) {
      timerRef.current = setInterval(refresh, pollMs);
      return () => clearInterval(timerRef.current);
    }
  }, [accountId, pollMs]); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    openOrders,
    loading,
    error,
    refresh,
    place,
    cancel,
    modify,
    ORDER_SIDE,
    ORDER_TYPE,
  };
}
