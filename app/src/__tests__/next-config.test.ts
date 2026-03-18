/**
 * Tests for next.config.mjs to prevent regressions that break `make dev`.
 *
 * These verify:
 * - API rewrite defaults to localhost (not Docker service name)
 * - CSP includes 'unsafe-eval' in development (required for webpack HMR)
 * - CSP omits 'unsafe-eval' in production
 */
import { readFileSync } from "fs";
import { join } from "path";

const configContent = readFileSync(
  join(__dirname, "../../next.config.mjs"),
  "utf-8"
);

describe("next.config.mjs", () => {
  describe("API rewrite destination", () => {
    it("defaults to http://localhost:8000, not a Docker service name", () => {
      // The fallback must be localhost for local dev to work
      expect(configContent).toContain('"http://localhost:8000"');
      expect(configContent).not.toContain('"http://api:8000"');
    });

    it("allows override via API_INTERNAL_URL env var", () => {
      expect(configContent).toContain("process.env.API_INTERNAL_URL");
    });
  });

  describe("Content-Security-Policy", () => {
    it("conditionally adds unsafe-eval for dev mode", () => {
      // Webpack HMR in dev mode requires 'unsafe-eval' in script-src
      expect(configContent).toContain("'unsafe-eval'");
      // Must be conditional on isDev, not unconditional
      expect(configContent).toMatch(/isDev\s*\?.*unsafe-eval/);
    });

    it("defines isDev based on NODE_ENV", () => {
      expect(configContent).toMatch(
        /const isDev\s*=\s*process\.env\.NODE_ENV\s*!==\s*"production"/
      );
    });
  });
});
