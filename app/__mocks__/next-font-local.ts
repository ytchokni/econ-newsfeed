// jest stand-in for next/font/local (only the Next.js compiler can load fonts).
export default function localFont(options: { variable?: string }) {
  return {
    className: "mock-font",
    variable: options?.variable ?? "--mock-font",
    style: { fontFamily: "mock" },
  };
}
