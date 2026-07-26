/// <reference types="node" />

import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { APP_THEMES } from "./theme";

type Rgb = readonly [number, number, number];

const globalsSource = readFileSync(new URL("../styles/globals.css", import.meta.url), "utf8");
const tailwindConfigSource = readFileSync(new URL("../../tailwind.config.js", import.meta.url), "utf8");

const rootBlockPattern = /:root(?:\[data-app-theme="([^"]+)"\])?\s*\{([\s\S]*?)\}/g;
const declarationPattern = /(--theme-[\w-]+):\s*([^;]+);/g;

function parseDeclarations(block: string): Map<string, string> {
  const declarations = new Map<string, string>();
  for (const match of block.matchAll(declarationPattern)) {
    declarations.set(match[1], match[2].trim());
  }
  return declarations;
}

function parseThemeVariables() {
  let base = new Map<string, string>();
  const overrides = new Map<string, Map<string, string>>();

  for (const match of globalsSource.matchAll(rootBlockPattern)) {
    const themeId = match[1];
    if (themeId) {
      overrides.set(themeId, parseDeclarations(match[2]));
    } else if (base.size === 0) {
      base = parseDeclarations(match[2]);
    }
  }

  if (base.size === 0) {
    throw new Error(`Could not parse the base theme from ${JSON.stringify(globalsSource.slice(0, 160))}`);
  }

  return { base, overrides };
}

function resolveRgb(variables: ReadonlyMap<string, string>, property: string, seen = new Set<string>()): Rgb {
  if (seen.has(property)) {
    throw new Error(`Circular theme variable reference: ${[...seen, property].join(" -> ")}`);
  }

  const rawValue = variables.get(property);
  if (!rawValue) {
    throw new Error(`Missing theme variable ${property}; found ${[...variables.keys()].join(", ")}`);
  }

  const alias = rawValue.match(/^var\((--theme-[\w-]+)\)$/);
  if (alias) {
    return resolveRgb(variables, alias[1], new Set([...seen, property]));
  }

  const channels = rawValue.match(/^(\d+)\s+(\d+)\s+(\d+)$/);
  if (!channels) {
    throw new Error(`Expected an RGB triplet for ${property}, received ${rawValue}`);
  }

  return [Number(channels[1]), Number(channels[2]), Number(channels[3])];
}

