/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      boxShadow: {
        glow: "0 0 0 1px rgba(34,211,238,0.25), 0 0 20px -4px rgba(34,211,238,0.45)",
        "glow-sm": "0 0 0 1px rgba(34,211,238,0.2), 0 0 10px -3px rgba(34,211,238,0.4)",
      },
      backgroundImage: {
        grid: "linear-gradient(to right, rgba(148,163,184,0.06) 1px, transparent 1px), linear-gradient(to bottom, rgba(148,163,184,0.06) 1px, transparent 1px)",
      },
      backgroundSize: {
        grid: "32px 32px",
      },
      keyframes: {
        "pulse-glow": {
          "0%, 100%": { opacity: "1", boxShadow: "0 0 0 0 rgba(34,211,238,0.5)" },
          "50%": { opacity: "0.7", boxShadow: "0 0 0 6px rgba(34,211,238,0)" },
        },
      },
      animation: {
        "pulse-glow": "pulse-glow 1.8s ease-in-out infinite",
      },
    },
  },
  plugins: [],
}
