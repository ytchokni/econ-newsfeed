/** @jest-environment node */
// Production builds must not depend on fonts.googleapis.com (it made CI flaky).
import { existsSync, readFileSync, readdirSync, statSync } from "fs";
import { join } from "path";

const SRC = join(__dirname, "..");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (name === "__tests__" || name === "node_modules") return [];
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.(tsx?|css)$/.test(name) ? [path] : [];
  });
}

it("never imports next/font/google", () => {
  const offenders = sourceFiles(SRC).filter((f) => readFileSync(f, "utf-8").includes("next/font/google"));
  expect(offenders).toEqual([]);
});

it("ships every font file it declares, with a unicode-range each", () => {
  const fontsTs = readFileSync(join(SRC, "app/fonts.ts"), "utf-8");
  const files = [...fontsTs.matchAll(/src: "\.\/(fonts\/[^"]+\.woff2)"/g)].map((m) => m[1]);
  expect(files.length).toBeGreaterThanOrEqual(8);
  for (const file of files) expect(existsSync(join(SRC, "app", file))).toBe(true);
  expect(fontsTs.match(/prop: "unicode-range"/g)).toHaveLength(files.length);
});

it("keeps the CSS variables the styles use", () => {
  const css = readFileSync(join(SRC, "app/globals.css"), "utf-8");
  expect(css).toMatch(/--font-source-serif: var\(--font-source-serif-latin\)/);
  expect(css).toMatch(/--font-dm-sans: var\(--font-dm-sans-latin\), var\(--font-dm-sans-latin-ext\)/);
});
