export async function searchTrades(token: string, accountId: number) {
  const res = await fetch('https://api.topstepx.com/api/Trade/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
    body: JSON.stringify({ accountId })
  });
  if (!res.ok) throw new Error(`Trades fetch failed ${res.status}`);
  return res.json();
}
