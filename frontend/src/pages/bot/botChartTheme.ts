export interface BotChartThemeColors {
  accent: string;
  secondary: string;
  positive: string;
  negative: string;
  warning: string;
  label: string;
  grid: string;
  border: string;
}

export function readBotChartThemeColors(): BotChartThemeColors {
  const styles = typeof document === "undefined" ? null : window.getComputedStyle(document.documentElement);
  const color = (variable: string, fallback: string, alpha = 1) => {
    const value = styles?.getPropertyValue(variable).trim();
    const channels = value?.match(/[\d.]+/g)?.slice(0, 3);
    if (!channels || channels.length !== 3) {
      return fallback;
    }
    return alpha === 1 ? `rgb(${channels.join(",")})` : `rgba(${channels.join(",")},${alpha})`;
  };
  return {
    accent: color("--theme-accent", "rgb(34,211,238)"),
    secondary: color("--theme-accent-secondary", "rgb(244,114,182)"),
    positive: color("--theme-positive", "rgb(34,197,94)"),
    negative: color("--theme-negative", "rgb(244,63,94)"),
    warning: color("--theme-warning", "rgb(250,204,21)"),
    label: color("--theme-chart-label", "rgb(148,163,184)"),
    grid: color("--theme-chart-grid", "rgba(51,65,85,0.35)", 0.35),
    border: color("--theme-border-strong", "rgba(71,85,105,0.55)", 0.55),
  };
}
