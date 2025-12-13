import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  optimizeDeps: {
    include: ["@microsoft/signalr"],
  },
  server: {
    proxy: {
      "/topstep": {
        target: "https://api.topstepx.com",
        changeOrigin: true,
        secure: true,
        rewrite: (path) => path.replace(/^\/topstep/, ""),
      },
    },
  },
});
