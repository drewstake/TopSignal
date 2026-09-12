import type { SVGProps } from "react";

const paths = {
  dashboard: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  accounts: "M4 7h16v13H4z M7 7V4h10v3 M4 11h16 M15 15h2",
  trades: "M4 7h15m-4-4 4 4-4 4 M20 17H5m4-4-4 4 4 4",
  expenses: "M6 3h12v18l-3-2-3 2-3-2-3 2z M9 8h6 M9 12h6",
  bot: "M5 7h14v12H5z M12 3v4 M9 12v2 M15 12v2 M2 11v4 M22 11v4",
  themes: "M12 3a9 9 0 1 0 0 18h1a2 2 0 0 0 1-4c-1-1 0-3 2-3h2c5 0 3-11-6-11z M7 10h.01 M10 6h.01 M15 7h.01",
  chart: "M4 3v17h17 M8 14l4-5 4 3 5-7",
  arrow: "M5 12h14 M14 7l5 5-5 5",
  chevron: "m8 10 4 4 4-4",
  target: "M21 12a9 9 0 1 1-9-9 M17 12a5 5 0 1 1-5-5 M12 12l9-9 M16 3h5v5",
  shield: "m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z M8 12l3 3 5-6",
  pulse: "M2 12h5l3-8 4 16 3-8h5",
  clock: "M12 8v5l3 2 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
  link: "m10 14 4-4 M8 16l-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0 M16 8l1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0",
  calendar: "M4 5h16v16H4z M8 3v4 M16 3v4 M4 10h16 M8 14h2 M14 14h2",
} as const;

export type IconName = keyof typeof paths;

export function Icon({ name, ...props }: SVGProps<SVGSVGElement> & { name: IconName }) {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}><path d={paths[name]} /></svg>;
}
