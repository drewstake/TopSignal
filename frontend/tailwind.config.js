/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        app: {
          bg: "rgb(var(--theme-bg) / <alpha-value>)",
          surface: "rgb(var(--theme-surface) / <alpha-value>)",
          raised: "rgb(var(--theme-surface-raised) / <alpha-value>)",
          border: "rgb(var(--theme-border) / <alpha-value>)",
          "border-strong": "rgb(var(--theme-border-strong) / <alpha-value>)",
          text: "rgb(var(--theme-text) / <alpha-value>)",
          "text-soft": "rgb(var(--theme-text-soft) / <alpha-value>)",
          muted: "rgb(var(--theme-muted) / <alpha-value>)",
          "muted-text": "rgb(var(--theme-muted-text) / <alpha-value>)",
          "chart-text": "rgb(var(--theme-chart-text) / <alpha-value>)",
          "muted-strong": "rgb(var(--theme-muted-strong) / <alpha-value>)",
          accent: "rgb(var(--theme-accent) / <alpha-value>)",
          "accent-text": "rgb(var(--theme-accent-text) / <alpha-value>)",
          "accent-contrast": "rgb(var(--theme-accent-contrast) / <alpha-value>)",
          secondary: "rgb(var(--theme-accent-secondary) / <alpha-value>)",
          highlight: "rgb(var(--theme-highlight) / <alpha-value>)",
          positive: "rgb(var(--theme-positive) / <alpha-value>)",
          "positive-soft": "rgb(var(--theme-positive-soft) / <alpha-value>)",
          "positive-text": "rgb(var(--theme-positive-text) / <alpha-value>)",
          negative: "rgb(var(--theme-negative) / <alpha-value>)",
          "negative-soft": "rgb(var(--theme-negative-soft) / <alpha-value>)",
          "negative-text": "rgb(var(--theme-negative-text) / <alpha-value>)",
          focus: "rgb(var(--theme-focus) / <alpha-value>)",
          warning: "rgb(var(--theme-warning) / <alpha-value>)",
        },
      },
      boxShadow: {
        panel: "0 10px 30px -18px rgb(var(--theme-bg) / 0.85)",
      },
    },
  },
  plugins: [],
}
