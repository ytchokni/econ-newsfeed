import {
  sourceSerifLatin,
  sourceSerifLatinExt,
  sourceSerifVietnamese,
  sourceSerifGreek,
  sourceSerifCyrillic,
  sourceSerifCyrillicExt,
  dmSansLatin,
  dmSansLatinExt,
} from "./fonts";

/** Class names that define every self-hosted font subset's CSS variable. */
export const fontVariables = [
  sourceSerifLatin.variable,
  sourceSerifLatinExt.variable,
  sourceSerifVietnamese.variable,
  sourceSerifGreek.variable,
  sourceSerifCyrillic.variable,
  sourceSerifCyrillicExt.variable,
  dmSansLatin.variable,
  dmSansLatinExt.variable,
].join(" ");
