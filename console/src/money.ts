// How money reads in the console: dollars and cents, everywhere.
//
// Spend is added up to the ten-thousandth of a dollar and budgets are stored to four places, so a raw value
// reads "$31.3457854" or "$30.0000". usd formats a value; moneyText does the same to each amount inside a
// sentence the services wrote, such as a task's status reason, including ones recorded before they rounded.

export function usd(value: string | number | null | undefined): string {
  const n = Number(value ?? 0);
  return `$${(Number.isFinite(n) ? n : 0).toFixed(2)}`;
}

// Each "$<number>" in the text, to two decimal places. An amount already in dollars and cents is left as it is.
export function moneyText(text: string): string;
export function moneyText(text: string | null | undefined): string | null;
export function moneyText(text: string | null | undefined): string | null {
  if (text == null) return null;
  return text.replace(/\$(\d+(?:\.\d+)?)/g, (_, amount: string) => usd(amount));
}
