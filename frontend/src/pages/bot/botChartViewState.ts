export interface BotChartViewStateInput {
  hasBot: boolean;
  loading: boolean;
  error: string | null;
  candleCount: number;
}

export type BotChartViewState =
  | { kind: "ready"; message: null }
  | { kind: "loading" | "empty" | "error" | "unselected"; message: string };

export function resolveBotChartViewState(input: BotChartViewStateInput): BotChartViewState {
  if (!input.hasBot) {
    return { kind: "unselected", message: "Select or save a bot to load its ProjectX candles." };
  }
  if (input.loading && input.candleCount === 0) {
    return { kind: "loading", message: "Loading candles" };
  }
  if (input.error) {
    return { kind: "error", message: input.error };
  }
  if (input.candleCount === 0) {
    return { kind: "empty", message: "No candles returned for this chart window." };
  }
  return { kind: "ready", message: null };
}
