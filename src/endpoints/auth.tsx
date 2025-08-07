export async function loginKey(userName: string, apiKey: string) {
  const res = await fetch('https://api.topstepx.com/api/Auth/loginKey', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ userName, apiKey })
  });
  if (!res.ok) throw new Error(`Login failed ${res.status}`);
  return res.json();
}
