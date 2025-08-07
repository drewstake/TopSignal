import { HubConnectionBuilder, HttpTransportType } from '@microsoft/signalr';

export function createUserHub(token: string, accountId: number) {
  const url = `https://rtc.topstepx.com/hubs/user?access_token=${token}`;
  return new HubConnectionBuilder()
    .withUrl(url, { skipNegotiation: true, transport: HttpTransportType.WebSockets })
    .withAutomaticReconnect()
    .build();
}

export function createMarketHub(token: string, contractId: string) {
  const url = `https://rtc.topstepx.com/hubs/market?access_token=${token}`;
  return new HubConnectionBuilder()
    .withUrl(url, { skipNegotiation: true, transport: HttpTransportType.WebSockets })
    .withAutomaticReconnect()
    .build();
}
