import type { TopstepTrade } from "../../api/trade";

export type RoundTripTrade = {
  id: string;

  accountId: number;
  contractId: string;

  dir: "Long" | "Short";
  size: number;

  entryTime: string; // ISO
  exitTime: string; // ISO

  entryPrice: number;
  exitPrice: number;

  grossPnl: number | null;
  fees: number;
  netPnl: number | null;

  entryExecIds: number[];
  exitExecId: number;
};

type Lot = {
  qty: number; // + long, - short
  entryPrice: number;
  entryTime: string;
  feeRemaining: number;
  entryExecId: number;
};

function safeNum(n: unknown) {
  const v = Number(n);
  return Number.isFinite(v) ? v : 0;
}

function sideToSignedQty(side: number, size: number) {
  const s = safeNum(size);
  if (s <= 0) return 0;
  if (side === 1) return +s; // buy
  if (side === 0) return -s; // sell
  return 0;
}

function minISO(a: string, b: string) {
  return new Date(a).getTime() <= new Date(b).getTime() ? a : b;
}

export function computeRoundTripsFromExecutions(tradesRaw: TopstepTrade[]): RoundTripTrade[] {
  const execs = (tradesRaw || [])
    .filter((t) => !t.voided)
    .slice()
    .sort((a, b) => {
      const c = a.creationTimestamp.localeCompare(b.creationTimestamp);
      return c !== 0 ? c : a.id - b.id;
    });

  const byContract = new Map<string, { lots: Lot[]; posQty: number }>();
  const out: RoundTripTrade[] = [];

  for (const ex of execs) {
    const qtySigned = sideToSignedQty(ex.side, ex.size);
    if (qtySigned === 0) continue;

    const fee = safeNum(ex.fees);
    const px = safeNum(ex.price);

    const key = ex.contractId || "UNKNOWN";
    if (!byContract.has(key)) byContract.set(key, { lots: [], posQty: 0 });
    const st = byContract.get(key)!;

    // opening (same direction as position, or flat)
    if (st.posQty === 0 || Math.sign(st.posQty) === Math.sign(qtySigned)) {
      // Track fees on the lot so partial exits can apportion commissions back to
      // the fills that opened the position instead of charging the entire exit
      // ticket to the last leg.
      st.lots.push({
        qty: qtySigned,
        entryPrice: px,
        entryTime: ex.creationTimestamp,
        feeRemaining: fee,
        entryExecId: ex.id,
      });
      st.posQty += qtySigned;
      continue;
    }

    // closing or reversing
    const posBefore = st.posQty; // opposite sign from qtySigned here
    const closeQtyAbs = Math.min(Math.abs(qtySigned), Math.abs(posBefore));
    const closeFrac = closeQtyAbs / Math.abs(qtySigned);

    const dir: "Long" | "Short" = posBefore > 0 ? "Long" : "Short";

    let need = closeQtyAbs;

    let entryQty = 0;
    let entryPxQty = 0;
    let entryFees = 0;
    let entryTime = "";
    const entryExecIds: number[] = [];

    while (need > 0 && st.lots.length > 0) {
      const lot = st.lots[0];
      const lotAbs = Math.abs(lot.qty);
      if (lotAbs <= 0) {
        st.lots.shift();
        continue;
      }

      const take = Math.min(need, lotAbs);
      const frac = take / lotAbs;

      const feePart = lot.feeRemaining * frac;
      lot.feeRemaining -= feePart;

      entryFees += feePart;
      entryQty += take;
      entryPxQty += lot.entryPrice * take;

      entryExecIds.push(lot.entryExecId);
      entryTime = entryTime ? minISO(entryTime, lot.entryTime) : lot.entryTime;

      // reduce lot qty toward 0
      lot.qty = lot.qty > 0 ? lot.qty - take : lot.qty + take;
      need -= take;

      if (Math.abs(lot.qty) < 1e-9) st.lots.shift();
    }

    const exitFees = fee * closeFrac;
    const feesTotal = entryFees + exitFees;

    const gross = ex.profitAndLoss === null || ex.profitAndLoss === undefined
      ? null
      : safeNum(ex.profitAndLoss) * closeFrac;

    const net = gross === null ? null : gross - feesTotal;

    const entryPrice = entryQty > 0 ? entryPxQty / entryQty : px;

    out.push({
      id: `${ex.accountId}:${ex.id}:${out.length}`,
      accountId: ex.accountId,
      contractId: key,
      dir,
      size: entryQty,
      entryTime: entryTime || ex.creationTimestamp,
      exitTime: ex.creationTimestamp,
      entryPrice,
      exitPrice: px,
      grossPnl: gross,
      fees: feesTotal,
      netPnl: net,
      entryExecIds: Array.from(new Set(entryExecIds)),
      exitExecId: ex.id,
    });

    // update position, then add new lot if we reversed past flat
    st.posQty += qtySigned;

    const openAfterAbs = Math.max(0, Math.abs(qtySigned) - closeQtyAbs);
    if (openAfterAbs > 0) {
      const openQtySigned = Math.sign(qtySigned) * openAfterAbs;
      const openFee = fee - exitFees;

      st.lots.push({
        qty: openQtySigned,
        entryPrice: px,
        entryTime: ex.creationTimestamp,
        feeRemaining: openFee,
        entryExecId: ex.id,
      });
    }
  }

  return out;
}
