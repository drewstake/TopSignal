export type RiskSource = { id: string; label: string; kind: "trades" | "saved" | "job" };
export type RiskSources = { account_id: number; balance: number | null; sources: RiskSource[] };
export type RiskProbability = { probability: number; simulation_interval: [number, number] };
export type RiskFanPoint = { day: number; p10: number; p50: number; p90: number };
export type RiskScenario = {
  multiplier: number;
  account_breach: RiskProbability;
  daily_limit_hit: RiskProbability | null;
  ending_balance: { p10: number; p50: number; p90: number };
  max_drawdown: { p50: number; p90: number; p95: number };
  fan: RiskFanPoint[];
};
export type RiskForecast = {
  version: string; generated_at: string; source_label: string; source_id: string;
  input_fingerprint: string; parameters: { horizon_days: number; starting_balance: number; loss_buffer: number };
  paths: number; block_days: number;
  sample: { trades: number; active_days: number; first_day: string | null; last_day: string | null; net_pnl: number };
  status: "ready" | "insufficient_data"; warnings: string[]; scenarios: RiskScenario[];
};
