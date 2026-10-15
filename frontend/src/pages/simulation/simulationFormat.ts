// Intl formatters are expensive to construct. Reuse them across every market
// publication instead of constructing hundreds through toLocaleString.
const integer = new Intl.NumberFormat('en-US');
const currency = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });
export const quantity = (value: number) => integer.format(value);
export const dollars = (value: number) => currency.format(value);
