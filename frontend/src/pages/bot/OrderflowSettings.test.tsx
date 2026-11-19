// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { BotConfig } from "../../lib/types";
import { botsApi } from "../../lib/api";
import { OrderflowSettings } from "./OrderflowSettings";

it("keeps unsaved edits through polling and saves the five settings independently", async () => {
  const bot = { id: 40, order_size: 2, max_daily_loss: 800, updated_at: "2026-11-19T10:00:00Z",
    strategy_params: { daily_pnl_limit: 800, imbalance_direction: "following" } } as BotConfig;
  const onChanged = vi.fn();
  const save = vi.spyOn(botsApi, "saveOrderflowSettings").mockResolvedValue({ ...bot, max_daily_loss: 450,
    updated_at: "2026-11-19T11:00:00Z", strategy_params: { ...bot.strategy_params, take_profit_points: 32.5 } });
  const view = render(<OrderflowSettings bot={bot} onChanged={onChanged} />);
  fireEvent.change(screen.getByLabelText("Daily loss limit ($)"), { target: { value: "450" } });
  fireEvent.change(screen.getByLabelText("Take-profit (points)"), { target: { value: "32.5" } });
  view.rerender(<OrderflowSettings bot={{ ...bot }} onChanged={onChanged} />);
  expect(save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
  await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
  expect(save).toHaveBeenCalledWith(40, { contracts: 2, daily_profit_target: 800, daily_loss_limit: 450,
    take_profit_points: 32.5, stop_loss_points: 25, expected_updated_at: bot.updated_at });
  expect(screen.getByRole<HTMLInputElement>("spinbutton", { name: "Daily loss limit ($)" }).value).toBe("450");
  save.mockRestore();
});