function relativeLuminance(color: Rgb) {
  const [red, green, blue] = color.map((channel) => {
    const normalized = channel / 255;
    return normalized <= 0.04045
      ? normalized / 12.92
      : ((normalized + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

function contrastRatio(foreground: Rgb, background: Rgb) {
  const foregroundLuminance = relativeLuminance(foreground);
  const backgroundLuminance = relativeLuminance(background);
  return (
    (Math.max(foregroundLuminance, backgroundLuminance) + 0.05) /
    (Math.min(foregroundLuminance, backgroundLuminance) + 0.05)
  );
}

function blend(foreground: Rgb, background: Rgb, opacity: number): Rgb {
  return foreground.map(
    (channel, index) => channel * opacity + background[index] * (1 - opacity),
  ) as unknown as Rgb;
}

const { base, overrides } = parseThemeVariables();
const compactBackgroundProperties = ["--theme-bg", "--theme-surface", "--theme-surface-raised"] as const;
const compactSmallTextProperties = [
  "--theme-text",
  "--theme-muted-text",
  "--theme-chart-text",
  "--theme-accent-text",
  "--theme-positive-text",
  "--theme-negative-text",
] as const;
const compactGraphicalProperties = [
  "--theme-accent",
  "--theme-warning",
  "--theme-positive",
  "--theme-negative",
] as const;
const compactSoftBackgrounds = [
  { label: "10% accent soft background", property: "--theme-accent", opacity: 0.1 },
  { label: "15% accent selected background", property: "--theme-accent", opacity: 0.15 },
  { label: "10% warning soft background", property: "--theme-warning", opacity: 0.1 },
  { label: "25% positive soft background", property: "--theme-positive-soft", opacity: 0.25 },
  { label: "25% negative soft background", property: "--theme-negative-soft", opacity: 0.25 },
  { label: "60% raised-surface background", property: "--theme-surface-raised", opacity: 0.6 },
] as const;

function variablesForTheme(themeId: string) {
  const variables = new Map(base);
  overrides.get(themeId)?.forEach((value, property) => variables.set(property, value));
  return variables;
}

function expectContrast(
  themeId: string,
  label: string,
  foreground: Rgb,
  background: Rgb,
  minimum: number,
) {
  expect(
    contrastRatio(foreground, background),
    `${themeId} ${label}`,
  ).toBeGreaterThanOrEqual(minimum);
}

describe("Compact dashboard semantic theme tokens", () => {
  it("exposes dedicated Tailwind utilities without changing the existing status-color utilities", () => {
    expect(tailwindConfigSource).toContain('positive: "rgb(var(--theme-positive) / <alpha-value>)"');
    expect(tailwindConfigSource).toContain('negative: "rgb(var(--theme-negative) / <alpha-value>)"');
    expect(tailwindConfigSource).toContain('accent: "rgb(var(--theme-accent) / <alpha-value>)"');
    expect(tailwindConfigSource).toContain('warning: "rgb(var(--theme-warning) / <alpha-value>)"');
    expect(tailwindConfigSource).toContain('"muted-text": "rgb(var(--theme-muted-text) / <alpha-value>)"');
    expect(tailwindConfigSource).toContain('"chart-text": "rgb(var(--theme-chart-text) / <alpha-value>)"');
    expect(tailwindConfigSource).toContain('"accent-text": "rgb(var(--theme-accent-text) / <alpha-value>)"');
    expect(tailwindConfigSource).toContain('"positive-soft": "rgb(var(--theme-positive-soft) / <alpha-value>)"');
    expect(tailwindConfigSource).toContain('"positive-text": "rgb(var(--theme-positive-text) / <alpha-value>)"');
    expect(tailwindConfigSource).toContain('"negative-soft": "rgb(var(--theme-negative-soft) / <alpha-value>)"');
    expect(tailwindConfigSource).toContain('"negative-text": "rgb(var(--theme-negative-text) / <alpha-value>)"');
    expect(tailwindConfigSource).toContain('focus: "rgb(var(--theme-focus) / <alpha-value>)"');
  });

  APP_THEMES.forEach((theme) => {
    it(`${theme.name} keeps every Compact foreground legible on dashboard surfaces`, () => {
      const variables = variablesForTheme(theme.id);

      compactSmallTextProperties.forEach((property) => {
        const foreground = resolveRgb(variables, property);
        compactBackgroundProperties.forEach((backgroundProperty) => {
          const background = resolveRgb(variables, backgroundProperty);
          expectContrast(
            theme.id,
            `${property} small text on ${backgroundProperty}`,
            foreground,
            background,
            4.5,
          );
        });
      });
    });

    it(`${theme.name} keeps Compact semantic marks, soft states, and focus indicators legible`, () => {
      const variables = variablesForTheme(theme.id);

      const text = resolveRgb(variables, "--theme-text");
      const muted = resolveRgb(variables, "--theme-muted-text");
      const positiveText = resolveRgb(variables, "--theme-positive-text");
      const negativeText = resolveRgb(variables, "--theme-negative-text");
      const focus = resolveRgb(variables, "--theme-focus");

      compactBackgroundProperties.forEach((backgroundProperty) => {
        const background = resolveRgb(variables, backgroundProperty);
        compactGraphicalProperties.forEach((property) => {
          expectContrast(
            theme.id,
            `${property} graphical mark on ${backgroundProperty}`,
            resolveRgb(variables, property),
            background,
            3,
          );
        });
        expectContrast(theme.id, `focus indicator on ${backgroundProperty}`, focus, background, 3);

        compactSoftBackgrounds.forEach(({ label, property, opacity }) => {
          const tintedBackground = blend(resolveRgb(variables, property), background, opacity);
          expectContrast(theme.id, `foreground on ${label} over ${backgroundProperty}`, text, tintedBackground, 4.5);
          expectContrast(theme.id, `muted text on ${label} over ${backgroundProperty}`, muted, tintedBackground, 4.5);
          expectContrast(theme.id, `focus indicator on ${label} over ${backgroundProperty}`, focus, tintedBackground, 3);
        });
      });

      compactBackgroundProperties.forEach((backgroundProperty) => {
        const background = resolveRgb(variables, backgroundProperty);
        const positiveTint = blend(resolveRgb(variables, "--theme-positive-soft"), background, 0.25);
        const negativeTint = blend(resolveRgb(variables, "--theme-negative-soft"), background, 0.25);
        expectContrast(theme.id, `positive text on 25% positive tint over ${backgroundProperty}`, positiveText, positiveTint, 4.5);
        expectContrast(theme.id, `negative text on 25% negative tint over ${backgroundProperty}`, negativeText, negativeTint, 4.5);
      });
    });
  });
});
