import { topstepPost } from "./topstepClient";

export type OrderSide = 0 | 1; // 0 = buy/bid, 1 = sell/ask
export type OrderType = 1 | 2 | 4 | 5 | 6 | 7;

export type BracketConfig = {
  ticks: number;
  type: OrderType;
};

export type PlaceOrderRequest = {
  accountId: number;
  contractId: string;
  type: OrderType;
  side: OrderSide;
  size: number;
  limitPrice?: number | null;
  stopPrice?: number | null;
  trailPrice?: number | null;
  customTag?: string | null;
  stopLossBracket?: BracketConfig | null;
  takeProfitBracket?: BracketConfig | null;
};

export type PlaceOrderResponse = {
  orderId?: number;
  success: boolean;
  errorCode: number;
  errorMessage: string | null;
};

type BaseOrderResponse = {
  success: boolean;
  errorCode: number;
  errorMessage: string | null;
};

export async function placeOrder(request: PlaceOrderRequest) {
  return topstepPost<PlaceOrderResponse>("/api/Order/place", {
    accountId: request.accountId,
    contractId: request.contractId,
    type: request.type,
    side: request.side,
    size: request.size,
    limitPrice: request.limitPrice ?? null,
    stopPrice: request.stopPrice ?? null,
    trailPrice: request.trailPrice ?? null,
    customTag: request.customTag ?? null,
    stopLossBracket: request.stopLossBracket ?? null,
    takeProfitBracket: request.takeProfitBracket ?? null,
  });
}

export async function cancelOrder(args: { accountId: number; orderId: number }) {
  return topstepPost<BaseOrderResponse>("/api/Order/cancel", args);
}

export async function modifyOrder(args: {
  accountId: number;
  orderId: number;
  size?: number | null;
  limitPrice?: number | null;
  stopPrice?: number | null;
  trailPrice?: number | null;
}) {
  return topstepPost<BaseOrderResponse>("/api/Order/modify", {
    accountId: args.accountId,
    orderId: args.orderId,
    size: args.size ?? null,
    limitPrice: args.limitPrice ?? null,
    stopPrice: args.stopPrice ?? null,
    trailPrice: args.trailPrice ?? null,
  });
}

export type OpenOrdersResponse = {
  orders: Array<{
    id: number;
    accountId: number;
    contractId: string;
    creationTimestamp: string;
    updateTimestamp: string;
    status: number;
    type: OrderType;
    side: OrderSide;
    size: number;
    limitPrice: number | null;
    stopPrice: number | null;
    filledPrice: number | null;
  }>;
  success: boolean;
  errorCode: number;
  errorMessage: string | null;
};

export async function searchOpenOrders(args: { accountId: number }) {
  return topstepPost<OpenOrdersResponse>("/api/Order/searchOpen", args);
}
