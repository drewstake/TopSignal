export const riskMoney = (value: number) => value.toLocaleString("en-US", {
  style: "currency", currency: "USD", maximumFractionDigits: 0,
});
