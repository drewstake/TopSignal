import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { formatIsoDateUtc } from "../../../lib/tradingDay";
import { PnlCalendarCard } from "./PnlCalendarCard";

describe("PnlCalendarCard", () => {
  it("shows daily outcomes and separates fees from commissions", () => {
    const now = new Date();
    const date = formatIsoDateUtc(new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 2)));
    const markup = renderToStaticMarkup(
      <PnlCalendarCard
        accountId={42}
        days={[
          {
            date,
            trade_count: 5,
            win_count: 3,
            loss_count: 1,
            breakeven_count: 1,
            gross_pnl: 220,
            non_commission_fees: 2.22,
            commissions: 1.5,
            fees: 3.72,
            net_pnl: 216.28,
          },
        ]}
        loading={false}
        error={null}
      />,
    );

    expect(markup).toContain("W 3 · L 1 · T 5");
    expect(markup).toContain("Fees $2.22");
    expect(markup).toContain("Comm $1.50");
    expect(markup).toContain("3 wins, 1 losses, 1 breakeven, 5 total trades");
    expect(markup).toContain("Total fees $3.72");
  });
});
