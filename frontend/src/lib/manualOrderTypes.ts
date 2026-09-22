export interface ManualOrderInput {
  request_id: string;
  side: "BUY" | "SELL";
  quantity: number;
  stop_loss_ticks: number;
  take_profit_ticks: number;
  confirm_live_order_routing: true;
}
export interface ManualOrderResult {
  attempt_id: number;
  request_id: string;
  account_id: number;
  contract_id: string;
  side: "BUY" | "SELL";
  quantity: number;
  stop_loss_ticks: number;
  take_profit_ticks: number;
  status: string;
  execution_status?: string;
  entry_fill_price?: number | null;
  exit_fill_price?: number | null;
  provider_order_id: string | null;
  message: string | null;
}
export interface ManualOrderState {
  account_id: number;
  contract: { id: string; name: string; tick_size: number; tick_value: number };
  max_stop_risk: number;
  positions: { id: string; contract_id: string; type: number; size: number; average_price: number | null }[];
  orders: { order_id: string; contract_id: string; order_type: number; side: number; size: number; parent_order_id: string | null; limit_price: number | null; stop_price: number | null }[];
  recent_attempts: ManualOrderResult[];
}
