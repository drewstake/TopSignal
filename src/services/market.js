import { HubConnectionBuilder, HttpTransportType, LogLevel } from '@microsoft/signalr';
import { getToken } from '../lib/storage';

// Use direct WSS by default; set VITE_RTC_BASE to proxy through Vite if needed.
const HUB_BASE = import.meta.env.VITE_RTC_BASE || 'https://rtc.topstepx.com';
const MARKET_HUB_URL = `${HUB_BASE}/hubs/market`;

/**
 * Connect to market hub and subscribe to one contract.
 * Options: { contractId, onQuote, onTrade, onDepth, logLevel }
 * Returns: { stop, connection }
 */
export async function connectMarket({ contractId, onQuote, onTrade, onDepth, logLevel = 'none' }) {
  const token = getToken?.();
  if (!token) throw new Error('Not authenticated');
  if (!contractId) throw new Error('No contractId provided');

  const levelMap = {
    none: LogLevel.None,
    error: LogLevel.Error,
    info: LogLevel.Information,
    debug: LogLevel.Debug,
  };
  const level = levelMap[logLevel] ?? LogLevel.None;

  const connection = new HubConnectionBuilder()
    .withUrl(MARKET_HUB_URL, {
      skipNegotiation: true,
      transport: HttpTransportType.WebSockets,
      accessTokenFactory: () => token,
    })
    .configureLogging(level)
    .withAutomaticReconnect()
    .build();

  // Heartbeat tweaks: quicker stall detection/reconnect
  connection.keepAliveIntervalInMilliseconds = 5000;  // ping ~5s
  connection.serverTimeoutInMilliseconds = 20000;     // consider dead at 20s

  if (onQuote) connection.on('GatewayQuote', (cid, data) => { if (cid === contractId) onQuote(data); });
  if (onTrade) connection.on('GatewayTrade', (cid, data) => { if (cid === contractId) onTrade(data); });
  if (onDepth) connection.on('GatewayDepth', (cid, data) => { if (cid === contractId) onDepth(data); });

  await connection.start();

  const subscribe = async () => {
    await Promise.all([
      connection.invoke('SubscribeContractQuotes', contractId),
      connection.invoke('SubscribeContractTrades', contractId),
      // connection.invoke('SubscribeContractMarketDepth', contractId), // opt-in
    ]);
  };

  const unsubscribe = async () => {
    try { await connection.invoke('UnsubscribeContractQuotes', contractId); } catch { /* ignore */ }
    try { await connection.invoke('UnsubscribeContractTrades', contractId); } catch { /* ignore */ }
    try { await connection.invoke('UnsubscribeContractMarketDepth', contractId); } catch { /* ignore */ }
  };

  await subscribe();

  connection.onreconnected(() => {
    // re-subscribe after reconnect
    subscribe().catch(() => { /* ignore */ });
  });

  async function stop() {
    await unsubscribe();
    try { await connection.stop(); } catch { /* ignore */ }
  }

  return { stop, connection };
}
