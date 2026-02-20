/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      boxShadow: {
        panel: "0 10px 30px -18px rgba(15, 23, 42, 0.85)",
      },
    },
  },
  plugins: [],
}
