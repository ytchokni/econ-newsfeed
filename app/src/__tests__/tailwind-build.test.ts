import { execSync } from "child_process";
import path from "path";
import fs from "fs";
import os from "os";

/**
 * Verifies that Tailwind CSS actually processes utility classes.
 *
 * This catches version mismatches (e.g. Tailwind v4 with v3 config/syntax)
 * where the build succeeds but no CSS is generated for utility classes.
 */
describe("Tailwind CSS build", () => {
  it("generates CSS for utility classes used in the project", () => {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "tw-test-"));
    const outputFile = path.join(tmpDir, "output.css");
    const appDir = path.resolve(__dirname, "../..");
    const inputFile = path.join(appDir, "src/app/globals.css");

    try {
      execSync(
        `npx @tailwindcss/cli -i ${inputFile} -o ${outputFile}`,
        { cwd: appDir, timeout: 30000 }
      );

      const output = fs.readFileSync(outputFile, "utf-8");

      expect(output).toContain("sticky");
      expect(output).toContain("flex");
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });
});
