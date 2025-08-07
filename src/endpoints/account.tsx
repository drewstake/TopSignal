export async function searchAccounts(token: string) {
  const res = await fetch('https://api.topstepx.com/api/Account/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
    body: JSON.stringify({ onlyActiveAccounts: true })
  });
  if (!res.ok) throw new Error(`Accounts fetch failed ${res.status}`);
  return res.json();
}
