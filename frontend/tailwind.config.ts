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
        sail: {
          darkest: "#050914",
          dark: "#0a1128",
          card: "#101d3f",
          border: "#1c2e5e",
          accent: "#0077b6",
          cyan: "#00b4d8",
          gold: "#f4a261",
          goldDark: "#e76f51",
          success: "#2ec4b6",
          danger: "#e63946",
          warning: "#ffb703",
        },
      },
    },
  },
  plugins: [],
};

export default config;
