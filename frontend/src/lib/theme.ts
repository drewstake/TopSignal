export const APP_THEME_STORAGE_KEY = "topsignal.theme";
export const APP_THEME_CHANGED_EVENT = "topsignal-theme-changed";

export type AppThemeId =
  | "midnight"
  | "daylight"
  | "market"
  | "ember"
  | "terminal-green"
  | "bloomberg-dark"
  | "arctic"
  | "paper-trading"
  | "neon-noir"
  | "oceanic-depth"
  | "matrix-purple"
  | "solarized-pro";

export interface AppTheme {
  id: AppThemeId;
  name: string;
  description: string;
  colorScheme: "dark" | "light";
  swatches: string[];
  tags: string[];
}

export interface AppThemeChangedDetail {
  themeId: AppThemeId;
}

export const APP_THEMES: AppTheme[] = [
  {
    id: "midnight",
    name: "Midnight",
    description: "The original high-contrast trading workspace with cyan execution accents.",
    colorScheme: "dark",
    swatches: ["#020617", "#0f172a", "#22d3ee", "#8b5cf6"],
    tags: ["Default", "Dark"],
  },
  {
    id: "daylight",
    name: "Daylight",
    description: "A bright review mode for scanning reports and journals in well-lit rooms.",
    colorScheme: "light",
    swatches: ["#f8fafc", "#ffffff", "#0e7490", "#4f46e5"],
    tags: ["Light", "Review"],
  },
  {
    id: "market",
    name: "Market",
    description: "Deep green surfaces with cool blue action states and amber risk markers.",
    colorScheme: "dark",
    swatches: ["#031713", "#0b2a24", "#34d399", "#38bdf8"],
    tags: ["Dark", "Focus"],
  },
  {
    id: "ember",
    name: "Ember",
    description: "A warm graphite theme with copper highlights for evening sessions.",
    colorScheme: "dark",
    swatches: ["#181512", "#29231d", "#fb923c", "#38bdf8"],
    tags: ["Dark", "Warm"],
  },
  {
    id: "terminal-green",
    name: "Terminal Green",
    description: "A sharp black trading terminal with phosphor green signals, mint text, amber risk, and red loss states.",
    colorScheme: "dark",
    swatches: ["#010403", "#08120f", "#5fff8b", "#ff4d4d"],
    tags: ["Dark", "Terminal"],
  },
  {
    id: "bloomberg-dark",
    name: "Bloomberg Dark",
    description:
      "A dense black market terminal with charcoal panels, amber execution accents, blue signals, and high-contrast status colors.",
    colorScheme: "dark",
    swatches: ["#000000", "#131416", "#ff9f1c", "#2997ff", "#00c853", "#ff3b30", "#ffd60a"],
    tags: ["Dark", "Terminal"],
  },
  {
    id: "arctic",
    name: "Arctic Glass",
    description: "Icy blue-gray workspace surfaces with frosted panels, steel borders, and glacier accents.",
    colorScheme: "light",
    swatches: ["#e8f1f8", "#f8fbff", "#0284c7", "#4f46e5"],
    tags: ["Light", "Premium"],
  },
  {
    id: "paper-trading",
    name: "Paper Trading",
    description: "An editorial daylight workspace with paper-white panels, ink-blue type, cobalt actions, and restrained trading states.",
    colorScheme: "light",
    swatches: ["#f6f3ec", "#ffffff", "#102033", "#2457d6", "#0f9f9a"],
    tags: ["Light", "Editorial"],
  },
  {
    id: "neon-noir",
    name: "Neon Noir",
    description: "A black and graphite cyberpunk workspace with magenta action states, cyan signals, and violet highlights.",
    colorScheme: "dark",
    swatches: ["#010104", "#15161f", "#ff2bd6", "#10f0ff", "#9333ff"],
    tags: ["Dark", "Cyberpunk"],
  },
  {
    id: "oceanic-depth",
    name: "Oceanic Depth",
    description: "Deep navy surfaces with aqua execution accents, seafoam secondary states, and sharp status colors.",
    colorScheme: "dark",
    swatches: ["#020b16", "#061827", "#22d3ee", "#7dd3c7"],
    tags: ["Dark", "Calm"],
  },
  {
    id: "matrix-purple",
    name: "Matrix Purple",
    description: "Near-black eggplant surfaces with restrained violet accents, mint signals, green gains, rose losses, and amber warnings.",
    colorScheme: "dark",
    swatches: ["#0b0611", "#171020", "#4b405d", "#9f7aea", "#8ff3d1", "#34d399", "#fb7185", "#fbbf24"],
    tags: ["Dark", "Futuristic"],
  },
  {
    id: "solarized-pro",
    name: "Solarized Pro",
    description: "A calm blue-gray workspace with blue-green panels, cyan action accents, and low-glare status colors.",
    colorScheme: "dark",
    swatches: ["#0b1820", "#163038", "#2dd4d8", "#5b9fd5"],
    tags: ["Dark", "Low Glare"],
  },
];

export const DEFAULT_APP_THEME_ID: AppThemeId = "midnight";

export function isAppThemeId(value: unknown): value is AppThemeId {
  return APP_THEMES.some((theme) => theme.id === value);
}

export function getAppTheme(themeId: AppThemeId): AppTheme {
  return APP_THEMES.find((theme) => theme.id === themeId) ?? APP_THEMES[0];
}

export function readStoredAppThemeId(): AppThemeId {
  if (typeof window === "undefined") {
    return DEFAULT_APP_THEME_ID;
  }

  try {
    const rawValue = window.localStorage.getItem(APP_THEME_STORAGE_KEY);
    return isAppThemeId(rawValue) ? rawValue : DEFAULT_APP_THEME_ID;
  } catch {
    return DEFAULT_APP_THEME_ID;
  }
}

export function applyAppTheme(themeId: AppThemeId) {
  if (typeof document === "undefined") {
    return;
  }

  const theme = getAppTheme(themeId);
  document.documentElement.dataset.appTheme = theme.id;
  document.documentElement.style.colorScheme = theme.colorScheme;
}

export function writeStoredAppThemeId(themeId: AppThemeId) {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(APP_THEME_STORAGE_KEY, themeId);
  } catch {
    // Theme selection should not break the app when storage is unavailable.
  }
}

export function selectAppTheme(themeId: AppThemeId) {
  writeStoredAppThemeId(themeId);
  applyAppTheme(themeId);

  if (typeof window === "undefined") {
    return;
  }

  window.dispatchEvent(
    new CustomEvent<AppThemeChangedDetail>(APP_THEME_CHANGED_EVENT, {
      detail: { themeId },
    }),
  );
}

export function initializeAppTheme() {
  applyAppTheme(readStoredAppThemeId());
}
