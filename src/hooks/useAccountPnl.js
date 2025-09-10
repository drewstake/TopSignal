import { useEffect, useRef, useState } from 'react';
import { getAccountPnl } from '../services/pnl';

export function useAccountPnl({ accountId, pollMs = 5000 } = {}) {
  const [unrealized, setUnrealized] = useState(0);
  const [realized, setRealized] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const t = useRef(null);

  async function refresh() {
    if (!accountId) return;
    setLoading(true);
    try {
      const res = await getAccountPnl({ accountId: Number(accountId) });
      setUnrealized(res.unrealizedPnl);
      setRealized(res.realizedPnl);
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!accountId) return;
    refresh();
    if (pollMs > 0) {
      t.current = setInterval(refresh, pollMs);
      return () => clearInterval(t.current);
    }
  }, [accountId, pollMs]); // eslint-disable-line react-hooks/exhaustive-deps

  return { unrealized, realized, loading, error, refresh };
}
