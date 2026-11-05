import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/setupTests.ts",
    // Booking tests walk a whole multi-step journey, which needs more than the 5-second default.
    testTimeout: 15000,
  },
});
