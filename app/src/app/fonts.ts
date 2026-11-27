// Self-hosted copies of the Google Fonts files (OFL) so production builds
// never fetch fonts.googleapis.com. One localFont() per subset, because
// next/font/local cannot give each file its own unicode-range; the subsets
// are chained in --font-source-serif / --font-dm-sans in globals.css, which
// gives per-character fallback across them. Only the last subset of each
// family carries the metric-adjusted fallback face (adjusted for the system
// font), so a loaded latin face is never pre-empted by the fallback.
// Files and ranges are the ones the Google Fonts loader produced before.

import localFont from "next/font/local";

export const sourceSerifLatin = localFont({
  src: "./fonts/SourceSerif4-latin.woff2",
  weight: "400 700",
  style: "normal",
  display: "swap",
  variable: "--font-source-serif-latin",
  preload: true,
  adjustFontFallback: false,
  declarations: [{ prop: "unicode-range", value: "U+0000-00FF,U+131,U+152-153,U+2BB-2BC,U+2C6,U+2DA,U+2DC,U+304,U+308,U+329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD" }],
});

export const sourceSerifLatinExt = localFont({
  src: "./fonts/SourceSerif4-latin-ext.woff2",
  weight: "400 700",
  style: "normal",
  display: "swap",
  variable: "--font-source-serif-latin-ext",
  preload: false,
  adjustFontFallback: false,
  declarations: [{ prop: "unicode-range", value: "U+100-2BA,U+2BD-2C5,U+2C7-2CC,U+2CE-2D7,U+2DD-2FF,U+304,U+308,U+329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C4,U+2113,U+2C60-2C7F,U+A720-A7FF" }],
});

export const sourceSerifVietnamese = localFont({
  src: "./fonts/SourceSerif4-vietnamese.woff2",
  weight: "400 700",
  style: "normal",
  display: "swap",
  variable: "--font-source-serif-vietnamese",
  preload: false,
  adjustFontFallback: false,
  declarations: [{ prop: "unicode-range", value: "U+102-103,U+110-111,U+128-129,U+168-169,U+1A0-1A1,U+1AF-1B0,U+300-301,U+303-304,U+308-309,U+323,U+329,U+1EA0-1EF9,U+20AB" }],
});

export const sourceSerifGreek = localFont({
  src: "./fonts/SourceSerif4-greek.woff2",
  weight: "400 700",
  style: "normal",
  display: "swap",
  variable: "--font-source-serif-greek",
  preload: false,
  adjustFontFallback: false,
  declarations: [{ prop: "unicode-range", value: "U+370-377,U+37A-37F,U+384-38A,U+38C,U+38E-3A1,U+3A3-3FF" }],
});

export const sourceSerifCyrillic = localFont({
  src: "./fonts/SourceSerif4-cyrillic.woff2",
  weight: "400 700",
  style: "normal",
  display: "swap",
  variable: "--font-source-serif-cyrillic",
  preload: false,
  adjustFontFallback: false,
  declarations: [{ prop: "unicode-range", value: "U+301,U+400-45F,U+490-491,U+4B0-4B1,U+2116" }],
});

export const sourceSerifCyrillicExt = localFont({
  src: "./fonts/SourceSerif4-cyrillic-ext.woff2",
  weight: "400 700",
  style: "normal",
  display: "swap",
  variable: "--font-source-serif-cyrillic-ext",
  preload: false,
  adjustFontFallback: "Times New Roman",
  declarations: [{ prop: "unicode-range", value: "U+460-52F,U+1C80-1C8A,U+20B4,U+2DE0-2DFF,U+A640-A69F,U+FE2E-FE2F" }],
});

export const dmSansLatin = localFont({
  src: "./fonts/DMSans-latin.woff2",
  weight: "400 700",
  style: "normal",
  display: "swap",
  variable: "--font-dm-sans-latin",
  preload: true,
  adjustFontFallback: false,
  declarations: [{ prop: "unicode-range", value: "U+0000-00FF,U+131,U+152-153,U+2BB-2BC,U+2C6,U+2DA,U+2DC,U+304,U+308,U+329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD" }],
});

export const dmSansLatinExt = localFont({
  src: "./fonts/DMSans-latin-ext.woff2",
  weight: "400 700",
  style: "normal",
  display: "swap",
  variable: "--font-dm-sans-latin-ext",
  preload: false,
  adjustFontFallback: "Arial",
  declarations: [{ prop: "unicode-range", value: "U+100-2BA,U+2BD-2C5,U+2C7-2CC,U+2CE-2D7,U+2DD-2FF,U+304,U+308,U+329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C4,U+2113,U+2C60-2C7F,U+A720-A7FF" }],
});
