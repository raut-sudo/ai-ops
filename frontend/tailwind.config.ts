import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "#0d0d0d",
        surface: "#1a1a1a",
        "surface-2": "#242424",
        "surface-3": "#2e2e2e",
        border: "#2a2a2a",
        accent: "#2563eb",
        "accent-hover": "#1d4ed8",
        success: "#16a34a",
        warning: "#d97706",
        danger: "#dc2626",
        "text-primary": "#f5f5f5",
        "text-secondary": "#a0a0a0",
        "text-muted": "#6b6b6b",
      },
    },
  },
  plugins: [],
};

export default config;
