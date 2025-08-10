import { apiPost } from './http';

/**
 * Retrieve bars (max 20,000 per request).
 * Units: 1=Second, 2=Minute, 3=Hour, 4=Day, 5=Week, 6=Month
 * @param {{
 *  contractId: string,
 *  live: boolean,
 *  startTime: string, // ISO
 *  endTime: string,   // ISO
 *  unit: 1|2|3|4|5|6,
 *  unitNumber: number,
 *  limit: number,
 *  includePartialBar: boolean,
 *  signal?: AbortSignal
 * }} p
 */
export async function retrieveBars(p) {
  const required = ['contractId', 'live', 'startTime', 'endTime', 'unit', 'unitNumber', 'limit', 'includePartialBar'];
  required.forEach((k) => {
    if (p?.[k] === undefined) throw new Error(`${k} is required`);
  });

  const data = await apiPost('/api/History/retrieveBars', p, { signal: p.signal });
  if (!data?.success) throw new Error(data?.errorMessage || `Retrieve bars failed (code ${data?.errorCode})`);
  // Return raw bars array { t, o, h, l, c, v } — normalize here if you want
  return data.bars || [];
}
