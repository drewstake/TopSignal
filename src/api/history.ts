import { topstepPost } from "./topstepClient";

export type TopstepBar = {
  t: string; // timestamp
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
};

export type RetrieveBarsResponse = {
  bars: TopstepBar[];
  success: boolean;
  errorCode: number;
  errorMessage: string | null;
};

export async function retrieveBars(args: {
  contractId: string;
  live: boolean;
  startTime: string;
  endTime: string;
  unit: number; // 1 sec, 2 min, 3 hour...
  unitNumber: number;
  limit: number;
  includePartialBar: boolean;
}) {
  return topstepPost<RetrieveBarsResponse>("/api/History/retrieveBars", {
    contractId: args.contractId,
    live: args.live,
    startTime: args.startTime,
    endTime: args.endTime,
    unit: args.unit,
    unitNumber: args.unitNumber,
    limit: args.limit,
    includePartialBar: args.includePartialBar,
  });
}
