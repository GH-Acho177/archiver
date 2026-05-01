/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // CSS-variable colors — defined as RGB channels so Tailwind opacity
        // modifiers (bg-accent/20, border-border/50, etc.) work correctly.
        bg:       "rgb(var(--color-bg)    / <alpha-value>)",
        panel:    "rgb(var(--color-panel) / <alpha-value>)",
        hover:    "rgb(var(--color-hover) / <alpha-value>)",
        border:   "rgb(var(--color-border)/ <alpha-value>)",
        text:     "rgb(var(--color-text)  / <alpha-value>)",
        dim:      "rgb(var(--color-dim)   / <alpha-value>)",
        accent:   "rgb(var(--color-accent)/ <alpha-value>)",
        "log-bg": "rgb(var(--color-log-bg)/ <alpha-value>)",
      },
      fontFamily: {
        sans: ["Segoe UI Variable Text", "Segoe UI", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
