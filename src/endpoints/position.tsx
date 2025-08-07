export async function searchOpenPositions(token: string, accountId: number) {
  const res = await fetch('https://api.topstepx.com/api/Position/searchOpen', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
    body: JSON.stringify({ accountId })
  });
  if (!res.ok) throw new Error(`Positions fetch failed ${res.status}`);
  return res.json();
}
