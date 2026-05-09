import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  APP_THEME_CHANGED_EVENT,
  APP_THEME_STORAGE_KEY,
  APP_THEMES,
  applyAppTheme,
  getAppTheme,
  readStoredAppThemeId,
  selectAppTheme,
} from "./theme";

describe("app theme persistence", () => {
  beforeEach(() => {
    const values = new Map<string, string>();
    const eventTarget = new EventTarget();
    const documentElement = {
      dataset: {} as Record<string, string>,
      style: {} as Record<string, string>,
    };

    vi.stubGlobal("window", {
      addEventListener: eventTarget.addEventListener.bind(eventTarget),
      removeEventListener: eventTarget.removeEventListener.bind(eventTarget),
      dispatchEvent: eventTarget.dispatchEvent.bind(eventTarget),
      localStorage: {
        getItem: (key: string) => (values.has(key) ? values.get(key)! : null),
        setItem: (key: string, value: string) => {
          values.set(key, value);
        },
        removeItem: (key: string) => {
          values.delete(key);
        },
        clear: () => {
          values.clear();
        },
      },
    });
    vi.stubGlobal("document", { documentElement });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("falls back to midnight when storage has an unknown value", () => {
    window.localStorage.setItem(APP_THEME_STORAGE_KEY, "unknown");

    expect(readStoredAppThemeId()).toBe("midnight");
  });

  it("applies the selected theme to the document root", () => {
    applyAppTheme("daylight");

    expect(document.documentElement.dataset.appTheme).toBe("daylight");
    expect(document.documentElement.style.colorScheme).toBe("light");
  });

  it("supports Arctic Glass as a light theme", () => {
    applyAppTheme("arctic");

    expect(document.documentElement.dataset.appTheme).toBe("arctic");
    expect(document.documentElement.style.colorScheme).toBe("light");
  });

  it("registers Neon Noir as a dark selectable palette", () => {
    const theme = getAppTheme("neon-noir");

    expect(theme.name).toBe("Neon Noir");
    expect(theme.colorScheme).toBe("dark");
    expect(theme.swatches).toEqual(["#010104", "#15161f", "#ff2bd6", "#10f0ff", "#9333ff"]);
    expect(APP_THEMES.map((item) => item.id)).toContain("neon-noir");
  });

  it("registers the Oceanic Depth theme as a dark selectable palette", () => {
    const theme = getAppTheme("oceanic-depth");

    expect(theme.name).toBe("Oceanic Depth");
    expect(theme.colorScheme).toBe("dark");
    expect(APP_THEMES.map((item) => item.id)).toContain("oceanic-depth");
  });

  it("registers Matrix Purple with the requested status palette", () => {
    const theme = getAppTheme("matrix-purple");

    expect(theme.name).toBe("Matrix Purple");
    expect(theme.colorScheme).toBe("dark");
    expect(theme.swatches).toEqual(
      expect.arrayContaining(["#9f7aea", "#8ff3d1", "#34d399", "#fb7185", "#fbbf24"]),
    );
    expect(APP_THEMES.map((item) => item.id)).toContain("matrix-purple");
  });

  it("registers Solarized Pro with the requested low-glare palette", () => {
    const theme = getAppTheme("solarized-pro");

    expect(theme.name).toBe("Solarized Pro");
    expect(theme.colorScheme).toBe("dark");
    expect(theme.swatches).toEqual(expect.arrayContaining(["#0b1820", "#163038", "#2dd4d8", "#5b9fd5"]));
    expect(APP_THEMES.map((item) => item.id)).toContain("solarized-pro");
  });

  it("registers Terminal Green with a phosphor trading-terminal palette", () => {
    const theme = getAppTheme("terminal-green");

    expect(theme.name).toBe("Terminal Green");
    expect(theme.colorScheme).toBe("dark");
    expect(theme.swatches).toEqual(expect.arrayContaining(["#010403", "#08120f", "#5fff8b", "#ff4d4d"]));
    expect(APP_THEMES.map((item) => item.id)).toContain("terminal-green");
  });

  it("stores the selected theme and emits a browser event", () => {
    const listener = vi.fn((event: Event) => {
      expect((event as CustomEvent).detail).toEqual({ themeId: "ember" });
    });
    window.addEventListener(APP_THEME_CHANGED_EVENT, listener);

    selectAppTheme("ember");

    expect(window.localStorage.getItem(APP_THEME_STORAGE_KEY)).toBe("ember");
    expect(document.documentElement.dataset.appTheme).toBe("ember");
    expect(listener).toHaveBeenCalledTimes(1);
  });
});
